"""Android body end to end (v0.5): the UNCHANGED Runner + Governor +
an intent-resolving decider drive AndroidDriver through the fake
MrGhosty bridge (tests/fake_android_bridge.py — the same HTTP/JSON
contract as the real Java bridge). The scenario mirrors
tests/test_conformance.py and SimPhoneDriver: open Notes, new note,
type "hello ghost", save, then attempt the consequential delete-all.

Asserted here, exactly as for the other bodies:
- perception is a gapless numbered map carrying body_refs;
- the consequential delete is DENIED with no approver (note survives)
  and EXECUTES with one;
- the trail records the protocol event shapes, govern events carry
  classification + outcome, and execute descriptors carry body_ref;
- a mid-run screen mutation makes the bridge answer 409 "target
  mismatch" and the Runner heals by descriptor and retries once.
"""

import pytest

from ghost_hands import actions as A
from ghost_hands.android_driver import AndroidDriver
from ghost_hands.deciders import Decider, ScriptedDecider
from ghost_hands.errors import HandsError
from ghost_hands.governor import Governor
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail

from fake_android_bridge import TOKEN, make_bridge


@pytest.fixture
def bridge():
    server, url, state = make_bridge()
    yield url, state
    server.shutdown()


SCENARIO = [
    ("click", "Notes", None),
    ("click", "New note", None),
    ("type", "Note text", "hello ghost"),
    ("click", "Save", None),
    ("click", "Delete all", None),
]


class IntentDecider(Decider):
    """Resolves each abstract step's target by element name from the
    live map at decision time (same pattern as test_conformance)."""

    def __init__(self, scenario, on_step=None):
        self._steps = list(scenario)
        self._i = 0
        self._on_step = on_step

    def decide(self, goal, element_map, history):
        if self._i >= len(self._steps):
            return A.done("scenario complete")
        verb, name, text = self._steps[self._i]
        step_no = self._i
        self._i += 1
        if self._on_step is not None:
            self._on_step(step_no)
        el = element_map.find_by_text(name)
        if el is None:
            raise HandsError(f"scenario target {name!r} not on this map")
        if verb == "click":
            return A.click(el.number)
        if verb == "type":
            return A.type(el.number, text or "")
        raise HandsError(f"unknown scenario verb {verb!r}")


def run_scenario(url, approver, decider=None):
    driver = AndroidDriver(bridge_url=url, token=TOKEN)
    trail = Trail()
    runner = Runner(
        driver,
        decider or IntentDecider(SCENARIO),
        governor=Governor(),
        approver=approver,
        trail=trail,
    )
    report = runner.run("android e2e: notes scenario")
    return driver, trail, report


def test_perception_gapless_with_body_refs(bridge):
    url, _state = bridge
    driver = AndroidDriver(bridge_url=url, token=TOKEN)
    m = driver.perceive()
    assert [el.number for el in m.elements] == list(range(1, len(m) + 1))
    assert all(el.body_ref for el in m.elements)


def test_android_notes_flow_denied_without_approver(bridge):
    url, state = bridge
    _driver, trail, report = run_scenario(url, approver=None)
    assert report.stop_reason == "denied", report.summary
    assert state.notes == ["hello ghost"], "the typed note must survive"
    govern = [ev for ev in trail.events if ev["type"] == "govern"]
    assert govern[-1]["classification"] == "consequential"
    assert govern[-1]["outcome"] == "deny"
    # Protocol event order + descriptor body_refs on the record.
    types = [ev["type"] for ev in trail.events]
    order = [
        types.index(t)
        for t in ("perceive", "decide", "govern", "execute", "result")
    ]
    assert order == sorted(order), f"trail event order broken: {types[:12]}"
    for ev in govern:
        assert "classification" in ev and "outcome" in ev
    executes = [ev for ev in trail.events if ev["type"] == "execute"]
    assert executes and all(
        ev["element"] and ev["element"].get("body_ref") for ev in executes
    )


def test_android_notes_flow_executes_with_approver(bridge):
    url, state = bridge
    driver, _trail, report = run_scenario(url, approver=lambda a: True)
    assert report.stop_reason == "done", report.summary
    assert state.notes == [], "the approved delete must actually happen"
    # Home again: Back out of the notes app, like the sim phone flow.
    driver.act(A.press("Back"), None)
    assert state.screen == "home"
    assert driver.current_url() == "android://com.android.launcher3"


def test_android_settings_toggle(bridge):
    url, state = bridge
    driver = AndroidDriver(bridge_url=url, token=TOKEN)
    trail = Trail()
    runner = Runner(
        driver,
        ScriptedDecider(
            [
                {"kind": "click", "target": 2},  # Settings icon
                {"kind": "click", "target": 2},  # Bluetooth toggle
            ]
        ),
        trail=trail,
    )
    report = runner.run("android e2e: toggle bluetooth")
    assert report.stop_reason == "done", report.summary
    assert state.toggles["Bluetooth"] is True


# -- gestures (v0.7), straight through AndroidDriver + the fake bridge ------


def _open_settings(driver):
    m = driver.perceive()
    settings = m.get(2)
    assert settings.name == "Settings"
    driver.act(A.click(settings.number), settings)
    return driver.perceive()


def _bounds_center(element):
    left, top, right, bottom = (
        int(part) for part in element.attrs["bounds"].split(",")
    )
    return (left + right) / 2, (top + bottom) / 2


def test_double_click_tap_counter_e2e(bridge):
    url, state = bridge
    driver = AndroidDriver(bridge_url=url, token=TOKEN)
    m = _open_settings(driver)
    counter = m.find_by_text("Tap counter")
    assert counter is not None and counter.value == "0"
    result = driver.act(A.double_click(counter.number), counter)
    assert "double-tapped" in result
    m = driver.perceive()
    assert m.find_by_text("Tap counter").value == "2"
    assert state.tap_count == 2
    gestures = [g for g in state.gestures if g["kind"] == "double_click"]
    assert gestures and gestures[-1]["from_ref"] == counter.body_ref
    assert gestures[-1]["to_ref"] is None
    payload = state.act_payloads[-1]
    assert payload["target_ref"] == counter.body_ref
    assert payload["expected"] == {
        "tag": counter.tag,
        "role": counter.role,
        "name": counter.name,
    }
    assert payload["action"]["kind"] == "double_click"


def test_drag_slider_to_wifi_sets_volume_50_e2e(bridge):
    url, state = bridge
    driver = AndroidDriver(bridge_url=url, token=TOKEN)
    m = _open_settings(driver)
    slider = m.find_by_text("Volume")
    wifi = m.find_by_text("Wi-Fi")
    assert slider is not None and wifi is not None
    assert slider.attrs["bounds"] == "270,380,810,500"
    dest_center_x, _ = _bounds_center(wifi)
    assert dest_center_x == 540  # round(100*(540-270)/540) == 50
    result = driver.act(A.drag(slider.number, wifi.number), slider)
    assert "50" in result
    assert state.volume == 50
    m = driver.perceive()
    assert m.find_by_text("Volume").value == "50"
    gestures = [g for g in state.gestures if g["kind"] == "drag"]
    assert gestures and gestures[-1]["from_ref"] == slider.body_ref
    assert gestures[-1]["to_ref"] == wifi.body_ref
    payload = state.act_payloads[-1]
    assert payload["target_ref"] == slider.body_ref
    assert payload["to_ref"] == wifi.body_ref
    assert payload["to_expected"] == {
        "tag": wifi.tag,
        "role": wifi.role,
        "name": wifi.name,
    }


def test_click_at_tap_counter_e2e(bridge):
    url, state = bridge
    driver = AndroidDriver(bridge_url=url, token=TOKEN)
    m = _open_settings(driver)
    counter = m.find_by_text("Tap counter")
    center_x, center_y = _bounds_center(counter)
    result = driver.act(A.click_at(center_x, center_y), None)
    assert "Tap counter" in result
    assert state.tap_count == 1
    m = driver.perceive()
    assert m.find_by_text("Tap counter").value == "1"
    gestures = [g for g in state.gestures if g["kind"] == "click_at"]
    assert gestures and gestures[-1]["from_ref"] == counter.body_ref
    assert gestures[-1]["x"] == center_x
    assert gestures[-1]["y"] == center_y
    payload = state.act_payloads[-1]
    assert "target_ref" not in payload
    assert "expected" not in payload
    assert payload["action"] == {
        "kind": "click_at",
        "x": center_x,
        "y": center_y,
    }


def test_click_at_hit_nothing_e2e(bridge):
    url, _state = bridge
    driver = AndroidDriver(bridge_url=url, token=TOKEN)
    driver.perceive()
    with pytest.raises(HandsError, match="hit nothing"):
        driver.act(A.click_at(2000, 2000), None)


def test_android_runner_heals_after_screen_mutation(bridge):
    url, state = bridge

    def mutate(step_no):
        # At the "New note" step (index 3, after Save), a banner
        # appears atop the notes list: every ref shifts by one, so the
        # click the decider is about to issue points at the wrong node
        # and the bridge answers 409 target mismatch.
        if step_no == 4:
            state.banner = True

    steps = SCENARIO[:4] + [("click", "New note", None)]
    _driver, trail, report = run_scenario(
        url,
        approver=lambda a: True,
        decider=IntentDecider(steps, on_step=mutate),
    )
    assert report.stop_reason == "done", report.summary
    heals = [ev for ev in trail.events if ev["type"] == "heal"]
    assert heals and heals[-1]["found"] is True, trail.events
    assert state.screen == "notes_edit", "the healed click opened the editor"
