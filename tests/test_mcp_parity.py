"""MCP parity (v0.8): the server runs against the body and approver
the environment selects — fake (default), chromium, simphone, android —
and asks can be decided by the phone instead of denied by default."""

import json
import threading
import time
import urllib.parse

import pytest

from fake_android_bridge import TOKEN, make_bridge
from ghost_hands.drivers import find_chrome
from ghost_hands.errors import HandsError
from ghost_hands.mcp_server import HandsSession, handle_request

CHROME = find_chrome()
needs_chrome = pytest.mark.skipif(
    CHROME is None, reason="no Chromium binary on this machine"
)

ENV_KEYS = (
    "GHOST_HANDS_DRIVER",
    "GHOST_HANDS_START_URL",
    "GHOST_HANDS_APPROVER",
    "GHOST_HANDS_POLICY",
    "GHOST_HANDS_ANDROID_BRIDGE",
    "GHOST_HANDS_ANDROID_TOKEN",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def bridge():
    server, url, state = make_bridge()
    yield url, state
    server.shutdown()


def phone_finger(state, approved, delay=0.3):
    """Ryan's finger, simulated (same pattern as the phone e2e suite)."""

    def run():
        deadline = time.time() + 15
        while time.time() < deadline:
            ids = state.pending_approval_ids()
            if ids:
                time.sleep(delay)
                state.decide_approval(ids[0], approved)
                return
            time.sleep(0.02)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def call(session, name, arguments, msg_id=1):
    resp = handle_request(
        session,
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    return resp["result"]


def text_of(result):
    return result["content"][0]["text"]


# ------------------------------------------------------------ env parsing


def test_default_session_is_fake_with_no_approver():
    session = HandsSession()
    assert session.driver_kind == "fake"
    assert session.approver is None
    assert session.approver_channel is None


def test_bad_driver_env_is_loud(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_DRIVER", "webkit")
    with pytest.raises(HandsError) as excinfo:
        HandsSession()
    assert "GHOST_HANDS_DRIVER" in str(excinfo.value)
    assert "webkit" in str(excinfo.value)


def test_bad_approver_env_is_loud(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_APPROVER", "smoke-signal")
    with pytest.raises(HandsError) as excinfo:
        HandsSession()
    assert "GHOST_HANDS_APPROVER" in str(excinfo.value)


# ------------------------------------------------------------- body parity


def test_android_session_perceive(monkeypatch, bridge):
    url, state = bridge
    monkeypatch.setenv("GHOST_HANDS_DRIVER", "android")
    monkeypatch.setenv("GHOST_HANDS_ANDROID_BRIDGE", url)
    monkeypatch.setenv("GHOST_HANDS_ANDROID_TOKEN", TOKEN)
    session = HandsSession()
    assert session.driver_kind == "android"
    result = call(session, "hands_perceive", {})
    text = text_of(result)
    assert "Notes" in text  # the fake phone's home screen
    assert "android://" in text
    # The pairing token never reaches tool output.
    assert TOKEN not in text
    assert TOKEN not in json.dumps(session.trail.events)


def test_simphone_session_perceive(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_DRIVER", "simphone")
    session = HandsSession()
    text = text_of(call(session, "hands_perceive", {}))
    assert "Notes" in text and "Settings" in text


def test_pages_arg_rejected_on_nonfake_body(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_DRIVER", "simphone")
    session = HandsSession()
    result = call(session, "hands_perceive", {"pages": {"https://x/": "<html/>"}})
    assert result["isError"] is True
    assert "fake" in text_of(result)


@needs_chrome
def test_chromium_session_smoke(monkeypatch):
    page = (
        "<html><head><title>Parity</title></head>"
        "<body><button>Tap me</button></body></html>"
    )
    monkeypatch.setenv("GHOST_HANDS_DRIVER", "chromium")
    monkeypatch.setenv(
        "GHOST_HANDS_START_URL", "data:text/html," + urllib.parse.quote(page)
    )
    session = HandsSession()
    try:
        init = handle_request(
            session, {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
        )
        assert init["result"]["serverInfo"]["name"] == "ghost-hands"
        listing = handle_request(
            session, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        names = [t["name"] for t in listing["result"]["tools"]]
        assert names == [
            "hands_perceive", "hands_act", "hands_run", "hands_trail"]
        # No URL passed: GHOST_HANDS_START_URL opens on first perceive.
        text = text_of(call(session, "hands_perceive", {}, msg_id=3))
        assert "Tap me" in text
        acted = text_of(
            call(session, "hands_act",
                 {"action": {"kind": "click", "target": 1}}, msg_id=4)
        )
        assert not acted.startswith("DENIED")
        assert not acted.startswith("ERROR")
    finally:
        session.close()


# ------------------------------------------------------------ run parity


def test_run_defaults_to_session_body_and_validates_driver():
    session = HandsSession()  # fake body
    result = call(
        session, "hands_run",
        {"steps": [{"kind": "click", "target": 2}], "goal": "open blog"},
    )
    report = json.loads(text_of(result))
    assert report["stop_reason"] in ("done", "decider finished", "max steps")
    bad = call(session, "hands_run", {"steps": [], "driver": "webkit"})
    assert bad["isError"] is True
    assert "unknown driver" in text_of(bad)


def test_run_on_simphone_body():
    session = HandsSession()
    result = call(
        session, "hands_run",
        {"steps": [{"kind": "wait", "seconds": 0}], "driver": "simphone"},
    )
    report = json.loads(text_of(result))
    assert "stop_reason" in report


# ------------------------------------------- approval channel: the default

# A one-page shop whose only element is a consequential submit button
# (the same shape test_mcp.py uses): "Pay now" is [1].
PAY_PAGES = {
    "https://shop.local/": (
        '<form action="/done"><button type="submit">Pay now</button></form>'
    )
}


def _perceive_pay_page(session):
    return call(session, "hands_perceive", {"pages": PAY_PAGES})


def test_default_ask_denied_and_message_names_the_channel():
    session = HandsSession()
    _perceive_pay_page(session)
    text = text_of(
        call(session, "hands_act", {"action": {"kind": "click", "target": 1}})
    )
    assert text.startswith("DENIED")
    assert "no approver (MCP default)" in text


# ---------------------------------------------- approval channel: phone


def _bridge_env(monkeypatch, bridge):
    url, state = bridge
    monkeypatch.setenv("GHOST_HANDS_APPROVER", "phone")
    monkeypatch.setenv("GHOST_HANDS_ANDROID_BRIDGE", url)
    monkeypatch.setenv("GHOST_HANDS_ANDROID_TOKEN", TOKEN)
    return state


def test_phone_approver_approves_consequential_act(monkeypatch, bridge):
    state = _bridge_env(monkeypatch, bridge)
    session = HandsSession()
    assert session.approver_channel == "phone"
    finger = phone_finger(state, approved=True)
    _perceive_pay_page(session)
    text = text_of(
        call(session, "hands_act", {"action": {"kind": "click", "target": 1}})
    )
    finger.join(timeout=5)
    assert text.startswith("approved via phone"), text
    approvals = [e for e in session.trail.events if e["type"] == "approval"]
    assert [a["phase"] for a in approvals] == ["request", "decision"]
    assert approvals[0]["channel"] == "phone"
    assert approvals[1]["approved"] is True
    assert TOKEN not in text


def test_phone_approver_denial_blocks_execution(monkeypatch, bridge):
    state = _bridge_env(monkeypatch, bridge)
    session = HandsSession()
    finger = phone_finger(state, approved=False)
    _perceive_pay_page(session)
    text = text_of(
        call(session, "hands_act", {"action": {"kind": "click", "target": 1}})
    )
    finger.join(timeout=5)
    assert text.startswith("DENIED"), text
    assert "via phone" in text
    # Nothing executed for that step.
    assert not [
        e for e in session.trail.events
        if e["type"] == "execute" and e.get("step") == session.step
    ]


def test_phone_approver_covers_hands_run(monkeypatch, bridge):
    # SHOP_PAGES flow: home -> cart -> "Place order — pay $42" (a
    # consequential submit). The phone approves it mid-run; a denial
    # would stop the run with stop_reason "denied".
    from pages_fixtures import SHOP_PAGES

    state = _bridge_env(monkeypatch, bridge)
    session = HandsSession()
    finger = phone_finger(state, approved=True)
    result = call(
        session, "hands_run",
        {
            "steps": [
                {"kind": "click", "target": 1},  # View cart
                {"kind": "click", "target": 1},  # Place order (ask)
            ],
            "pages": SHOP_PAGES,
            "goal": "buy the thing",
        },
    )
    finger.join(timeout=5)
    report = json.loads(text_of(result))
    assert report["stop_reason"] != "denied", report
    assert report["actions_by_class"].get("consequential", 0) >= 1
