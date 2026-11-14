"""v1.12 features (the P0 wave from the Nov 13, 2026 deep-research
roadmap):

- P0-1: pre-action revalidation + identity heal. Chromium revalidates
  its target atomically at dispatch (same node moved = proceed with
  fresh geometry, no model call; node gone = a distinct stale error);
  the Runner's heal re-resolves under containment (every recorded
  identity signal must match, framed elements never heal, ambiguity is
  refused); secret-bearing and upload actions NEVER heal.
- P0-2: wait-for-quiet before perceiving and acting (bounded, noted
  on the trail when the cap expires, disableable).
- P0-3: line-level snapshot diffs for small page changes, with the
  v1.4 summary note as the large-change fallback; heal events carry
  the change note.
"""

from __future__ import annotations

import time

import pytest

from ghost_hands import StaleTargetError, UnhealableTargetError  # noqa: F401
from ghost_hands.actions import Action
from ghost_hands.deciders import ScriptedDecider
from ghost_hands.drivers import ChromiumDriver, FakeDriver
from ghost_hands.eyes import Element, ElementMap
from ghost_hands.errors import HandsError
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail


# ---------------------------------------------------------------------------
# Identity signals on the map (P0-1)
# ---------------------------------------------------------------------------

TWINS = """<!doctype html><html><head><title>Twins</title></head>
<body>
<button id="del-a" data-nav="https://twin.local/deleted-a">Delete</button>
<button id="del-b" data-nav="https://twin.local/deleted-b">Delete</button>
</body></html>"""

TWINS_SWAPPED = """<!doctype html><html><head><title>Twins</title></head>
<body>
<button id="del-b" data-nav="https://twin.local/deleted-b">Delete</button>
<button id="del-a" data-nav="https://twin.local/deleted-a">Delete</button>
</body></html>"""

TWIN_URL = "https://twin.local/list"


def test_identity_signals_recorded():
    m = ElementMap.from_html(TWINS, url=TWIN_URL)
    a, b = m.elements
    # nth tells the twins apart; text and id ride the descriptor.
    assert (a.nth, b.nth) == (1, 2)
    assert a.text == "Delete" and b.text == "Delete"
    desc = b.descriptor()
    assert desc["id"] == "del-b" and desc["nth"] == 2 and desc["text"] == "Delete"
    ident = b.identity()
    assert ident["id"] == "del-b" and ident["nth"] == 2


def test_containment_finds_the_moved_twin():
    before = ElementMap.from_html(TWINS, url=TWIN_URL)
    after = ElementMap.from_html(TWINS_SWAPPED, url=TWIN_URL)
    target = before.get(2)  # del-b, before the swap
    found = after.find_heal_candidate(target.descriptor())
    assert found is not None and found.number == 1 and found.id == "del-b"


def test_containment_rejects_a_different_identity():
    # The target (del-b) is gone; only its same-named twin remains.
    # The loose v0.2 lookup would happily return the twin — the whole
    # point of containment is that this must NOT heal.
    before = ElementMap.from_html(TWINS, url=TWIN_URL)
    only_a = ElementMap.from_html(
        TWINS.replace(
            '<button id="del-b" data-nav="https://twin.local/deleted-b">Delete</button>\n',
            "",
        ),
        url=TWIN_URL,
    )
    target = before.get(2)
    assert only_a.find_heal_candidate(target.descriptor()) is None
    # ...while the permissive lookup (plan-cache semantics) still finds
    # *a* Delete — the two lookups are deliberately different.
    assert only_a.find_by_descriptor(target.descriptor()) is not None


def test_containment_nth_tiebreak_and_ambiguity_refusal():
    no_ids_a = ElementMap.from_elements(
        [
            {"tag": "button", "role": "button", "name": "Delete"},
            {"tag": "button", "role": "button", "name": "Delete"},
        ],
        url=TWIN_URL,
    )
    desc_second = no_ids_a.get(2).descriptor()
    found = no_ids_a.find_heal_candidate(desc_second)
    assert found is not None and found.number == 2
    # A descriptor claiming an nth that no candidate holds is refused.
    desc_third = dict(desc_second, nth=3)
    assert no_ids_a.find_heal_candidate(desc_third) is None


def test_containment_framed_elements_never_heal():
    framed = ElementMap.from_elements(
        [{"tag": "button", "role": "button", "name": "Pay", "frame": "inner"}],
        url=TWIN_URL,
    )
    desc = framed.get(1).descriptor()
    assert desc["frame"] == "inner"
    assert framed.find_heal_candidate(desc) is None


# ---------------------------------------------------------------------------
# Runner-level containment + never-heal (FakeDriver)
# ---------------------------------------------------------------------------


TWIN_PAGES = {
    TWIN_URL: TWINS,
    "https://twin.local/deleted-a": "<html><body><p>a deleted</p></body></html>",
    "https://twin.local/deleted-b": "<html><body><p>b deleted</p></body></html>",
}


def test_runner_heals_to_the_right_twin():
    driver = FakeDriver(dict(TWIN_PAGES), start_url=TWIN_URL)
    driver.mutate_hook = lambda d: d.pages.__setitem__(TWIN_URL, TWINS_SWAPPED)
    trail = Trail()
    report = Runner(
        driver,
        ScriptedDecider(
            [{"kind": "click", "target": 2}, {"kind": "done", "summary": "deleted b"}]
        ),
        trail=trail,
        approver=lambda info: True,
    ).run("delete the second item")
    assert report.stop_reason == "done", report.summary
    # The click landed on del-b (now at [1]), not on its twin.
    assert driver.current_url() == "https://twin.local/deleted-b"
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1 and heals[0]["kind"] == "reresolve"
    assert heals[0]["found"] is True and heals[0]["to_target"] == 1


def test_runner_refuses_to_heal_onto_the_wrong_twin():
    # NEGATIVE CHECK (the sanity gate): the target is deleted from the
    # page and only the same-named twin remains. The run must fail —
    # never click the twin.
    gone_b = TWINS.replace(
        '<button id="del-b" data-nav="https://twin.local/deleted-b">Delete</button>\n',
        "",
    )
    driver = FakeDriver(dict(TWIN_PAGES), start_url=TWIN_URL)
    driver.mutate_hook = lambda d: d.pages.__setitem__(TWIN_URL, gone_b)
    trail = Trail()
    report = Runner(
        driver,
        ScriptedDecider(
            [{"kind": "click", "target": 2}, {"kind": "done", "summary": "deleted b"}]
        ),
        trail=trail,
        approver=lambda info: True,
        error_recovery_attempts=0,
    ).run("delete the second item")
    assert report.stop_reason == "error", report.summary
    assert driver.current_url() == TWIN_URL  # nothing was clicked
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1 and heals[0]["found"] is False


LOGIN_URL = "https://vault.local/login"
LOGIN_V1 = """<!doctype html><html><head><title>Login</title></head>
<body>
<input id="user" name="user" type="text" placeholder="Username">
<input id="pass" name="pass" type="password" placeholder="Password">
</body></html>"""
LOGIN_V2 = """<!doctype html><html><head><title>Login</title></head>
<body>
<input id="promo" name="promo" type="text" placeholder="Promo code">
<input id="user" name="user" type="text" placeholder="Username">
<input id="pass" name="pass" type="password" placeholder="Password">
</body></html>"""


def test_secret_action_never_heals():
    # The password field moves from [2] to [3] between perception and
    # the type. An ordinary action would heal; a secret-bearing one
    # must refuse — its approval was bound to the perceived element.
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
        secrets={"PASSWORD": "s3cret-value"},
        error_recovery_attempts=0,
    ).run("log in")
    assert report.stop_reason == "error", report.summary
    assert "unhealable" in report.summary
    # The secret never landed anywhere — least of all in the wrong field.
    assert "s3cret-value" not in driver.fields.values()
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1 and heals[0]["kind"] == "refused"
    assert heals[0]["found"] is False


UPLOAD_URL = "https://up.local/form"
UPLOAD_V1 = """<!doctype html><html><head><title>Upload</title></head>
<body>
<input id="file-a" name="file" type="file" placeholder="Upload">
</body></html>"""
UPLOAD_V2 = """<!doctype html><html><head><title>Upload</title></head>
<body>
<input id="file-b" name="file" type="file" placeholder="Upload">
</body></html>"""


def test_upload_action_never_heals(tmp_path):
    payload = tmp_path / "payload.txt"
    payload.write_text("hello")
    driver = FakeDriver({UPLOAD_URL: UPLOAD_V1}, start_url=UPLOAD_URL)
    driver.mutate_hook = lambda d: d.pages.__setitem__(UPLOAD_URL, UPLOAD_V2)
    trail = Trail()
    report = Runner(
        driver,
        ScriptedDecider(
            [
                {"kind": "set_file", "target": 1, "path": str(payload)},
                {"kind": "done", "summary": "uploaded"},
            ]
        ),
        trail=trail,
        approver=lambda info: True,
        error_recovery_attempts=0,
    ).run("upload the file")
    assert report.stop_reason == "error", report.summary
    assert "unhealable" in report.summary
    assert not any(
        str(v).startswith("file:") for v in driver.fields.values()
    )
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1 and heals[0]["kind"] == "refused"


HEAL_URL = "https://heal.local/form"
HEAL_V1 = """<!doctype html><html><head><title>Form</title></head>
<body>
<input name="q" type="text" placeholder="Your note">
<button>Save</button>
</body></html>"""
HEAL_V2 = """<!doctype html><html><head><title>Form</title></head>
<body>
<button>Dismiss banner</button>
<input name="q" type="text" placeholder="Your note">
<button>Save</button>
</body></html>"""


def test_heal_event_carries_change_note():
    # The classic banner-insert heal: the heal event must surface what
    # changed on the page (the banner), not just silent renumbering.
    driver = FakeDriver({HEAL_URL: HEAL_V1}, start_url=HEAL_URL)
    driver.mutate_hook = lambda d: d.pages.__setitem__(HEAL_URL, HEAL_V2)
    trail = Trail()
    report = Runner(
        driver,
        ScriptedDecider(
            [
                {"kind": "type", "target": 1, "text": "noted"},
                {"kind": "done", "summary": "typed"},
            ]
        ),
        trail=trail,
    ).run("heal with a note")
    assert report.stop_reason == "done", report.summary
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1
    assert "Dismiss banner" in (heals[0].get("change_note") or "")


# ---------------------------------------------------------------------------
# Chromium pre-action revalidation (stubbed CDP surface — no browser)
# ---------------------------------------------------------------------------


class _StubChromium(ChromiumDriver):
    """ChromiumDriver whose in-page surface is a scripted state dict,
    so revalidation can be tested without launching a browser."""

    def __init__(self, info):
        super().__init__()
        self.auto_wait = 0.3
        self._info = info
        self.mouse_events: list = []

    def _launch(self):  # never launch
        pass

    def _gh_eval(self, body):
        if "__ghRevalidate" in body:
            return dict(self._info)
        if "__ghCoords" in body:
            box = self._info["box"]
            return [
                {
                    "x": box["x"] + box["w"] / 2,
                    "y": box["y"] + box["h"] / 2,
                    "w": box["w"],
                    "h": box["h"],
                }
            ]
        return []

    def _eval(self, expression, session_id=None):
        return True

    def _mouse(self, *args, **kwargs):
        self.mouse_events.append(args)


def _save_button(box):
    return Element(
        number=1, tag="button", role="button", name="Save", box=box
    )


def test_revalidation_same_node_moved_proceeds():
    perceived = {"x": 100, "y": 100, "w": 80, "h": 30}
    moved = {"x": 100, "y": 260, "w": 80, "h": 30}
    driver = _StubChromium(
        {"status": "ok", "box": moved, "tag": "button", "name": "Save", "frame": None}
    )
    result = driver.act(Action.from_dict({"kind": "click", "target": 1}), _save_button(perceived))
    assert "clicked [1]" in result
    assert driver.mouse_events  # the click really dispatched
    note = driver.last_revalidation
    assert note is not None and note["moved"] is True
    assert note["from_box"] == perceived and note["to_box"] == moved


def test_revalidation_unmoved_is_silent():
    box = {"x": 100, "y": 100, "w": 80, "h": 30}
    driver = _StubChromium(
        {"status": "ok", "box": box, "tag": "button", "name": "Save", "frame": None}
    )
    driver.act(Action.from_dict({"kind": "click", "target": 1}), _save_button(box))
    assert driver.last_revalidation is not None
    assert driver.last_revalidation["moved"] is False


def test_revalidation_gone_node_is_a_distinct_stale_error():
    driver = _StubChromium({"status": "gone"})
    with pytest.raises(StaleTargetError, match="target missing"):
        driver.act(
            Action.from_dict({"kind": "click", "target": 1}),
            _save_button({"x": 0, "y": 0, "w": 10, "h": 10}),
        )


def test_revalidation_occluded_node_is_a_mismatch():
    box = {"x": 0, "y": 0, "w": 10, "h": 10}
    driver = _StubChromium(
        {"status": "occluded", "box": box, "tag": "button", "name": "Save", "frame": None}
    )
    with pytest.raises(HandsError, match="covered by another element"):
        driver.act(Action.from_dict({"kind": "click", "target": 1}), _save_button(box))


class _RevalidatingFake(FakeDriver):
    """A fake body whose click target moved inside the driver: the
    action succeeds, and the driver reports a same-node revalidation."""

    def act(self, action, element):
        result = super().act(action, element)
        if action.kind == "click":
            self.last_revalidation = {
                "moved": True,
                "from_box": {"x": 0, "y": 0, "w": 10, "h": 10},
                "to_box": {"x": 0, "y": 50, "w": 10, "h": 10},
            }
        return result


def test_runner_records_revalidation_without_a_model_call():
    from ghost_hands.drivers import DEMO_PAGES

    decider = ScriptedDecider(
        [{"kind": "click", "target": 2}, {"kind": "done", "summary": "searched"}]
    )
    trail = Trail()
    report = Runner(_RevalidatingFake(DEMO_PAGES), decider, trail=trail).run(
        "search the demo web"
    )
    assert report.stop_reason == "done", report.summary
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1 and heals[0]["kind"] == "revalidate"
    assert heals[0]["attempt"] == 0
    assert heals[0]["from_box"]["y"] == 0 and heals[0]["to_box"]["y"] == 50
    assert report.heals == 1
    # The decider spent exactly its two scripted steps: revalidation
    # cost no model call and no extra decide round-trip.
    assert decider._i == 2


# ---------------------------------------------------------------------------
# Wait-for-quiet (P0-2)
# ---------------------------------------------------------------------------


def test_quiet_returns_fast_on_a_settled_page():
    driver = ChromiumDriver()
    driver._eval = lambda expr, session_id=None: [0, 10_000]  # last mutation 10s ago
    start = time.time()
    assert driver.wait_for_quiet() is True
    assert time.time() - start < 0.4  # ~the poll interval, not the window
    assert driver.last_quiet_note is None


def test_quiet_proceeds_with_note_on_a_busy_page():
    driver = ChromiumDriver()
    driver.quiet_cap = 0.3
    # The mutation stamp is always "now": the page never settles.
    driver._eval = lambda expr, session_id=None: [10_000, 10_000]
    start = time.time()
    assert driver.wait_for_quiet() is False
    assert 0.25 < time.time() - start < 2.0  # capped, then proceeds
    assert "possibly mid-transition" in (driver.last_quiet_note or "")


def test_quiet_env_disable(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_QUIET", "0")
    driver = ChromiumDriver()
    driver._eval = lambda expr, session_id=None: (_ for _ in ()).throw(
        AssertionError("eval must not be called when quiet is disabled")
    )
    assert driver.wait_for_quiet() is True


class _BusyFake(FakeDriver):
    last_quiet_note = "possibly mid-transition: stub page never settles"

    def wait_for_quiet(self):
        return False


def test_runner_records_quiet_notes_and_proceeds():
    from ghost_hands.drivers import DEMO_PAGES

    trail = Trail()
    report = Runner(
        _BusyFake(DEMO_PAGES),
        ScriptedDecider(
            [{"kind": "click", "target": 2}, {"kind": "done", "summary": "searched"}]
        ),
        trail=trail,
    ).run("search the demo web")
    assert report.stop_reason == "done", report.summary
    perceives = [e for e in trail.events if e["type"] == "perceive"]
    assert perceives and all(
        "possibly mid-transition" in (e.get("quiet_note") or "") for e in perceives
    )
    results = [e for e in trail.events if e["type"] == "result" and e.get("ok")]
    assert results and "possibly mid-transition" in (results[0].get("quiet_note") or "")


def test_runner_quiet_knob_off_skips_the_wait():
    from ghost_hands.drivers import DEMO_PAGES

    calls = []

    class _CountingBusy(_BusyFake):
        def wait_for_quiet(self):
            calls.append(1)
            return False

    trail = Trail()
    report = Runner(
        _CountingBusy(DEMO_PAGES),
        ScriptedDecider([{"kind": "done", "summary": "idle"}]),
        trail=trail,
        quiet_wait=False,
    ).run("do nothing")
    assert report.stop_reason == "done"
    assert calls == []
    assert all("quiet_note" not in e for e in trail.events)


# ---------------------------------------------------------------------------
# Snapshot diffs (P0-3)
# ---------------------------------------------------------------------------


def test_map_diff_small_change_is_line_level():
    a = ElementMap.from_html(
        '<html><body><button>One</button><button>Two</button>'
        "<button>Three</button></body></html>",
        url="https://d.local/",
    )
    b = ElementMap.from_html(
        '<html><body><button>One</button><button>Dos</button>'
        "<button>Three</button></body></html>",
        url="https://d.local/",
    )
    diff = b.map_diff(a)
    assert diff is not None
    assert '- [2] <button> "Two"' in diff
    assert '+ [2] <button> "Dos"' in diff
    assert a.map_diff(a) is None
    assert a.map_diff(None) is None


def test_map_diff_large_change_falls_back():
    a = ElementMap.from_html(
        "<html><body><button>One</button></body></html>", url="https://d.local/"
    )
    many = "".join(f"<button>B{i}</button>" for i in range(15))
    b = ElementMap.from_html(
        f"<html><body>{many}</body></html>", url="https://d.local/"
    )
    assert b.map_diff(a) is None  # too big: summary note / full map instead
    assert b.diff_note(a) is not None  # the v1.4 summary still speaks


def test_runner_history_carries_the_map_diff():
    from ghost_hands.deciders import ScriptedDecider as SD

    class RecordingDecider:
        def __init__(self, steps):
            self._inner = SD(steps)
            self.histories = []

        def decide(self, goal, element_map, history):
            self.histories.append([dict(h) for h in history])
            return self._inner.decide(goal, element_map, history)

    decider = RecordingDecider(
        [
            {"kind": "click", "target": 2},
            {"kind": "done", "summary": "searched"},
        ]
    )
    from ghost_hands.drivers import DEMO_PAGES

    report = Runner(FakeDriver(DEMO_PAGES), decider, trail=Trail()).run(
        "search the demo web"
    )
    assert report.stop_reason == "done", report.summary
    notes = [
        h.get("note", "")
        for hist in decider.histories
        for h in hist
        if h.get("kind") == "system"
    ]
    assert any(
        n.startswith("Page change: navigated to") and "Map diff:" in n
        for n in notes
    ), notes
