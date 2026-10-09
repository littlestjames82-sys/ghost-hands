"""PhoneApprover (v0.6): unit + wire-contract tests against the fake
MrGhosty bridge (tests/fake_android_bridge.py — the same HTTP/JSON
contract as the real Java bridge in MrGhosty v1.7+).

Covered: the SAFE summary (typed text and form values are redacted by
construction), the request/poll cycle for every outcome (approved,
denied, expired, local timeout, unreachable, 401), the bridge's
approval contract (201/400/404/409, lazy expiry, and — structurally —
the absence of any decision endpoint), and the trail's approval
events (channel: phone).
"""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from ghost_hands import actions as A
from ghost_hands.errors import HandsError
from ghost_hands.eyes import ElementMap
from ghost_hands.phone_approver import PhoneApprover, build_summary
from ghost_hands.trail import Trail

from fake_android_bridge import TOKEN, make_bridge

SECRET = "s3cret-password"  # 15 characters


@pytest.fixture
def bridge():
    server, url, state = make_bridge()
    yield url, state
    server.shutdown()


def decide_soon(state, approved=True, delay=0.25):
    """Simulate Ryan's finger: when a pending approval appears on the
    'phone', decide it after a short delay (from another thread)."""

    def run():
        deadline = time.time() + 10
        while time.time() < deadline:
            ids = list(state.approvals)
            if ids:
                time.sleep(delay)
                state.decide_approval(ids[-1], approved)
                return
            time.sleep(0.02)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def expire_soon(state, delay=0.25):
    def run():
        deadline = time.time() + 10
        while time.time() < deadline:
            ids = list(state.approvals)
            if ids:
                time.sleep(delay)
                state.expire_approval(ids[-1])
                return
            time.sleep(0.02)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def raw(method, base, path, payload=None, token=TOKEN):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"X-Ghost-Hands-Token": token}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(raw_body)
        except ValueError:
            return exc.code, {"raw": raw_body}


def make_approver(url, **kwargs):
    kwargs.setdefault("token", TOKEN)
    kwargs.setdefault("timeout", 10)
    kwargs.setdefault("poll_interval", 0.05)
    return PhoneApprover(bridge_url=url, **kwargs)


# ---------------------------------------------------------------------------
# The SAFE summary: redaction is structural
# ---------------------------------------------------------------------------


def _password_map():
    return ElementMap.from_elements(
        [
            {
                "tag": "input",
                "role": "textbox",
                "name": "Password",
                "type": "password",
            }
        ],
        url="https://app.local/login",
    )


def test_summary_type_shows_count_never_text():
    element = _password_map().get(1)
    summary = build_summary(
        A.type(1, SECRET), "consequential", element, "https://app.local/login"
    )
    assert SECRET not in summary
    assert f"{len(SECRET)} characters" in summary
    assert "Password" in summary
    assert "type (consequential)" in summary


def test_summary_fill_form_names_never_values():
    action = A.fill_form(
        {"Card number": "4111111111111111", "Email": "ryan@example.com"},
        submit=True,
    )
    summary = build_summary(action, "consequential", None, "https://shop.local/pay")
    assert "Card number" in summary and "Email" in summary
    assert "4111111111111111" not in summary
    assert "ryan@example.com" not in summary
    assert "SUBMIT" in summary


def test_payload_over_the_wire_is_redacted(bridge):
    url, state = bridge
    trail = Trail()
    approver = make_approver(url, trail=trail)
    approver.observe(_password_map())
    decide_soon(state, approved=True)
    assert approver(A.type(1, SECRET)) is True
    payload = state.approval_payloads[-1]
    blob = json.dumps(payload)
    assert SECRET not in blob
    assert TOKEN not in blob
    assert payload["kind"] == "type"
    assert payload["target"]["name"] == "Password"
    assert f"{len(SECRET)} characters" in payload["summary"]
    assert payload["url"] == "https://app.local/login"
    assert payload["test"] is False


# ---------------------------------------------------------------------------
# The request/poll cycle, every outcome
# ---------------------------------------------------------------------------


def test_approved_flow_and_trail_events(bridge):
    url, state = bridge
    trail = Trail()
    approver = make_approver(url, trail=trail)
    decide_soon(state, approved=True)
    assert approver(A.click(3)) is True
    assert approver.last_status == "approved"
    events = [ev for ev in trail.events if ev["type"] == "approval"]
    assert len(events) == 2
    request, decision = events
    assert request["channel"] == "phone" and request["phase"] == "request"
    assert decision["phase"] == "decision"
    assert decision["approved"] is True
    assert decision["decided_by"] == "phone"
    assert decision["request_id"] == request["request_id"]
    assert decision["request_id"] == state.approval_payloads[-1]["id"]


def test_denied_flow(bridge):
    url, state = bridge
    trail = Trail()
    approver = make_approver(url, trail=trail)
    decide_soon(state, approved=False)
    assert approver(A.click(3)) is False
    assert approver.last_status == "denied"
    decision = [ev for ev in trail.events if ev["type"] == "approval"][-1]
    assert decision["approved"] is False
    assert decision["decided_by"] == "phone"


def test_expired_flow(bridge):
    url, state = bridge
    approver = make_approver(url)
    expire_soon(state)
    assert approver(A.click(3)) is False
    assert approver.last_status == "expired"


def test_local_timeout_is_a_denial(bridge):
    url, state = bridge
    approver = make_approver(url, timeout=0.4)
    started = time.time()
    assert approver(A.click(3)) is False
    assert time.time() - started < 5
    assert approver.last_status == "timeout"
    assert state.pending_approval_ids(), "the request was still created on the phone"


def test_unreachable_is_a_denial_with_a_clear_error():
    approver = PhoneApprover(
        bridge_url="http://127.0.0.1:1", token=TOKEN, timeout=5, poll_interval=0.05
    )
    assert approver(A.click(1)) is False
    assert approver.last_status == "error"
    assert approver.last_error and "could not reach" in approver.last_error
    assert TOKEN not in approver.last_error


def test_wrong_token_is_a_denial(bridge):
    url, state = bridge
    approver = make_approver(url, token="not-the-pairing-code")
    assert approver(A.click(1)) is False
    assert approver.last_status == "error"
    assert approver.last_error and "401" in approver.last_error


def test_missing_token_is_a_denial(bridge, monkeypatch):
    url, state = bridge
    monkeypatch.delenv("GHOST_HANDS_ANDROID_TOKEN", raising=False)
    approver = PhoneApprover(bridge_url=url, timeout=5, poll_interval=0.05)
    assert approver(A.click(1)) is False
    assert approver.last_error and "pairing token" in approver.last_error


def test_nonlocal_bridge_refused_unless_explicit():
    with pytest.raises(HandsError, match="non-loopback"):
        PhoneApprover(bridge_url="http://192.0.2.10:8378", token=TOKEN)
    approver = PhoneApprover(
        bridge_url="http://192.0.2.10:8378", token=TOKEN, allow_nonlocal=True
    )
    assert approver.bridge_url == "http://192.0.2.10:8378"


def test_event_sink_receives_approval_events(bridge):
    url, state = bridge
    seen = []
    approver = make_approver(
        url, event_sink=lambda t, step, fields: seen.append((t, fields))
    )
    decide_soon(state, approved=True)
    assert approver(A.click(2)) is True
    assert [t for t, _ in seen] == ["approval", "approval"]
    assert seen[0][1]["channel"] == "phone" and seen[0][1]["phase"] == "request"
    assert seen[1][1]["approved"] is True


def test_test_approval_round_trip(bridge):
    url, state = bridge
    approver = make_approver(url)
    decide_soon(state, approved=True)
    assert approver.test_approval() == "approved"
    payload = state.approval_payloads[-1]
    assert payload["kind"] == "test" and payload["test"] is True
    assert "runs nothing" in payload["summary"]


# ---------------------------------------------------------------------------
# The bridge contract (what the Java bridge must also honor)
# ---------------------------------------------------------------------------


def _approval_payload(**overrides):
    payload = {
        "id": "abc123",
        "kind": "click",
        "classification": "consequential",
        "summary": "click (consequential) on <button> button \"Place order\"",
        "target": {"number": 1, "tag": "button", "role": "button", "name": "Place order"},
        "url": "https://shop.local/cart",
        "timeout_seconds": 60,
        "test": False,
    }
    payload.update(overrides)
    return payload


def test_contract_create_and_read_back(bridge):
    url, state = bridge
    status, body = raw("POST", url, "/v1/approvals", _approval_payload())
    assert status == 201
    assert body == {"ok": True, "id": "abc123", "status": "pending"}
    status, body = raw("GET", url, "/v1/approvals/abc123")
    assert status == 200 and body["status"] == "pending"


def test_contract_duplicate_id_conflict(bridge):
    url, state = bridge
    assert raw("POST", url, "/v1/approvals", _approval_payload())[0] == 201
    status, body = raw("POST", url, "/v1/approvals", _approval_payload())
    assert status == 409
    assert "duplicate" in body["error"]


def test_contract_bad_payloads_rejected(bridge):
    url, state = bridge
    for bad in (
        _approval_payload(id=""),
        _approval_payload(kind=""),
        _approval_payload(summary=""),
        _approval_payload(timeout_seconds=0),
        _approval_payload(timeout_seconds=-5),
    ):
        status, _ = raw("POST", url, "/v1/approvals", bad)
        assert status == 400, bad


def test_contract_unknown_id_404(bridge):
    url, state = bridge
    status, _ = raw("GET", url, "/v1/approvals/nope")
    assert status == 404


def test_contract_requires_token(bridge):
    url, state = bridge
    status, _ = raw("POST", url, "/v1/approvals", _approval_payload(), token="wrong")
    assert status == 401
    status, _ = raw("GET", url, "/v1/approvals/abc123", token="wrong")
    assert status == 401


def test_contract_lazy_expiry(bridge):
    url, state = bridge
    status, _ = raw(
        "POST", url, "/v1/approvals", _approval_payload(timeout_seconds=0.05)
    )
    assert status == 201
    time.sleep(0.15)
    status, body = raw("GET", url, "/v1/approvals/abc123")
    assert status == 200 and body["status"] == "expired"


def test_contract_decision_visible_with_decided_by(bridge):
    url, state = bridge
    raw("POST", url, "/v1/approvals", _approval_payload())
    assert state.decide_approval("abc123", True)
    status, body = raw("GET", url, "/v1/approvals/abc123")
    assert body["status"] == "approved"
    assert body["decided_by"] == "phone"
    assert body["decided_at"]


def test_contract_there_is_no_decision_endpoint(bridge):
    """The structural rule: a token holder can create and read, never
    decide. Every plausible decision route must not exist."""
    url, state = bridge
    raw("POST", url, "/v1/approvals", _approval_payload())
    for method, path in (
        ("POST", "/v1/approvals/abc123/decide"),
        ("POST", "/v1/approvals/abc123/approve"),
        ("POST", "/v1/approvals/abc123"),
        ("PUT", "/v1/approvals/abc123"),
        ("DELETE", "/v1/approvals/abc123"),
    ):
        status, _ = raw(method, url, path, _approval_payload())
        assert status in (404, 405, 501), (method, path, status)
    # And the approval is still pending afterwards.
    _, body = raw("GET", url, "/v1/approvals/abc123")
    assert body["status"] == "pending"
