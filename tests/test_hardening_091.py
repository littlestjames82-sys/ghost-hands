"""v0.9.1 hardening regression tests.

Every test here pins a finding from the 0.9.1 audit (see
HARDENING_091.md): each fails on the 0.9.0 code and passes on 0.9.1.
The audit's method note applies: page content, bus/bus-bridge/model
replies, and MCP client messages are all attacker-shaped input.
"""

from __future__ import annotations

import io
import json
import os
import stat

import pytest

from ghost_hands import actions as A
from ghost_hands.errors import HandsError
from ghost_hands.eyes import ElementMap
from ghost_hands.governor import Governor, Policy


# ---------------------------------------------------------------------------
# F1 — export: page-derived strings must not break out of the comment
# ---------------------------------------------------------------------------


def test_export_descriptor_comment_cannot_inject_code():
    from ghost_hands.export import export_ghost_hands_script

    evil = 'x"\n]\nimport os; os.system("echo PWNED")\nJUNK = ['
    events = [
        {"type": "perceive", "step": 1, "url": "https://demo.local/"},
        {
            "type": "execute",
            "step": 1,
            "action": {"kind": "click", "target": 1},
            "element": {"number": 1, "tag": "button", "name": evil},
        },
    ]
    src = export_ghost_hands_script(events)
    # The payload survives only as inert text inside one comment line:
    # no line of the generated program may *start* with the injection.
    for line in src.splitlines():
        assert not line.startswith("import os")
        assert not line.startswith("JUNK")
    import ast

    ast.parse(src)  # still a valid program
    assert src.count("\n") == export_ghost_hands_script(events).count("\n")


def test_export_comment_folds_newlines_in_href_and_id():
    from ghost_hands.export import _descriptor_comment

    comment = _descriptor_comment(
        {
            "number": 2,
            "tag": "a",
            "name": "ok",
            "href": "https://x.local/\nimport os",
            "id": "a\rb",
        }
    )
    assert "\n" not in comment and "\r" not in comment
    assert comment.startswith("  # ")


# ---------------------------------------------------------------------------
# F2 — governor: domain rules cover the target's own destination
# ---------------------------------------------------------------------------


def _el(html, text, url="https://good.local/"):
    m = ElementMap.from_html(html, url=url)
    found = m.find_by_text(text)
    assert found is not None
    return found


def test_governor_blocked_domain_via_link_click():
    e = _el('<a href="https://evil.local/steal">Details</a>', "Details")
    g = Governor(Policy(blocked_domains=("evil.local",)))
    d = g.evaluate(A.click(e.number), e, page_url="https://good.local/")
    assert d.outcome == "deny" and "blocked" in d.reason


def test_governor_allowlist_via_link_click():
    e = _el('<a href="https://other.local/x">Details</a>', "Details")
    g = Governor(Policy(allowed_domains=("good.local",)))
    d = g.evaluate(A.click(e.number), e, page_url="https://good.local/")
    assert d.outcome == "deny" and "allowlist" in d.reason


def test_governor_blocked_domain_via_form_action():
    e = _el(
        '<form action="https://evil.local/submit">'
        "<button type=\"submit\">Go</button></form>",
        "Go",
    )
    g = Governor(Policy(blocked_domains=("evil.local",)))
    d = g.evaluate(A.click(e.number), e, page_url="https://good.local/")
    assert d.outcome == "deny" and "blocked" in d.reason


def test_governor_same_host_and_relative_links_still_allowed():
    g = Governor(
        Policy(
            allowed_domains=("good.local",),
            blocked_domains=("evil.local",),
        )
    )
    abs_el = _el('<a href="https://good.local/about">About</a>', "About")
    assert (
        g.evaluate(A.click(abs_el.number), abs_el, page_url="https://good.local/")
        .outcome
        == "allow"
    )
    rel_el = _el('<a href="/about">About</a>', "About")
    assert (
        g.evaluate(A.click(rel_el.number), rel_el, page_url="https://good.local/")
        .outcome
        == "allow"
    )


def test_governor_typing_not_judged_by_form_action_host():
    # Typing navigates nowhere: a field in a cross-host form is judged
    # as a write; the form's host is judged when a submit is attempted.
    e = _el(
        '<form action="https://evil.local/submit">'
        '<input placeholder="Name"></form>',
        "Name",
    )
    g = Governor(Policy(blocked_domains=("evil.local",)))
    d = g.evaluate(A.type(e.number, "x"), e, page_url="https://good.local/")
    assert d.outcome == "allow"


# ---------------------------------------------------------------------------
# F3 — MCP server: hostile messages cannot crash the loop
# ---------------------------------------------------------------------------


def _session(monkeypatch):
    for var in (
        "GHOST_HANDS_DRIVER",
        "GHOST_HANDS_POLICY",
        "GHOST_HANDS_APPROVER",
        "GHOST_HANDS_START_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    from ghost_hands.mcp_server import HandsSession

    return HandsSession()


def test_mcp_non_dict_message_is_invalid_request(monkeypatch):
    from ghost_hands.mcp_server import handle_request

    s = _session(monkeypatch)
    for payload in ([1, 2, 3], "hello", 42, None):
        resp = handle_request(s, payload)
        assert resp is not None
        assert resp["error"]["code"] == -32600


def test_mcp_non_dict_params_and_arguments(monkeypatch):
    from ghost_hands.mcp_server import handle_request

    s = _session(monkeypatch)
    resp = handle_request(
        s, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": ["x"]}
    )
    assert resp["error"]["code"] == -32602
    resp = handle_request(
        s,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "hands_trail", "arguments": ["x"]},
        },
    )
    assert resp["error"]["code"] == -32602


def test_mcp_pages_must_be_str_to_str_map(monkeypatch):
    from ghost_hands.mcp_server import handle_request

    s = _session(monkeypatch)
    for bad_pages in (123, ["https://x.local/"], {"https://x.local/": 42}):
        resp = handle_request(
            s,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "hands_perceive",
                    "arguments": {"pages": bad_pages},
                },
            },
        )
        assert resp["result"]["isError"] is True
        assert "pages" in resp["result"]["content"][0]["text"]


def test_mcp_run_steps_must_be_action_objects(monkeypatch):
    from ghost_hands.mcp_server import handle_request

    s = _session(monkeypatch)
    resp = handle_request(
        s,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "hands_run",
                "arguments": {"steps": "click everything"},
            },
        },
    )
    assert resp["result"]["isError"] is True


def test_mcp_serve_loop_survives_garbage_and_oversize(monkeypatch):
    from ghost_hands.mcp_server import MAX_LINE_BYTES, _serve_loop

    s = _session(monkeypatch)
    lines = [
        "[1,2,3]",
        "x" * (MAX_LINE_BYTES + 10),
        '{"jsonrpc":"2.0","id":7,"method":"ping"}',
    ]
    inp = io.StringIO("\n".join(lines) + "\n")
    out = io.StringIO()
    _serve_loop(s, inp, out)  # must not raise
    responses = [json.loads(l) for l in out.getvalue().strip().splitlines()]
    assert len(responses) == 3
    assert responses[0]["error"]["code"] == -32600
    assert responses[1]["error"]["code"] == -32700
    assert responses[2] == {"jsonrpc": "2.0", "id": 7, "result": {}}


# ---------------------------------------------------------------------------
# F4 — BusClient: scheme allowlist, workspace slugs, response caps
# ---------------------------------------------------------------------------


def test_busclient_refuses_non_http_schemes():
    from ghost_hands.busclient import BusClient, BusError

    for bad in ("file:///etc/passwd", "ftp://x.local/", "gopher://x/", "/tmp/x"):
        with pytest.raises(BusError):
            BusClient(bad)


def test_busclient_refuses_workspace_traversal():
    from ghost_hands.busclient import BusClient, BusError

    for bad_ws in ("../../admin", "a/b", "a?x=1", "a#f", "..", "a b", ""):
        if bad_ws == "":
            continue  # empty means "no workspace", which is legitimate
        with pytest.raises(BusError):
            BusClient("http://127.0.0.1:8377", workspace=bad_ws)
    ok = BusClient("http://127.0.0.1:8377", workspace="hands-test_2")
    assert ok.base_url.endswith("/w/hands-test_2")


class _FakeResp:
    def __init__(self, body: bytes, status: int = 200):
        self._buf = io.BytesIO(body)
        self.status = status

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_busclient_response_size_cap(monkeypatch):
    from ghost_hands.busclient import MAX_RESPONSE_BYTES, BusClient, BusError

    body = b"x" * (MAX_RESPONSE_BYTES + 1)
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: _FakeResp(body)
    )
    client = BusClient("http://127.0.0.1:8377")
    with pytest.raises(BusError, match="exceeded"):
        client.health()


# ---------------------------------------------------------------------------
# F5 — credential-bearing files are owner-only (0600)
# ---------------------------------------------------------------------------


def test_trail_file_created_0600(tmp_path):
    from ghost_hands.trail import Trail

    path = tmp_path / "trail.jsonl"
    trail = Trail(path)
    trail.record("perceive", 1, url="https://x.local/")
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_trail_preexisting_loose_file_is_tightened(tmp_path):
    from ghost_hands.trail import Trail

    path = tmp_path / "trail.jsonl"
    path.write_text('{"type": "perceive", "step": 1}\n', encoding="utf-8")
    os.chmod(path, 0o644)
    Trail(path)
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_session_file_created_0600(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from ghost_hands.drivers import ChromiumDriver, find_chrome

    if find_chrome() is None:
        pytest.skip("no Chromium in this environment")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler API
            data = b"<html><head><title>s</title></head><body>ok</body></html>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    driver = ChromiumDriver()
    try:
        driver.open(f"http://127.0.0.1:{server.server_address[1]}/")
        path = tmp_path / "session.json"
        driver.save_session(path)
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    finally:
        driver.close()
        server.shutdown()


# ---------------------------------------------------------------------------
# F6 — Chromium navigation: local/internal schemes refused pre-launch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "FILE:///etc/passwd",
        "file\t://x",
        "chrome://settings",
        "javascript:alert(1)",
        "view-source:https://example.com",
        "devtools://x",
    ],
)
def test_chromium_refuses_non_navigable_schemes(url):
    from ghost_hands.drivers import ChromiumDriver

    driver = ChromiumDriver()
    with pytest.raises(HandsError, match="refusing to navigate"):
        driver.open(url)
    with pytest.raises(HandsError, match="refusing to navigate"):
        driver.open_tab(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "http://127.0.0.1:8000/x",
        "data:text/html,<p>fixture</p>",
        "about:blank",
        "/relative/path",
    ],
)
def test_chromium_navigable_schemes_pass_the_check(url):
    from ghost_hands.drivers import _check_navigable

    _check_navigable(url)  # must not raise


# ---------------------------------------------------------------------------
# F7 — download filename traversal (CDP suggestedFilename)
# ---------------------------------------------------------------------------


def test_download_suggested_filename_reduced_to_bare_name():
    from ghost_hands.drivers import ChromiumDriver

    driver = ChromiumDriver()
    driver._on_cdp_event(
        {
            "method": "Browser.downloadWillBegin",
            "params": {
                "guid": "g1",
                "suggestedFilename": "../../evil.txt",
                "url": "https://x.local/evil.txt",
            },
        }
    )
    assert driver._downloads["g1"]["filename"] == "evil.txt"


# ---------------------------------------------------------------------------
# F8 — response caps on the other HTTP clients (fail closed)
# ---------------------------------------------------------------------------


def test_android_driver_response_size_cap(monkeypatch):
    from ghost_hands.android_driver import MAX_RESPONSE_BYTES, AndroidDriver

    body = b"x" * (MAX_RESPONSE_BYTES + 1)
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: _FakeResp(body)
    )
    driver = AndroidDriver(token="pairing-token")
    with pytest.raises(HandsError, match="too large"):
        driver._request("GET", "/v1/status")


def test_phone_approver_oversize_response_never_approves(monkeypatch):
    from ghost_hands.phone_approver import MAX_RESPONSE_BYTES, PhoneApprover

    body = b"x" * (MAX_RESPONSE_BYTES + 1)
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: _FakeResp(body)
    )
    approver = PhoneApprover(token="pairing-token", timeout=5, poll_interval=0.01)
    assert approver.request_decision("test", "test", "summary") is False
    assert approver.last_error is not None and "too large" in approver.last_error


def test_decider_response_size_cap(monkeypatch):
    from ghost_hands.deciders import MAX_RESPONSE_BYTES, OpenAICompatibleDecider

    body = b"x" * (MAX_RESPONSE_BYTES + 1)
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: _FakeResp(body)
    )
    decider = OpenAICompatibleDecider(
        base_url="http://model.local/v1", api_key="k", model="m"
    )
    element_map = ElementMap.from_html("<p>x</p>", url="https://x.local/")
    with pytest.raises(HandsError, match="too large"):
        decider.decide("goal", element_map, [])


def test_doctor_probe_response_size_cap(monkeypatch):
    from ghost_hands.doctor import _MAX_PROBE_BYTES, _http_get_json

    body = b'{"ok": true, "pad": "' + b"x" * (_MAX_PROBE_BYTES + 10) + b'"}'
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda req, timeout=None: _FakeResp(body)
    )
    status, payload, error = _http_get_json("http://127.0.0.1:9/health", 1.0)
    assert status == 200
    assert payload is None  # over-cap body reads as "not a JSON object"
