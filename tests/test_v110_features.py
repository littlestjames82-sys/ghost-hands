"""v1.10 features: heal chains (a step may re-find its moved target
more than once) and error-to-loop recovery (a terminally failed step
spends a run-level chance and hands control back to the decider
instead of stopping the run)."""

from __future__ import annotations

from ghost_hands import HandsError
from ghost_hands.actions import Action
from ghost_hands.deciders import ScriptedDecider
from ghost_hands.drivers import FakeDriver
from ghost_hands.governor import Policy
from ghost_hands.report import summarize
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail, verify_trail

# ---------------------------------------------------------------------------
# Heal chains
# ---------------------------------------------------------------------------

CHAIN_URL = "https://chain.local/todo"
V1 = """<!doctype html><html><head><title>Chain Todo</title></head>
<body>
<input id="new-item" name="new-item" type="text" placeholder="New item">
<button data-append-to="items" data-from-field="new-item">Add item</button>
<ul id="items"><li>Existing item</li></ul>
</body></html>"""
V2 = """<!doctype html><html><head><title>Chain Todo</title></head>
<body>
<button>Dismiss banner</button>
<input id="new-item" name="new-item" type="text" placeholder="New item">
<button data-append-to="items" data-from-field="new-item">Add item</button>
<ul id="items"><li>Existing item</li></ul>
</body></html>"""
V3 = """<!doctype html><html><head><title>Chain Todo</title></head>
<body>
<button>Dismiss banner</button>
<button>Close notice</button>
<input id="new-item" name="new-item" type="text" placeholder="New item">
<button data-append-to="items" data-from-field="new-item">Add item</button>
<ul id="items"><li>Existing item</li></ul>
</body></html>"""


def _arm_two_moves(driver):
    """The page mutates after each of the first two snapshots: the
    input sits at [1], then [2], then settles at [3]. One heal is not
    enough — the Nov 12 eval's shifting-page failure, reproduced."""

    def second(d):
        d.pages[CHAIN_URL] = V3

    def first(d):
        d.pages[CHAIN_URL] = V2
        d.mutate_hook = second

    driver.mutate_hook = first


def test_heal_chain_succeeds_on_second_heal():
    driver = FakeDriver({CHAIN_URL: V1}, start_url=CHAIN_URL)
    _arm_two_moves(driver)
    trail = Trail()
    report = Runner(
        driver,
        ScriptedDecider(
            [
                {"kind": "type", "target": 1, "text": "twice moved"},
                {"kind": "done", "summary": "typed"},
            ]
        ),
        trail=trail,
    ).run("heal chain test")
    assert report.stop_reason == "done", report.summary
    assert driver.fields.get("new-item") == "twice moved"
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 2
    assert [h["attempt"] for h in heals] == [1, 2]
    assert (heals[0]["from_target"], heals[0]["to_target"]) == (1, 2)
    assert (heals[1]["from_target"], heals[1]["to_target"]) == (2, 3)
    assert all(h["found"] for h in heals)
    assert report.heals == 2
    assert report.to_dict()["heals"] == 2
    assert report.error_recoveries == 0
    assert verify_trail(trail.events)["ok"] is True


def test_heal_attempts_zero_disables_healing():
    driver = FakeDriver({CHAIN_URL: V1}, start_url=CHAIN_URL)
    driver.mutate_hook = lambda d: d.pages.__setitem__(CHAIN_URL, V2)
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
        heal_attempts=0,
        error_recovery_attempts=0,
    ).run("heal chain test")
    assert report.stop_reason == "error"
    assert "after heal" not in report.summary
    assert [e for e in trail.events if e["type"] == "heal"] == []
    assert report.heals == 0
    assert driver.fields.get("new-item") is None


def test_heal_attempts_one_matches_old_behavior():
    # Same two-move page, chain length pinned to the pre-v1.10 single
    # retry: the first heal lands, the retried action is already stale
    # again, and the step fails exactly as it used to.
    driver = FakeDriver({CHAIN_URL: V1}, start_url=CHAIN_URL)
    _arm_two_moves(driver)
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
        heal_attempts=1,
        error_recovery_attempts=0,
    ).run("heal chain test")
    assert report.stop_reason == "error"
    assert "after heal" in report.summary
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1
    assert heals[0]["attempt"] == 1 and heals[0]["to_target"] == 2
    assert report.heals == 1


# ---------------------------------------------------------------------------
# Error-to-loop recovery
# ---------------------------------------------------------------------------

REC_URL = "https://rec.local/"
REC_PAGE = """<!doctype html><html><head><title>Rec</title></head>
<body><button>Save changes</button></body></html>"""


class _ExplodingDriver(FakeDriver):
    """Raises a plain driver error on the first ``failures`` act()
    calls, then behaves. The error is not a target mismatch, so no
    heal applies — the step fails terminally, which is exactly the
    failure error recovery exists for."""

    def __init__(self, pages, start_url=None, failures=1):
        super().__init__(pages, start_url=start_url)
        self.failures_left = failures

    def act(self, action, element):
        if self.failures_left > 0:
            self.failures_left -= 1
            raise HandsError("widget exploded mid-click")
        return super().act(action, element)


class _NoteAwareDecider:
    """Clicks the button; once the error-recovery note is in its
    history it clicks again (the working approach) and then reports
    done. Without the note it would keep its first plan."""

    def __init__(self):
        self.recovery_notes: list[str] = []

    def decide(self, goal, element_map, history):
        notes = [
            str(h.get("note", ""))
            for h in history
            if h.get("kind") == "system"
            and "Error recovery" in str(h.get("note", ""))
        ]
        if notes and not self.recovery_notes:
            self.recovery_notes = notes
            return Action.from_dict({"kind": "click", "target": 1})
        if self.recovery_notes:
            return Action.from_dict(
                {"kind": "done", "summary": "recovered and finished"}
            )
        return Action.from_dict({"kind": "click", "target": 1})


def test_error_recovery_continues_and_completes():
    driver = _ExplodingDriver({REC_URL: REC_PAGE}, start_url=REC_URL)
    decider = _NoteAwareDecider()
    trail = Trail()
    report = Runner(driver, decider, trail=trail).run("recover please")
    assert report.stop_reason == "done", report.summary
    assert report.summary == "recovered and finished"
    # The decider was told what broke, in its own history.
    assert decider.recovery_notes, "decider never saw the recovery note"
    assert "widget exploded mid-click" in decider.recovery_notes[0]
    # The recovery is on the record, with the error text.
    rec = [e for e in trail.events if e["type"] == "error_recovery"]
    assert len(rec) == 1 and rec[0]["attempt"] == 1
    assert "widget exploded mid-click" in rec[0]["error"]
    # Counted on the report, in its dict form, and in summarize().
    assert report.error_recoveries == 1
    assert report.to_dict()["error_recoveries"] == 1
    assert summarize(trail.events)["error_recoveries"] == 1
    assert report.heals == 0
    assert verify_trail(trail.events)["ok"] is True


def test_error_recovery_disabled_keeps_error_stop():
    driver = _ExplodingDriver({REC_URL: REC_PAGE}, start_url=REC_URL)
    trail = Trail()
    report = Runner(
        driver,
        ScriptedDecider(
            [
                {"kind": "click", "target": 1},
                {"kind": "done", "summary": "unreached"},
            ]
        ),
        trail=trail,
        error_recovery_attempts=0,
    ).run("no recovery")
    assert report.stop_reason == "error"
    assert "action failed" in report.summary
    assert [e for e in trail.events if e["type"] == "error_recovery"] == []
    assert report.error_recoveries == 0


def test_error_recovery_budget_exhaustion_stops_with_error():
    # The driver never cooperates: one recovery is spent, the decider
    # tries again, fails again, and the run stops with error exactly
    # as it would have before v1.10.
    driver = _ExplodingDriver(
        {REC_URL: REC_PAGE}, start_url=REC_URL, failures=99
    )
    trail = Trail()
    report = Runner(
        driver,
        ScriptedDecider(
            [
                {"kind": "click", "target": 1},
                {"kind": "click", "target": 1},
                {"kind": "done", "summary": "unreached"},
            ]
        ),
        trail=trail,
        error_recovery_attempts=1,
    ).run("always failing")
    assert report.stop_reason == "error"
    assert "action failed" in report.summary
    rec = [e for e in trail.events if e["type"] == "error_recovery"]
    assert len(rec) == 1
    assert report.error_recoveries == 1


def test_error_recovery_never_applies_to_governance_denial():
    page = """<!doctype html><html><head><title>Acct</title></head>
<body><button>Delete account</button></body></html>"""
    driver = FakeDriver({REC_URL: page}, start_url=REC_URL)
    trail = Trail()
    report = Runner(
        driver,
        ScriptedDecider(
            [
                {"kind": "click", "target": 1},
                {"kind": "done", "summary": "unreached"},
            ]
        ),
        policy=Policy(consequential="deny"),
        trail=trail,
    ).run("denied stays denied")
    assert report.stop_reason == "denied"
    assert [e for e in trail.events if e["type"] == "error_recovery"] == []
    assert [e for e in trail.events if e["type"] == "execute"] == []
    assert report.error_recoveries == 0
