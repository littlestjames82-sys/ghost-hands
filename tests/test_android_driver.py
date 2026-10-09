"""AndroidDriver unit tests (v0.5) — against the fake MrGhosty bridge
(tests/fake_android_bridge.py), which speaks the same HTTP/JSON
contract as the real Java bridge in MrGhosty v1.6+."""

import base64
import socket

import pytest

from ghost_hands import actions as A
from ghost_hands.android_driver import AndroidDriver
from ghost_hands.errors import HandsError
from ghost_hands.eyes import Element

from fake_android_bridge import TOKEN, TINY_PNG_B64, FakeAndroidState, make_bridge


@pytest.fixture
def bridge():
    server, url, state = make_bridge()
    yield url, state
    server.shutdown()


@pytest.fixture
def driver(bridge):
    url, _state = bridge
    return AndroidDriver(bridge_url=url, token=TOKEN)


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# -- construction / safety rails --------------------------------------------


def test_rejects_non_loopback_hosts():
    for url in ("http://192.168.1.20:8378", "http://example.com:8378",
                "http://10.0.0.5:8378"):
        with pytest.raises(HandsError, match="non-loopback"):
            AndroidDriver(bridge_url=url, token=TOKEN)


def test_allow_nonlocal_flag_constructs():
    driver = AndroidDriver(
        bridge_url="http://192.168.1.20:8378", token=TOKEN, allow_nonlocal=True
    )
    assert driver.bridge_url == "http://192.168.1.20:8378"


def test_accepts_loopback_spellings():
    for url in ("http://127.0.0.1:8378", "http://localhost:8378",
                "http://[::1]:8378"):
        AndroidDriver(bridge_url=url, token=TOKEN)


def test_invalid_url_rejected():
    with pytest.raises(HandsError, match="invalid Android bridge URL"):
        AndroidDriver(bridge_url="ftp://127.0.0.1:8378", token=TOKEN)


def test_token_from_env(monkeypatch, bridge):
    url, _state = bridge
    monkeypatch.setenv("GHOST_HANDS_ANDROID_TOKEN", TOKEN)
    driver = AndroidDriver(bridge_url=url)
    assert driver.status()["app"] == "MrGhosty"


def test_missing_token_is_a_clear_error(bridge, monkeypatch):
    url, _state = bridge
    monkeypatch.delenv("GHOST_HANDS_ANDROID_TOKEN", raising=False)
    driver = AndroidDriver(bridge_url=url)
    with pytest.raises(HandsError, match="pairing token"):
        driver.status()


# -- status validation ---------------------------------------------------------


def test_status_ok_and_capabilities(driver):
    status = driver.status()
    assert status["app"] == "MrGhosty"
    assert status["version"] == "1.7.0"
    assert status["foreground_package"] == "com.android.launcher3"
    assert driver.capabilities["perceive"] is True
    assert driver.capabilities["android"] is True
    assert driver.capabilities["screenshot"] is True  # fixture API 34


def test_status_screenshot_flag_follows_api_level():
    server, url, _state = make_bridge(FakeAndroidState(api_level=29))
    try:
        driver = AndroidDriver(bridge_url=url, token=TOKEN)
        driver.status()
        assert driver.capabilities["screenshot"] is False
    finally:
        server.shutdown()


def test_unreachable_bridge_error_is_actionable():
    driver = AndroidDriver(
        bridge_url=f"http://127.0.0.1:{_closed_port()}", token=TOKEN, timeout=2
    )
    with pytest.raises(HandsError) as excinfo:
        driver.status()
    message = str(excinfo.value)
    assert "could not reach" in message
    assert "adb forward tcp:8378 tcp:8378" in message
    assert TOKEN not in message


def test_token_mismatch_is_a_401_error(bridge):
    url, _state = bridge
    driver = AndroidDriver(bridge_url=url, token="definitely-wrong")
    with pytest.raises(HandsError) as excinfo:
        driver.status()
    assert "401" in str(excinfo.value)
    assert "token mismatch" in str(excinfo.value)


def test_token_header_is_sent(bridge):
    url, state = bridge
    driver = AndroidDriver(bridge_url=url, token=TOKEN)
    driver.status()
    assert state.seen_tokens and state.seen_tokens[-1] == TOKEN


def test_protocol_mismatch_is_loud():
    server, url, _state = make_bridge(FakeAndroidState(protocol=2))
    try:
        driver = AndroidDriver(bridge_url=url, token=TOKEN)
        with pytest.raises(HandsError, match="protocol mismatch"):
            driver.status()
    finally:
        server.shutdown()


def test_service_disconnected_is_loud():
    server, url, _state = make_bridge(FakeAndroidState(service_connected=False))
    try:
        driver = AndroidDriver(bridge_url=url, token=TOKEN)
        with pytest.raises(HandsError, match="accessibility service"):
            driver.status()
    finally:
        server.shutdown()


# -- perception ------------------------------------------------------------------


def test_perceive_maps_elements_with_body_ref(driver):
    element_map = driver.perceive()
    assert element_map.url == "android://com.android.launcher3"
    assert [el.number for el in element_map.elements] == [1, 2]
    notes = element_map.get(1)
    assert notes.name == "Notes"
    assert notes.role == "app_icon"
    assert notes.body_ref == "0/0"
    assert notes.attrs["package"] == "com.android.launcher3"
    assert notes.attrs["bounds"] == "0,100,540,340"
    descriptor = notes.descriptor()
    assert descriptor["body_ref"] == "0/0"
    assert element_map.get(2).body_ref == "0/1"


def test_snapshot_html_is_escaped(driver, bridge):
    _url, state = bridge
    state.notes.append('<script>alert("x")</script>')
    state.stack = ["home", "notes"]
    state.foreground_package = "com.example.notes"
    driver.perceive()
    html = driver.snapshot_html()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_current_url_uses_foreground_package(driver):
    assert driver.current_url() == "android://com.android.launcher3"


# -- acts -------------------------------------------------------------------------


def test_click_type_save_roundtrip(driver, bridge):
    _url, state = bridge
    m = driver.perceive()
    driver.act(A.click(1), m.get(1))  # Notes icon
    m = driver.perceive()
    new_note = m.find_by_text("New note")
    driver.act(A.click(new_note.number), new_note)
    m = driver.perceive()
    field = m.find_by_text("Note text")
    assert field.role == "text_field"
    driver.act(A.type(field.number, "hello ghost"), field)
    m = driver.perceive()
    save = m.find_by_text("Save")
    driver.act(A.click(save.number), save)
    assert state.notes == ["hello ghost"]


def test_act_target_missing_phrase(driver):
    driver.perceive()
    ghost = Element(
        number=99, tag="Button", role="button", name="Ghost", body_ref="0/9"
    )
    with pytest.raises(HandsError, match="target missing"):
        driver.act(A.click(99), ghost)


def test_act_target_mismatch_phrase(driver, bridge):
    _url, state = bridge
    m = driver.perceive()
    stale = m.get(1)
    # The screen changes under us: home -> notes, so ref 0/0 is now a
    # different element with a different key.
    state.stack = ["home", "notes"]
    state.foreground_package = "com.example.notes"
    with pytest.raises(HandsError, match="target mismatch"):
        driver.act(A.click(stale.number), stale)


def test_type_into_non_field_fails_honestly(driver):
    m = driver.perceive()
    icon = m.get(1)
    with pytest.raises(HandsError, match="not an editable"):
        driver.act(A.type(icon.number, "nope"), icon)


def test_press_keys(driver):
    assert "back" in driver.act(A.press("Back"), None)
    assert driver.act(A.press("home"), None) == "home"
    with pytest.raises(HandsError, match="unsupported on Android body"):
        driver.act(A.press("F13"), None)


def test_navigate_app_and_url(driver, bridge):
    _url, state = bridge
    result = driver.act(A.navigate("app://com.example.notes"), None)
    assert "launched" in result
    assert state.screen == "notes"
    driver.open("android://com.android.settings")
    assert state.screen == "settings"


def test_extract_text_and_targeted(driver):
    text = driver.act(A.extract(), None)
    assert "Notes" in text and "Settings" in text
    m = driver.perceive()
    assert driver.act(A.extract(target=1), m.get(1)) == "Notes"
    with pytest.raises(HandsError, match="unsupported on Android body"):
        driver.act(A.extract(mode="table"), None)


def test_screenshot_writes_png(driver, tmp_path):
    out = tmp_path / "shot.png"
    result = driver.act(A.screenshot(path=str(out)), None)
    raw = out.read_bytes()
    assert raw == base64.b64decode(TINY_PNG_B64)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert "saved" in result
    result2 = driver.act(A.screenshot(), None)
    assert driver.last_screenshot == raw
    assert "captured" in result2


def test_scroll(driver):
    assert "scrolled" in driver.act(A.scroll("down"), None)


def test_wait_plain_and_condition(driver):
    assert driver.act(A.wait(0.01), None) == "waited 0.01s"
    satisfied = driver.act(A.wait_for(text="Notes", timeout=2.0), None)
    assert "wait satisfied" in satisfied
    with pytest.raises(HandsError, match="wait timed out"):
        driver.act(A.wait_for(text="No such text", timeout=0.3), None)


@pytest.mark.parametrize(
    "action",
    [
        A.select(1, "x"),
        A.hover(1),
        A.right_click(1),
        A.fill_form({"a": "b"}),
        A.set_file(1, "/tmp/x"),
        A.download(url="https://example.com/x"),
        A.pdf(),
        A.set_viewport("mobile"),
    ],
)
def test_web_only_actions_fail_honestly(action):
    # No bridge needed: the refusal is local and immediate.
    driver = AndroidDriver(
        bridge_url=f"http://127.0.0.1:{_closed_port()}", token=TOKEN
    )
    element = Element(number=1, tag="Button", role="button", name="X",
                      body_ref="0/0")
    with pytest.raises(HandsError, match="unsupported on Android body"):
        driver.act(action, element)


def test_click_without_body_ref_is_target_missing(driver):
    element = Element(number=1, tag="Button", role="button", name="X")
    with pytest.raises(HandsError, match="target missing"):
        driver.act(A.click(1), element)


def test_close_is_idempotent(driver):
    driver.close()
    driver.close()


# -- gestures (v0.7): driver-side validation, before any HTTP ----------------


def test_drag_without_perceive_raises_before_http(bridge):
    url, state = bridge
    fresh = AndroidDriver(bridge_url=url, token=TOKEN)
    source = Element(
        number=1, tag="SeekBar", role="slider", name="Volume", body_ref="0/2"
    )
    with pytest.raises(
        HandsError, match="drag needs a destination in the last perceived map"
    ):
        fresh.act(A.drag(1, 2), source)
    assert state.act_payloads == []
    assert state.gestures == []


def test_drag_to_unknown_destination_raises_before_http(driver, bridge):
    _url, state = bridge
    m = driver.perceive()
    settings = m.get(2)
    driver.act(A.click(settings.number), settings)
    m = driver.perceive()
    slider = m.find_by_text("Volume")
    payloads_before = len(state.act_payloads)
    with pytest.raises(
        HandsError, match="drag needs a destination in the last perceived map"
    ):
        driver.act(A.drag(slider.number, 999), slider)
    # No act crossed the wire for the refused drag.
    assert len(state.act_payloads) == payloads_before
    assert [g for g in state.gestures if g["kind"] == "drag"] == []


def test_drag_without_source_raises(driver):
    driver.perceive()
    with pytest.raises(HandsError, match="drag requires a source"):
        driver.act(A.drag(1, 2), None)


def test_drag_without_to_target_raises_before_http(driver, bridge):
    _url, state = bridge
    m = driver.perceive()
    settings = m.get(2)
    driver.act(A.click(settings.number), settings)
    m = driver.perceive()
    slider = m.find_by_text("Volume")
    payloads_before = len(state.act_payloads)
    action = A.Action(kind="drag", target=slider.number)
    with pytest.raises(
        HandsError, match="drag needs a destination in the last perceived map"
    ):
        driver.act(action, slider)
    assert len(state.act_payloads) == payloads_before


def test_click_at_reaches_http_not_local_refusal():
    # click_at is no longer in the locally-refused kinds: against a
    # dead port it fails at the HTTP layer, not with "unsupported".
    driver = AndroidDriver(
        bridge_url=f"http://127.0.0.1:{_closed_port()}", token=TOKEN, timeout=2
    )
    with pytest.raises(HandsError, match="could not reach"):
        driver.act(A.click_at(10, 10), None)


def test_click_at_without_coordinates_raises_before_http():
    driver = AndroidDriver(
        bridge_url=f"http://127.0.0.1:{_closed_port()}", token=TOKEN
    )
    with pytest.raises(HandsError, match="click_at requires x and y"):
        driver.act(A.Action(kind="click_at"), None)
    with pytest.raises(HandsError, match="click_at requires x and y"):
        driver.act(A.Action(kind="click_at", x=10.0), None)
