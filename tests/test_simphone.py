"""SimPhoneDriver (v0.4): the simulated phone body, driven by the
unchanged Runner / Governor / ScriptedDecider."""

import pytest

from ghost_hands import actions as A
from ghost_hands.deciders import ScriptedDecider
from ghost_hands.eyes import ElementMap
from ghost_hands.governor import Governor, classify
from ghost_hands.runner import Runner
from ghost_hands.simphone import SimPhoneDriver, render_text_png
from ghost_hands.trail import Trail


def _run(driver, steps, approver=None, policy=None):
    trail = Trail()
    runner = Runner(
        driver, ScriptedDecider(steps), approver=approver, policy=policy, trail=trail
    )
    return runner.run("test goal"), trail


NOTE_FLOW = [
    {"kind": "click", "target": 1},  # Notes app icon
    {"kind": "click", "target": 1},  # New note
    {"kind": "type", "target": 1, "text": "hello ghost"},
    {"kind": "click", "target": 2},  # Save
    {"kind": "press", "key": "Back"},  # back home
]


def test_notes_flow_end_to_end():
    driver = SimPhoneDriver()
    report, _trail = _run(driver, NOTE_FLOW)
    assert report.stop_reason == "done"
    assert driver.notes == ["hello ghost"]
    assert driver.current_url() == "phone://home"


def test_home_screen_map_has_phone_roles():
    driver = SimPhoneDriver()
    m = driver.perceive()
    assert [el.number for el in m.elements] == [1, 2]
    assert [el.role for el in m.elements] == ["app_icon", "app_icon"]
    assert [el.name for el in m.elements] == ["Notes", "Settings"]


def test_settings_toggles_flip():
    driver = SimPhoneDriver()
    driver.open("phone://settings")
    m = driver.perceive()
    wifi = m.find_by_text("Wi-Fi")
    assert wifi.role == "toggle" and wifi.value == "on"
    result = driver.act(A.click(wifi.number), wifi)
    assert "off" in result
    assert driver.toggles["Wi-Fi"] is False
    assert driver.perceive().find_by_text("Wi-Fi").value == "off"


def test_saved_note_can_be_reopened_and_edited():
    driver = SimPhoneDriver()
    _run(driver, NOTE_FLOW)
    driver.open("phone://notes")
    m = driver.perceive()
    note_el = m.find_by_text("hello ghost")
    driver.act(A.click(note_el.number), note_el)
    editor = driver.perceive()
    field = editor.find_by_text("Note text")
    assert field.value == "hello ghost"
    driver.act(A.type(field.number, "hello again"), field)
    save = editor.find_by_text("Save")
    driver.act(A.click(save.number), save)
    assert driver.notes == ["hello again"]


def test_delete_all_is_consequential_and_gated():
    driver = SimPhoneDriver()
    _run(driver, NOTE_FLOW)
    driver.open("phone://notes")
    m = driver.perceive()
    delete_el = m.find_by_text("Delete all notes")
    assert classify(A.click(delete_el.number), delete_el, driver.current_url()) == (
        "consequential"
    )
    # Through the Runner with no approver: denied, notes intact.
    driver2 = SimPhoneDriver()
    _run(driver2, NOTE_FLOW)
    driver2.open("phone://notes")
    m2 = driver2.perceive()
    report, trail = _run(
        driver2, [{"kind": "click", "target": m2.find_by_text("Delete all").number}]
    )
    assert report.stop_reason == "denied"
    assert driver2.notes == ["hello ghost"]
    # With an approver: it runs and the notes are gone.
    report3, _ = _run(
        driver2,
        [{"kind": "click", "target": m2.find_by_text("Delete all").number}],
        approver=lambda a: True,
    )
    assert report3.stop_reason == "done"
    assert driver2.notes == []


def test_extract_reads_the_screen():
    driver = SimPhoneDriver()
    _run(driver, NOTE_FLOW)
    driver.open("phone://notes")
    assert driver.act(A.extract(), None) == "hello ghost"
    driver.open("phone://settings")
    assert "Wi-Fi: on" in driver.act(A.extract(), None)


def test_back_at_home_is_an_honest_noop():
    driver = SimPhoneDriver()
    assert "nowhere to go" in driver.act(A.press("Back"), None)
    assert driver.current_url() == "phone://home"


def test_screenshot_is_a_real_png(tmp_path):
    driver = SimPhoneDriver()
    out = tmp_path / "phone.png"
    result = driver.act(A.screenshot(str(out)), None)
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 200
    assert "PNG" in result


def test_png_renderer_produces_valid_png():
    data = render_text_png(["SIM PHONE - HOME", "Notes", "Settings"])
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    # IHDR width/height are big-endian at fixed offsets.
    import struct

    width, height = struct.unpack(">II", data[16:24])
    assert width > 50 and height > 30


def test_snapshot_html_sees_the_same_controls():
    driver = SimPhoneDriver()
    m = ElementMap.from_html(driver.snapshot_html(), url=driver.current_url())
    assert [el.name for el in m.elements] == ["Notes", "Settings"]


def test_unsupported_actions_fail_honestly():
    driver = SimPhoneDriver()
    with pytest.raises(Exception) as excinfo:
        driver.act(A.drag(1, 2), driver.perceive().get(1))
    assert "cannot perform" in str(excinfo.value)


def test_capability_flags_are_declared():
    caps = SimPhoneDriver.capabilities
    assert caps["perceive"] is True and caps["screenshot"] is True
    assert caps["tabs"] is False and caps["session"] is False


def test_governor_classes_on_phone_actions():
    driver = SimPhoneDriver()
    m = driver.perceive()
    notes_icon = m.get(1)
    gov = Governor()
    assert gov.classify(A.click(notes_icon.number), notes_icon) == "write"
    assert gov.classify(A.press("Back")) == "readonly"
    assert gov.classify(A.extract()) == "readonly"
