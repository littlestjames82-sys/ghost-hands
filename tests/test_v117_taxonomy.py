"""v1.17 features (failure taxonomy, roadmap P3-13):

Every run ends with a final ``status`` (SUCCESS / NO_TARGET /
BLOCKED / TIMEOUT / ERROR) and at most one ``failure_label``
(STALE / MOVED / VANISHED / OCCLUDED / BLOCKED / COST_CAP /
NO_TARGET / WRONG_ANSWER), derived deterministically at stop
time from the run's own recorded signals — see taxonomy.py for
the mapping and its precedence rules. The verdict rides the
trail on the ``stop`` event's payload (no new event type, so
the v1.15 activation pin on the default event-type sequence
holds verbatim) and lands on the RunReport and its dict form.
Pure instrumentation: nothing about any run changes.
"""

from __future__ import annotations

from ghost_hands.deciders import ScriptedDecider
from ghost_hands.drivers import FakeDriver
from ghost_hands.errors import HandsError
from ghost_hands.runner import Runner
from ghost_hands.taxonomy import classify_run
from ghost_hands.trail import Trail, verify_trail

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

FORM_URL = "https://tax.local/form"
FORM_PAGE = """<!doctype html><html><head><title>Form</title></head><body>
<form>
<label>Full name <input id="name" name="name" type="text"></label>
<button type="submit">Submit</button>
</form>
</body></html>"""

TODO_URL = "https://tax.local/todo"
TODO_V1 = """<!doctype html><html><head><title>Todo</title></head>
<body>
<input id="new-item" name="new-item" type="text" placeholder="New item">
<button data-append-to="items" data-from-field="new-item">Add item</button>
<ul id="items"><li>Existing item</li></ul>
</body></html>"""
GONE_PAGE = """<!doctype html><html><head><title>Gone</title></head>
<body><p>Nothing here.</p></body></html>"""

EMPTY_URL = "https://tax.local/empty"
EMPTY_PAGE = """<!doctype html><html><head><title>Empty</title></head>
<body><p>Just words, no controls at all.</p></body></html>"""

LOGIN_URL = "https://tax.local/login"
LOGIN_V1 = (
    "<!doctype html><html><head><title>Login</title></head><body>"
    '<input id="user" name="user" type="text" placeholder="Username">'
    '<input id="pass" name="pass" type="password" placeholder="Password">'
    "</body></html>"
)
LOGIN_V2 = (
    "<!doctype html><html><head><title>Login</title></head><body>"
    '<input id="promo" name="promo" type="text" placeholder="Promo code">'
    '<input id="user" name="user" type="text" placeholder="Username">'
    '<input id="pass" name="pass" type="password" placeholder="Password">'
    "</body></html>"
)


def _stop_event(trail):
    stops = [e for e in trail.events if e["type"] == "stop"]
    assert len(stops) == 1
    return stops[0]


def _assert_classification(report, trail, status, label):
    assert report.status == status, report.summary
    assert report.failure_label == label, report.summary
    assert report.final_status == status
    as_dict = report.to_dict()
    assert as_dict["status"] == status and as_dict["failure_label"] == label
    stop = _stop_event(trail)
    assert stop["status"] == status and stop["failure_label"] == label
    assert verify_trail(trail.events)["ok"]


# --------------------------------------------------------------------------
# One scripted run per reachable (status, label) pair
# --------------------------------------------------------------------------


def test_success_no_label_and_the_v115_type_pin_holds():
    trail = Trail()
    report = Runner(
        FakeDriver({FORM_URL: FORM_PAGE}, start_url=FORM_URL),
        ScriptedDecider(
            [
                {"kind": "click", "target": 2},
                {"kind": "done", "summary": "submitted"},
            ]
        ),
        approver=lambda a: True,
        trail=trail,
    ).run("submit the form")
    _assert_classification(report, trail, "SUCCESS", None)
    # The classification added FIELDS to the stop event, not a new
    # event type: the default trail's type sequence is exactly the
    # one the v1.15 activation test pins.
    types = [e["type"] for e in trail.events]
    assert types == [
        "perceive", "decide", "govern", "execute", "result",
        "perceive", "decide", "stop",
    ]


def test_token_budget_stop_is_timeout_cost_cap():
    class SpendingDecider(ScriptedDecider):
        def __init__(self, steps):
            super().__init__(steps)
            self._spent = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

        @property
        def usage(self):
            return self._spent

        def decide(self, goal, element_map, history):
            self._spent["calls"] += 1
            self._spent["prompt_tokens"] += 300
            return super().decide(goal, element_map, history)

    trail = Trail()
    report = Runner(
        FakeDriver({FORM_URL: FORM_PAGE}, start_url=FORM_URL),
        SpendingDecider([{"kind": "scroll"}, {"kind": "scroll"}]),
        trail=trail,
        max_llm_tokens=200,
    ).run("spend")
    assert report.stop_reason == "budget"
    assert report.summary.startswith("LLM token budget reached")
    _assert_classification(report, trail, "TIMEOUT", "COST_CAP")


def test_step_budget_stop_is_timeout_without_a_signal_label():
    trail = Trail()
    report = Runner(
        FakeDriver({FORM_URL: FORM_PAGE}, start_url=FORM_URL),
        ScriptedDecider([{"kind": "scroll"}] * 5),
        trail=trail,
        step_budget=2,
    ).run("scroll forever")
    assert report.stop_reason == "budget"
    assert report.summary == "step budget exhausted"
    _assert_classification(report, trail, "TIMEOUT", None)


def test_step_budget_label_comes_from_the_last_signal():
    # The target vanishes mid-run; error recovery hands the run
    # back, the decider scrolls out the remaining budget. Status
    # is the budget's (TIMEOUT); the label is the proximate
    # recorded failure: the failed re-resolution (VANISHED).
    driver = FakeDriver({TODO_URL: TODO_V1}, start_url=TODO_URL)
    driver.mutate_hook = lambda d: d.pages.__setitem__(TODO_URL, GONE_PAGE)
    trail = Trail()
    report = Runner(
        driver,
        ScriptedDecider(
            [{"kind": "type", "target": 1, "text": "x"}]
            + [{"kind": "scroll"}] * 3
        ),
        trail=trail,
        step_budget=3,
    ).run("type then scroll")
    assert report.stop_reason == "budget"
    _assert_classification(report, trail, "TIMEOUT", "VANISHED")


def test_approver_denial_is_blocked():
    trail = Trail()
    report = Runner(
        FakeDriver({FORM_URL: FORM_PAGE}, start_url=FORM_URL),
        ScriptedDecider([{"kind": "click", "target": 2}]),
        approver=lambda a: False,
        trail=trail,
    ).run("submit the form")
    assert report.stop_reason == "denied"
    _assert_classification(report, trail, "BLOCKED", "BLOCKED")


def test_unverified_stop_is_wrong_answer():
    # The v1.15 premature-done trap (fixture mirrored from
    # test_v115_validator.py): submit paints a success banner but
    # files nothing without the name field. With a one-shot
    # validation cap, the naive done claim is judged unproven,
    # the run stops unverified, and the taxonomy names it a
    # wrong answer.
    class ApplicationDriver(FakeDriver):
        def __init__(self, pages, start_url=None):
            super().__init__(pages, start_url=start_url)
            self.saved = False

        def act(self, action, element):
            result = super().act(action, element)
            if (
                action.kind == "click"
                and element is not None
                and element.type == "submit"
            ):
                if any(str(v).strip() for v in self.fields.values()):
                    self.saved = True
                    self._overrides[self._url] = (
                        "<!doctype html><html><body>"
                        "<h1>Application filed</h1>"
                        "<p>Reference: CONF-2026</p></body></html>"
                    )
                else:
                    self._overrides[self._url] = (
                        "<!doctype html><html><body>"
                        "<p>Application received. Thank you!</p>"
                        "</body></html>"
                    )
            return result

    trail = Trail()
    report = Runner(
        ApplicationDriver({FORM_URL: FORM_PAGE}, start_url=FORM_URL),
        ScriptedDecider(
            [
                {"kind": "click", "target": 2},
                {"kind": "done", "summary": "submitted"},
            ]
        ),
        approver=lambda a: True,
        trail=trail,
        validate=True,
        validation_attempts=1,
    ).run("File the expense application and report confirmation reference CONF-2026")
    assert report.stop_reason == "unverified"
    _assert_classification(report, trail, "ERROR", "WRONG_ANSWER")


def test_failed_verify_is_wrong_answer():
    trail = Trail()
    report = Runner(
        FakeDriver({FORM_URL: FORM_PAGE}, start_url=FORM_URL),
        ScriptedDecider([{"kind": "verify", "text": "definitely absent words"}]),
        trail=trail,
    ).run("prove the impossible")
    assert report.stop_reason == "error"
    assert report.summary.startswith("verification failed:")
    _assert_classification(report, trail, "ERROR", "WRONG_ANSWER")


def test_heal_exhausted_gone_target_is_vanished():
    driver = FakeDriver({TODO_URL: TODO_V1}, start_url=TODO_URL)
    driver.mutate_hook = lambda d: d.pages.__setitem__(TODO_URL, GONE_PAGE)
    trail = Trail()
    report = Runner(
        driver,
        ScriptedDecider(
            [
                {"kind": "type", "target": 1, "text": "x"},
                {"kind": "done", "summary": "typed"},
            ]
        ),
        trail=trail,
        error_recovery_attempts=0,
    ).run("type into a vanishing field")
    assert report.stop_reason == "error"
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert heals and heals[-1]["found"] is False
    # The failure text is target-shaped ("target missing"), yet
    # the label is VANISHED, not STALE: the re-resolution SEARCHED
    # and found nothing — positive evidence of absence outranks
    # the generic stale reading.
    _assert_classification(report, trail, "ERROR", "VANISHED")


def test_never_heal_refusal_is_stale():
    driver = FakeDriver({LOGIN_URL: LOGIN_V1}, start_url=LOGIN_URL)
    driver.mutate_hook = lambda d: d.pages.__setitem__(LOGIN_URL, LOGIN_V2)
    trail = Trail()
    report = Runner(
        driver,
        ScriptedDecider(
            [
                {"kind": "type", "target": 2, "text": "{{secret:PASSWORD}}"},
                {"kind": "done", "summary": "logged in"},
            ]
        ),
        trail=trail,
        secrets={"PASSWORD": "taxonomy-secret"},
        error_recovery_attempts=0,
    ).run("log in")
    assert report.stop_reason == "error"
    assert "unhealable" in report.summary
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert heals and heals[-1]["kind"] == "refused"
    _assert_classification(report, trail, "ERROR", "STALE")


def test_covered_target_is_occluded():
    class CoveredDriver(FakeDriver):
        """Every click lands on an element another element covers —
        the Chromium revalidation's occlusion verdict, word for
        word, raised at dispatch on every attempt."""

        def act(self, action, element):
            if action.kind == "click" and element is not None:
                raise HandsError(
                    f"target mismatch: [{element.number}] "
                    f'<{element.tag}> "{element.name}" is covered by '
                    "another element at its center "
                    "(pre-action revalidation)"
                )
            return super().act(action, element)

    trail = Trail()
    report = Runner(
        CoveredDriver({FORM_URL: FORM_PAGE}, start_url=FORM_URL),
        ScriptedDecider([{"kind": "click", "target": 2}]),
        approver=lambda a: True,
        trail=trail,
        error_recovery_attempts=0,
    ).run("submit the form")
    assert report.stop_reason == "error"
    # The heal chain kept re-finding the (covered) button, so the
    # bare last-signal scan would call this MOVED — the terminal
    # failure's own occlusion verdict outranks it.
    _assert_classification(report, trail, "ERROR", "OCCLUDED")


def test_no_target_on_a_page_with_no_candidates():
    trail = Trail()
    report = Runner(
        FakeDriver({EMPTY_URL: EMPTY_PAGE}, start_url=EMPTY_URL),
        ScriptedDecider([{"kind": "click", "target": 1}]),
        approver=lambda a: True,
        trail=trail,
        error_recovery_attempts=0,
    ).run("click something, anything")
    assert report.stop_reason == "error"
    _assert_classification(report, trail, "NO_TARGET", "NO_TARGET")


def test_driver_error_without_a_signal_has_no_label():
    class ExplodingDriver(FakeDriver):
        def act(self, action, element):
            raise HandsError("boom: the page exploded")

    trail = Trail()
    report = Runner(
        ExplodingDriver({FORM_URL: FORM_PAGE}, start_url=FORM_URL),
        ScriptedDecider([{"kind": "click", "target": 2}]),
        approver=lambda a: True,
        trail=trail,
        error_recovery_attempts=0,
    ).run("submit the form")
    assert report.stop_reason == "error"
    _assert_classification(report, trail, "ERROR", None)


# --------------------------------------------------------------------------
# Classifier units: the branches a scripted run cannot reach cheaply
# --------------------------------------------------------------------------


def _perceive(elements, step=1):
    return {"type": "perceive", "step": step, "elements": elements}


def _heal(kind, found, step=1):
    return {"type": "heal", "step": step, "kind": kind, "found": found}


def test_classifier_moved_from_the_last_signal_scan():
    events = [_perceive(3), _heal("reresolve", True)]
    assert classify_run("stuck", "element map unchanged", events) == (
        "ERROR",
        "MOVED",
    )
    events = [_perceive(3), _heal("revalidate", True)]
    assert classify_run("stuck", "element map unchanged", events) == (
        "ERROR",
        "MOVED",
    )


def test_classifier_bare_postcondition_failure_names_no_class():
    events = [
        _perceive(3),
        _heal("reresolve", False),
        {
            "type": "postcondition",
            "step": 2,
            "ok": False,
            "note": "click on [2] produced no observable change",
        },
    ]
    # The dead-button failure is the closest signal but names no
    # class; the scan continues to the failed re-resolution.
    assert classify_run("stuck", "action repeated 3 times", events) == (
        "ERROR",
        "VANISHED",
    )
    events = [
        _perceive(3),
        {
            "type": "postcondition",
            "step": 1,
            "ok": False,
            "note": "click on [2] produced no observable change",
        },
    ]
    assert classify_run("stuck", "action repeated 3 times", events) == (
        "ERROR",
        None,
    )


def test_classifier_no_target_definition_edges():
    # A perception that DID offer candidates defeats NO_TARGET
    # even with zero successes: the run saw targets and lost them.
    events = [
        _perceive(2),
        {"type": "execute", "step": 1, "action": {"kind": "click", "target": 1}},
        {"type": "result", "step": 1, "ok": False, "result": "error: target missing"},
        _heal("reresolve", False),
    ]
    status, label = classify_run("error", "action failed: target missing", events)
    assert (status, label) == ("ERROR", "VANISHED")
    # A targeted success defeats it too.
    events = [
        _perceive(0),
        {"type": "execute", "step": 1, "action": {"kind": "click", "target": 1}},
        {"type": "result", "step": 1, "ok": True, "result": "clicked"},
    ]
    assert classify_run("stuck", "element map unchanged", events) != (
        "NO_TARGET",
        "NO_TARGET",
    )
    # And a decider that never attempted a targeted action on an
    # empty page is a plain error, not NO_TARGET (conservative).
    events = [_perceive(0)]
    assert classify_run("error", "decider failed: boom", events) == (
        "ERROR",
        None,
    )


def test_classifier_budget_disambiguation():
    assert classify_run(
        "budget", "LLM token budget reached: 300 tokens spent", []
    ) == ("TIMEOUT", "COST_CAP")
    assert classify_run("budget", "step budget exhausted", []) == (
        "TIMEOUT",
        None,
    )
    # The token cap outranks NO_TARGET even on an all-empty trail.
    events = [
        _perceive(0),
        {"type": "execute", "step": 1, "action": {"kind": "click", "target": 1}},
        {"type": "result", "step": 1, "ok": False, "result": "error"},
    ]
    assert classify_run(
        "budget", "LLM token budget reached: 300 tokens spent", events
    ) == ("TIMEOUT", "COST_CAP")


def test_classifier_fixed_points():
    # done / denied / unverified ignore every other signal.
    events = [_perceive(0), _heal("reresolve", False)]
    assert classify_run("done", "goal complete", events) == ("SUCCESS", None)
    assert classify_run("denied", "approver declined", events) == (
        "BLOCKED",
        "BLOCKED",
    )
    assert classify_run("unverified", "items never satisfied", events) == (
        "ERROR",
        "WRONG_ANSWER",
    )
    # Unknown/future reasons degrade to a bare ERROR, never a guess.
    assert classify_run("mystery", "?", events) == ("ERROR", None)
