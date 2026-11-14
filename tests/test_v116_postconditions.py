"""v1.16 features (per-action postconditions, roadmap P1-8):

After an action executes, the effect it exists to produce is
checked IN CODE against a fresh perception — a typed field must
hold what was typed, a link click must land on its href, any
other click must change *something* observable. A click after
which nothing changed is the silent no-op Skyvern's cart
example made famous, and it is now caught at the step it
happens: a ``postcondition`` trail event, a specific note in
the decider's history, and a one-shot blind-retry refusal —
the decider's very next action may not repeat the failed
(kind, target) signature exactly (Stagehand's rule: never
retry act(), retry observe()). Postconditions never call a
model, never retry anything themselves, and never touch
approvals. They are opt-in (Runner(postconditions=True) or
GHOST_HANDS_POSTCONDITIONS=1), like the require-verify gate
and the validator before them: off, nothing changes.
"""

from __future__ import annotations

import json

from ghost_hands.actions import Action
from ghost_hands.drivers import FakeDriver
from ghost_hands.postconditions import postconditions_enabled
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail, verify_trail

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TODO_URL = "https://todo.local/list"
TODO_PAGE = """<!doctype html><html><head><title>Todo</title></head>
<body>
<input id="new-item" name="new-item" type="text" placeholder="New item">
<button data-append-to="items" data-from-field="new-item">Add item</button>
<ul id="items"><li>Existing item</li></ul>
</body></html>"""

PLAIN_URL = "https://plain.local/"
PLAIN_PAGE = """<!doctype html><html><head><title>Plain</title></head>
<body>
<h1>Plain page</h1>
<button>Do nothing</button>
<input id="note" name="note" type="text" placeholder="Note">
</body></html>"""

LINKS_URL = "https://links.local/"
LINKS_PAGE = """<!doctype html><html><head><title>Links</title></head>
<body>
<a href="https://links.local/target">Working link</a>
<a href="https://links.local/never">Dead link</a>
</body></html>"""
TARGET_PAGE = """<!doctype html><html><head><title>Target</title></head>
<body><h1>You made it</h1></body></html>"""

CARD_URL = "https://pay.local/checkout"
CARD_PAGE = """<!doctype html><html><head><title>Pay</title></head>
<body>
<label>Card number <input id="card" name="card_number" type="text"
autocomplete="cc-number"></label>
</body></html>"""
CARD = "4242 4242 4242 4242"  # the standard Luhn-valid test card

CHECK_URL = "https://prefs.local/"
CHECK_PAGE = """<!doctype html><html><head><title>Prefs</title></head>
<body>
<label><input id="news" name="news" type="checkbox"> Newsletter</label>
</body></html>"""


class MaxLengthDriver(FakeDriver):
    """A field with maxlength=5, honestly enforced at the store:
    what the page keeps is NOT what was typed past five chars —
    the typed-value mismatch a postcondition exists to catch."""

    def _store_field(self, element, value):
        super()._store_field(element, value[:5])


class CountingDriver(FakeDriver):
    """Counts the actions that actually reach the driver, per
    (kind, target) — a refused retry must never arrive here."""

    def __init__(self, pages, start_url=None):
        super().__init__(pages, start_url=start_url)
        self.executed: list[tuple] = []

    def act(self, action, element):
        self.executed.append((action.kind, action.target))
        return super().act(action, element)


class DeadLinkDriver(FakeDriver):
    """The 'Dead link' anchor's click is swallowed by the page
    (a scripted handler that goes nowhere): the driver reports
    the click, the browser stays exactly where it was."""

    def act(self, action, element):
        if (
            action.kind == "click"
            and element is not None
            and element.name == "Dead link"
        ):
            return f'clicked [{element.number}] "Dead link" (no effect)'
        return super().act(action, element)


class TogglingDriver(FakeDriver):
    """A body whose checkbox clicks really toggle: the new state
    lands in the field store, and the perceive overlay renders it
    as the control's live checked state."""

    def act(self, action, element):
        if (
            action.kind == "click"
            and element is not None
            and (element.type or "").lower() == "checkbox"
        ):
            key = element.id or element.attr_name or str(element.number)
            now = self.fields.get(key, "").lower() in ("true", "on", "1")
            self.fields[key] = "false" if now else "true"
            return f"clicked [{element.number}] \"{element.name}\""
        return super().act(action, element)


class RecordingDecider:
    """Scripted steps, but keeps every history it is shown — the
    failure note must be visible in the NEXT decide call."""

    def __init__(self, steps):
        self._steps = [Action.from_dict(s) for s in steps]
        self._i = 0
        self.histories: list[list[dict]] = []

    def decide(self, goal, element_map, history):
        self.histories.append([dict(h) for h in history])
        if self._i >= len(self._steps):
            return Action(kind="done", summary="script complete")
        action = self._steps[self._i]
        self._i += 1
        return action


def _events(trail, type_):
    return [e for e in trail.events if e["type"] == type_]


def _run(driver, steps, goal="postcondition test", **runner_kwargs):
    # The wave is opt-in; these tests exercise it switched on
    # unless a test says otherwise (the kill-switch tests pass
    # postconditions=False / no flag explicitly).
    runner_kwargs.setdefault("postconditions", True)
    trail = Trail()
    decider = RecordingDecider(steps)
    report = Runner(driver, decider, trail=trail, **runner_kwargs).run(goal)
    return trail, report, decider


# ---------------------------------------------------------------------------
# Value postconditions (type / select / fill)
# ---------------------------------------------------------------------------


def test_typed_value_match_passes():
    driver = FakeDriver({TODO_URL: TODO_PAGE}, start_url=TODO_URL)
    trail, report, _ = _run(
        driver,
        [
            {"kind": "type", "target": 1, "text": "buy milk"},
            {"kind": "done", "summary": "typed"},
        ],
    )
    assert report.stop_reason == "done"
    checks = _events(trail, "postcondition")
    assert len(checks) == 1
    assert checks[0]["expect"] == "value"
    assert checks[0]["ok"] is True
    assert checks[0]["action"] == {"kind": "type", "target": 1}
    assert report.postconditions == 1
    assert report.postcondition_failures == 0
    assert report.to_dict()["postconditions"] == 1
    assert report.to_dict()["postcondition_failures"] == 0
    assert verify_trail(trail.events)["ok"] is True


def test_typed_value_mismatch_caught_and_told_immediately():
    driver = MaxLengthDriver({TODO_URL: TODO_PAGE}, start_url=TODO_URL)
    trail, report, decider = _run(
        driver,
        [
            {"kind": "type", "target": 1, "text": "a much longer note"},
            {"kind": "done", "summary": "typed"},
        ],
    )
    # The failure is feedback, not a stop: the run still completes.
    assert report.stop_reason == "done"
    checks = _events(trail, "postcondition")
    assert len(checks) == 1 and checks[0]["ok"] is False
    assert "a much longer note" in checks[0]["note"]
    assert report.postcondition_failures == 1
    # And the decider was TOLD at its next decision, specifically:
    notes = [
        h.get("note", "")
        for h in decider.histories[1]
        if h.get("kind") == "system"
    ]
    assert any(
        "Postcondition failed" in n and "type on [1]" in n for n in notes
    ), decider.histories[1]


def test_fill_form_values_checked_per_field():
    driver = FakeDriver({TODO_URL: TODO_PAGE}, start_url=TODO_URL)
    trail, report, _ = _run(
        driver,
        [
            {"kind": "fill_form", "fields": {"new-item": "from the form"}},
            {"kind": "done", "summary": "filled"},
        ],
    )
    assert report.stop_reason == "done"
    checks = _events(trail, "postcondition")
    assert len(checks) == 1
    assert checks[0]["expect"] == "fields" and checks[0]["ok"] is True


# ---------------------------------------------------------------------------
# The silent no-op + the blind-retry refusal
# ---------------------------------------------------------------------------


def test_noop_click_flagged_and_identical_retry_refused_once():
    driver = CountingDriver({PLAIN_URL: PLAIN_PAGE}, start_url=PLAIN_URL)
    trail, report, _ = _run(
        driver,
        [
            {"kind": "click", "target": 1},  # no-op: flagged
            {"kind": "click", "target": 1},  # blind repeat: refused
            {"kind": "click", "target": 1},  # after the refusal: runs
            {"kind": "done", "summary": "clicked around"},
        ],
    )
    assert report.stop_reason == "done"
    # The driver saw the click exactly twice — the refused middle
    # repetition never executed.
    assert driver.executed.count(("click", 1)) == 2
    checks = _events(trail, "postcondition")
    assert len(checks) == 2 and all(c["ok"] is False for c in checks)
    assert all(c["expect"] == "change" for c in checks)
    refusals = [
        e
        for e in _events(trail, "result")
        if e.get("ok") is False and "refused" in str(e.get("result", ""))
    ]
    assert len(refusals) == 1
    assert "postcondition" in refusals[0]["result"]
    assert report.postcondition_failures == 2
    assert verify_trail(trail.events)["ok"] is True


def test_different_action_after_failure_executes_normally():
    driver = CountingDriver({PLAIN_URL: PLAIN_PAGE}, start_url=PLAIN_URL)
    trail, report, _ = _run(
        driver,
        [
            {"kind": "click", "target": 1},  # no-op: flagged
            {"kind": "type", "target": 2, "text": "real work"},  # runs
            {"kind": "done", "summary": "recovered"},
        ],
    )
    assert report.stop_reason == "done"
    assert ("type", 2) in driver.executed
    assert driver.fields.get("note") == "real work"
    # The type's own postcondition passes; only the click failed.
    checks = _events(trail, "postcondition")
    assert [c["ok"] for c in checks] == [False, True]
    refusals = [
        e for e in _events(trail, "result") if "refused" in str(e.get("result", ""))
    ]
    assert refusals == []


def test_failure_note_names_the_missing_effect():
    driver = FakeDriver({PLAIN_URL: PLAIN_PAGE}, start_url=PLAIN_URL)
    trail, _, decider = _run(
        driver,
        [
            {"kind": "click", "target": 1},
            {"kind": "done", "summary": "ok"},
        ],
    )
    checks = _events(trail, "postcondition")
    assert len(checks) == 1 and checks[0]["ok"] is False
    assert "no observable change" in checks[0]["note"]
    assert "Do nothing" in checks[0]["note"]


# ---------------------------------------------------------------------------
# Link / navigate URL expectations
# ---------------------------------------------------------------------------


def test_link_click_lands_on_its_href():
    driver = FakeDriver(
        {LINKS_URL: LINKS_PAGE, "https://links.local/target": TARGET_PAGE},
        start_url=LINKS_URL,
    )
    trail, report, _ = _run(
        driver,
        [
            {"kind": "click", "target": 1},
            {"kind": "done", "summary": "arrived"},
        ],
    )
    assert report.stop_reason == "done"
    assert driver.current_url() == "https://links.local/target"
    checks = _events(trail, "postcondition")
    assert len(checks) == 1
    assert checks[0]["expect"] == "url" and checks[0]["ok"] is True


def test_dead_link_click_fails_its_url_expectation():
    driver = DeadLinkDriver(
        {LINKS_URL: LINKS_PAGE, "https://links.local/never": TARGET_PAGE},
        start_url=LINKS_URL,
    )
    trail, report, _ = _run(
        driver,
        [
            {"kind": "click", "target": 2},
            {"kind": "done", "summary": "gave up honestly"},
        ],
    )
    assert report.stop_reason == "done"
    assert driver.current_url() == LINKS_URL  # never moved
    checks = _events(trail, "postcondition")
    assert len(checks) == 1
    assert checks[0]["expect"] == "url" and checks[0]["ok"] is False
    assert "https://links.local/never" in checks[0]["note"]


def test_navigate_lands_on_target():
    driver = FakeDriver(
        {LINKS_URL: LINKS_PAGE, "https://links.local/target": TARGET_PAGE},
        start_url=LINKS_URL,
    )
    trail, report, _ = _run(
        driver,
        [
            {"kind": "navigate", "url": "https://links.local/target"},
            {"kind": "done", "summary": "arrived"},
        ],
    )
    assert report.stop_reason == "done"
    checks = _events(trail, "postcondition")
    assert len(checks) == 1
    assert checks[0]["expect"] == "url" and checks[0]["ok"] is True


# ---------------------------------------------------------------------------
# Checkbox state
# ---------------------------------------------------------------------------


def test_checkbox_toggle_passes_state_expectation():
    driver = TogglingDriver({CHECK_URL: CHECK_PAGE}, start_url=CHECK_URL)
    trail, report, _ = _run(
        driver,
        [
            {"kind": "click", "target": 1},
            {"kind": "done", "summary": "toggled"},
        ],
    )
    assert report.stop_reason == "done"
    checks = _events(trail, "postcondition")
    assert len(checks) == 1
    assert checks[0]["expect"] == "state" and checks[0]["ok"] is True


def test_checkbox_click_that_changes_nothing_fails_state():
    # The plain fake body does not toggle a checkbox on click —
    # the state postcondition truthfully reports that clicking
    # it had no effect there.
    driver = FakeDriver({CHECK_URL: CHECK_PAGE}, start_url=CHECK_URL)
    trail, report, _ = _run(
        driver,
        [
            {"kind": "click", "target": 1},
            {"kind": "done", "summary": "clicked"},
        ],
    )
    assert report.stop_reason == "done"
    checks = _events(trail, "postcondition")
    assert len(checks) == 1
    assert checks[0]["expect"] == "state" and checks[0]["ok"] is False


# ---------------------------------------------------------------------------
# Readonly actions carry no postcondition
# ---------------------------------------------------------------------------


def test_readonly_actions_produce_no_postcondition_events():
    driver = FakeDriver({TODO_URL: TODO_PAGE}, start_url=TODO_URL)
    trail, report, _ = _run(
        driver,
        [
            {"kind": "extract", "mode": "text"},
            {"kind": "wait", "seconds": 0},
            {"kind": "screenshot"},
            {"kind": "verify", "text": "Todo"},
            {"kind": "done", "summary": "observed only"},
        ],
    )
    assert report.stop_reason == "done", report.summary
    assert _events(trail, "postcondition") == []
    assert report.postconditions == 0


# ---------------------------------------------------------------------------
# Masked + secret fields: checked without leaking
# ---------------------------------------------------------------------------


def test_masked_card_field_checked_by_presence_and_length_only():
    driver = FakeDriver({CARD_URL: CARD_PAGE}, start_url=CARD_URL)
    trail, report, _ = _run(
        driver,
        [
            {"kind": "type", "target": 1, "text": CARD},
            {"kind": "done", "summary": "paid"},
        ],
    )
    assert report.stop_reason == "done"
    checks = _events(trail, "postcondition")
    assert len(checks) == 1 and checks[0]["ok"] is True
    assert f"masked value present ({len(CARD)} chars)" in checks[0]["note"]
    # The card digits appear in NO postcondition event — presence
    # and length are the whole disclosure.
    assert CARD not in json.dumps(checks)


def test_secret_placeholder_checked_by_presence_only_and_leaks_nowhere():
    secret = "sk-live-9f8e7d6c5b4a3210fedc"
    driver = FakeDriver({PLAIN_URL: PLAIN_PAGE}, start_url=PLAIN_URL)
    trail, report, decider = _run(
        driver,
        [
            {"kind": "type", "target": 2, "text": "{{secret:API_TOKEN}}"},
            {"kind": "done", "summary": "entered"},
        ],
        secrets={"API_TOKEN": secret},
    )
    assert report.stop_reason == "done"
    checks = _events(trail, "postcondition")
    assert len(checks) == 1
    assert checks[0]["expect"] == "value_present"
    assert checks[0]["ok"] is True
    # The resolved secret is in NO trail event at all, and in no
    # history the decider was ever shown.
    assert secret not in json.dumps(trail.events)
    assert secret not in json.dumps(decider.histories)
    assert verify_trail(trail.events)["ok"] is True


# ---------------------------------------------------------------------------
# Heal first, judge the net outcome
# ---------------------------------------------------------------------------


def test_heal_then_succeed_passes_its_postcondition():
    v1 = """<!doctype html><html><head><title>Shift</title></head>
<body>
<input id="new-item" name="new-item" type="text" placeholder="New item">
<button data-append-to="items" data-from-field="new-item">Add item</button>
<ul id="items"></ul>
</body></html>"""
    v2 = """<!doctype html><html><head><title>Shift</title></head>
<body>
<button>Dismiss banner</button>
<input id="new-item" name="new-item" type="text" placeholder="New item">
<button data-append-to="items" data-from-field="new-item">Add item</button>
<ul id="items"></ul>
</body></html>"""
    driver = FakeDriver({TODO_URL: v1}, start_url=TODO_URL)
    driver.mutate_hook = lambda d: d.pages.__setitem__(TODO_URL, v2)
    trail, report, _ = _run(
        driver,
        [
            {"kind": "type", "target": 1, "text": "healed and held"},
            {"kind": "done", "summary": "typed"},
        ],
    )
    assert report.stop_reason == "done", report.summary
    assert len(_events(trail, "heal")) == 1
    checks = _events(trail, "postcondition")
    assert len(checks) == 1 and checks[0]["ok"] is True


# ---------------------------------------------------------------------------
# Activation: opt-in, kill switch, env
# ---------------------------------------------------------------------------


def test_kill_switch_disables_checks_and_refusals():
    driver = CountingDriver({PLAIN_URL: PLAIN_PAGE}, start_url=PLAIN_URL)
    trail, report, _ = _run(
        driver,
        [
            {"kind": "click", "target": 1},
            {"kind": "click", "target": 1},  # would be refused if on
            {"kind": "done", "summary": "clicked twice"},
        ],
        postconditions=False,
    )
    assert report.stop_reason == "done"
    assert driver.executed.count(("click", 1)) == 2
    assert _events(trail, "postcondition") == []


def test_activation_surfaces(monkeypatch):
    # Default: off (opt-in, like require_verify and validate).
    assert postconditions_enabled(None) is False
    assert postconditions_enabled(True) is True
    assert postconditions_enabled(False) is False
    # The env enables; an explicit argument beats the env both ways.
    monkeypatch.setenv("GHOST_HANDS_POSTCONDITIONS", "1")
    assert postconditions_enabled(None) is True
    assert postconditions_enabled(False) is False
    monkeypatch.setenv("GHOST_HANDS_POSTCONDITIONS", "0")
    assert postconditions_enabled(None) is False
    assert postconditions_enabled(True) is True
    # A default-constructed Runner (no flag) with the env on
    # produces postcondition events; with it off, none.
    monkeypatch.setenv("GHOST_HANDS_POSTCONDITIONS", "1")
    trail = Trail()
    report = Runner(
        FakeDriver({TODO_URL: TODO_PAGE}, start_url=TODO_URL),
        RecordingDecider(
            [
                {"kind": "type", "target": 1, "text": "via env"},
                {"kind": "done", "summary": "typed"},
            ]
        ),
        trail=trail,
    ).run("env activation")
    assert report.stop_reason == "done"
    assert len(_events(trail, "postcondition")) == 1
