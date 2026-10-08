import json

import ghost_hands
from ghost_hands.mcp_server import HandsSession, handle_request


def call(session, method, params=None, mid=1):
    return handle_request(
        session, {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}
    )


def test_initialize():
    resp = call(HandsSession(), "initialize")
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"] == {
        "name": "ghost-hands",
        "version": ghost_hands.__version__,
    }


def test_ping():
    resp = call(HandsSession(), "ping")
    assert resp["result"] == {}


def test_notification_gets_no_response():
    resp = handle_request(
        HandsSession(), {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert resp is None


def test_tools_list():
    resp = call(HandsSession(), "tools/list")
    names = [t["name"] for t in resp["result"]["tools"]]
    assert names == ["hands_perceive", "hands_act", "hands_run", "hands_trail"]


def test_unknown_method_error():
    resp = call(HandsSession(), "nope/nope")
    assert resp["error"]["code"] == -32601


def test_unknown_tool_error():
    resp = call(HandsSession(), "tools/call", {"name": "hands_nope", "arguments": {}})
    assert resp["error"]["code"] == -32602


def test_perceive_returns_map():
    s = HandsSession()
    resp = call(s, "tools/call", {"name": "hands_perceive", "arguments": {}})
    text = resp["result"]["content"][0]["text"]
    assert "https://demo.local/" in text
    assert "Search the demo web" in text


def test_act_type_then_trail():
    s = HandsSession()
    call(s, "tools/call", {"name": "hands_perceive", "arguments": {}}, mid=1)
    resp = call(
        s,
        "tools/call",
        {"name": "hands_act", "arguments": {"action": {"kind": "type", "target": 1, "text": "ghost"}}},
        mid=2,
    )
    assert "typed into" in resp["result"]["content"][0]["text"]
    resp = call(s, "tools/call", {"name": "hands_trail", "arguments": {}}, mid=3)
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["accounting"]["steps"] == 1
    assert payload["accounting"]["actions_by_class"].get("write") == 1


def test_act_consequential_denied_on_mcp():
    s = HandsSession()
    pages = {
        "https://shop.local/": '<form action="/done"><button type="submit">Pay now</button></form>'
    }
    call(s, "tools/call", {"name": "hands_perceive", "arguments": {"pages": pages}}, mid=1)
    resp = call(
        s,
        "tools/call",
        {"name": "hands_act", "arguments": {"action": {"kind": "click", "target": 1}}},
        mid=2,
    )
    text = resp["result"]["content"][0]["text"]
    assert text.startswith("DENIED")


def test_hands_run_fake():
    s = HandsSession()
    steps = [
        {"kind": "type", "target": 1, "text": "ghost"},
        {"kind": "click", "target": 2},
        {"kind": "done", "summary": "searched"},
    ]
    resp = call(
        s, "tools/call", {"name": "hands_run", "arguments": {"steps": steps}}, mid=1
    )
    report = json.loads(resp["result"]["content"][0]["text"])
    assert report["stop_reason"] == "done"
    assert report["steps"] == 2
