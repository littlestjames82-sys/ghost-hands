"""Ghost Hands bench: a self-contained verification suite.

Run with ``ghost-hands bench`` (offline suite) or ``ghost-hands bench
--live`` (adds the real-network cases against example.com and Wikipedia).
Every case prints PASS / FAIL / SKIP and a summary line
``GHOST HANDS BENCH: X/Y passed`` (plus ``GHOST HANDS BENCH (LIVE): X/Y``
when live cases run). Exit code is non-zero when any case fails. Chrome
cases SKIP with a note only when no Chromium binary exists on the machine;
live cases SKIP with the reason when the network cannot be reached —
assertions that fail on a reached page are real FAILs.
"""

from __future__ import annotations

import ast
import html as html_lib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import actions as A
from .actions import Action
from .deciders import OpenAICompatibleDecider, RuleDecider, ScriptedDecider
from .drivers import (
    CDPMessageBuffer,
    ChromiumDriver,
    DEMO_PAGES,
    FakeDriver,
    demo_steps,
    encode_cdp_message,
    find_chrome,
)
from .errors import HandsError
from .export import export_ghost_hands_script
from .eyes import ElementMap
from .governor import Governor, Policy, classify
from .mcp_server import HandsSession, handle_request
from .runner import Runner
from .trail import Trail, accounting

SKIP = "SKIP"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SHOP_PAGES = {
    "https://shop.local/": """<!doctype html><html><head><title>Shop</title></head>
<body><h1>Shop</h1>
<a href="https://shop.local/cart">View cart</a>
</body></html>""",
    "https://shop.local/cart": """<!doctype html><html><head><title>Cart</title></head>
<body><h1>Your cart</h1>
<form action="https://shop.local/thanks">
<button type="submit">Place order — pay $42</button>
</form>
</body></html>""",
    "https://shop.local/thanks": """<!doctype html><html><head><title>Thanks</title></head>
<body><h1>Order placed</h1></body></html>""",
}

LOGIN_PAGES = {
    "https://app.local/login": """<!doctype html><html><head><title>Login</title></head>
<body>
<form action="https://app.local/home">
<input id="user" name="user" type="text" placeholder="Username">
<input id="pass" name="pass" type="password" placeholder="Password">
<button type="submit">Sign in</button>
</form>
</body></html>""",
    "https://app.local/home": """<!doctype html><html><head><title>Home</title></head>
<body><h1>Welcome</h1></body></html>""",
}

TOGGLE_PAGES = {
    "https://toggle.local/a": """<!doctype html><html><body>
<button data-nav="https://toggle.local/b">Toggle</button></body></html>""",
    "https://toggle.local/b": """<!doctype html><html><body>
<p>Other side</p>
<button data-nav="https://toggle.local/a">Toggle</button></body></html>""",
}

NOOP_PAGES = {
    "https://noop.local/": """<!doctype html><html><body>
<button>Do nothing</button></body></html>""",
}


def _el(html: str, text: str):
    m = ElementMap.from_html(html)
    el = m.find_by_text(text)
    assert el is not None, f"no element matching {text!r}"
    return m, el


def _run(pages, steps, approver=None, policy=None, start=None):
    driver = FakeDriver(pages, start_url=start)
    trail = Trail()
    runner = Runner(
        driver, ScriptedDecider(steps), approver=approver, policy=policy, trail=trail
    )
    report = runner.run("bench")
    return driver, trail, report


# ---------------------------------------------------------------------------
# Element-map parsing
# ---------------------------------------------------------------------------


def case_map_button():
    m = ElementMap.from_html("<button>Sign in</button>")
    assert len(m) == 1 and m.elements[0].name == "Sign in"
    assert '[1] <button> "Sign in"' in m.render()


def case_map_link_href():
    m = ElementMap.from_html('<a href="https://x.local/cart">Cart</a>')
    el = m.elements[0]
    assert el.role == "link" and el.href == "https://x.local/cart"


def case_map_input_placeholder():
    m = ElementMap.from_html('<input type="text" placeholder="Username">')
    assert m.elements[0].name == "Username"


def case_map_aria_label():
    m = ElementMap.from_html('<button aria-label="Close dialog">X</button>')
    assert m.elements[0].name == "Close dialog"


def case_map_role_button():
    m = ElementMap.from_html('<div role="button">Tap me</div>')
    assert len(m) == 1 and m.elements[0].name == "Tap me"


def case_map_onclick():
    m = ElementMap.from_html('<span onclick="go()">Launch</span>')
    assert len(m) == 1 and m.elements[0].name == "Launch"


def case_map_budget_truncation():
    html = "".join(f"<button>Button number {i} with a long label</button>" for i in range(40))
    m = ElementMap.from_html(html)
    rendered = m.render(budget=200)
    assert "truncated" in rendered and len(rendered) < 400


def case_map_select_textarea():
    m = ElementMap.from_html(
        '<select name="color"><option>Red</option></select><textarea placeholder="Notes"></textarea>'
    )
    assert len(m) == 2
    assert m.elements[0].role == "combobox" and m.elements[1].name == "Notes"


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


def case_cls_link_readonly():
    _, el = _el('<a href="https://x.local/about">About</a>', "About")
    assert classify(A.click(el.number), el) == "readonly"


def case_cls_submit_consequential():
    _, el = _el('<form action="/go"><button type="submit">Sign in</button></form>', "Sign in")
    assert classify(A.click(el.number), el) == "consequential"


def case_cls_delete_consequential():
    _, el = _el("<button>Delete account</button>", "Delete account")
    assert classify(A.click(el.number), el) == "consequential"


def case_cls_type_write():
    _, el = _el('<input placeholder="Username">', "Username")
    assert classify(A.type(el.number, "ryan"), el) == "write"


def case_cls_type_payment_consequential():
    _, el = _el('<input placeholder="Payment card number">', "Payment")
    assert classify(A.type(el.number, "4242"), el) == "consequential"


def case_cls_navigate_readonly():
    assert classify(A.navigate("https://x.local/docs")) == "readonly"


def case_cls_navigate_delete_consequential():
    assert classify(A.navigate("https://x.local/account/delete")) == "consequential"


def case_cls_extract_screenshot_readonly():
    assert classify(A.extract()) == "readonly"
    assert classify(A.screenshot()) == "readonly"
    assert classify(A.scroll()) == "readonly"


def case_cls_plain_button_write():
    _, el = _el("<button>Add item</button>", "Add item")
    assert classify(A.click(el.number), el) == "write"


# ---------------------------------------------------------------------------
# Governor
# ---------------------------------------------------------------------------


def case_gov_readonly_allowed():
    d = Governor().evaluate(A.navigate("https://x.local/"))
    assert d.outcome == "allow" and d.classification == "readonly"


def case_gov_consequential_asks():
    _, el = _el('<form action="/go"><button type="submit">Buy</button></form>', "Buy")
    d = Governor().evaluate(A.click(el.number), el, page_url="https://x.local/")
    assert d.outcome == "ask" and d.classification == "consequential"


def case_gov_consequential_denied_by_policy():
    gov = Governor(Policy(consequential="deny"))
    _, el = _el("<button>Publish post</button>", "Publish")
    d = gov.evaluate(A.click(el.number), el)
    assert d.outcome == "deny"


def case_gov_blocked_domain():
    gov = Governor(Policy(blocked_domains=("evil.local",)))
    d = gov.evaluate(A.navigate("https://evil.local/"))
    assert d.outcome == "deny" and "blocked" in d.reason


def case_gov_allowlist_excludes():
    gov = Governor(Policy(allowed_domains=("good.local",)))
    d = gov.evaluate(A.navigate("https://other.local/"))
    assert d.outcome == "deny" and "allowlist" in d.reason


def case_gov_write_denied_by_policy():
    gov = Governor(Policy(write="deny"))
    _, el = _el('<input placeholder="Name">', "Name")
    d = gov.evaluate(A.type(el.number, "x"), el)
    assert d.outcome == "deny" and d.classification == "write"


def case_gov_navigate_blocked_domain():
    gov = Governor(Policy(blocked_domains=("bank.local",)))
    d = gov.evaluate(A.navigate("https://bank.local/login"))
    assert d.outcome == "deny"


# ---------------------------------------------------------------------------
# FakeDriver end-to-end
# ---------------------------------------------------------------------------


def case_e2e_search_flow():
    driver, _, report = _run(DEMO_PAGES, demo_steps())
    assert report.stop_reason == "done", report.summary
    assert driver.current_url() == "https://demo.local/todo"


def case_e2e_login_form():
    steps = [
        {"kind": "type", "target": 1, "text": "ryan"},
        {"kind": "type", "target": 2, "text": "hunter2"},
        {"kind": "click", "target": 3},
        {"kind": "done", "summary": "logged in"},
    ]
    driver, _, report = _run(LOGIN_PAGES, steps, approver=lambda a: True)
    assert report.stop_reason == "done", report.summary
    assert len(driver.submissions) == 1
    fields = driver.submissions[0]["fields"]
    assert fields.get("user") == "ryan" and fields.get("pass") == "hunter2"
    assert driver.current_url() == "https://app.local/home"


def case_e2e_todo_add():
    steps = [
        {"kind": "type", "target": 1, "text": "Ship Ghost Hands"},
        {"kind": "click", "target": 2},
        {"kind": "done", "summary": "added"},
    ]
    driver, _, report = _run(
        DEMO_PAGES, steps, start="https://demo.local/todo"
    )
    assert report.stop_reason == "done", report.summary
    assert "Ship Ghost Hands" in driver.snapshot_html()


def case_e2e_checkout_blocked():
    steps = [
        {"kind": "click", "target": 1},
        {"kind": "click", "target": 1},
        {"kind": "done", "summary": "ordered"},
    ]
    driver, _, report = _run(SHOP_PAGES, steps)  # no approver
    assert report.stop_reason == "denied", report.summary
    assert "approval required, no approver" in report.summary
    assert driver.submissions == []


def case_e2e_checkout_allowed_with_approver():
    steps = [
        {"kind": "click", "target": 1},
        {"kind": "click", "target": 1},
        {"kind": "done", "summary": "ordered"},
    ]
    driver, _, report = _run(SHOP_PAGES, steps, approver=lambda a: True)
    assert report.stop_reason == "done", report.summary
    assert len(driver.submissions) == 1
    assert driver.current_url() == "https://shop.local/thanks"


def case_e2e_external_domain_denied():
    steps = [
        {"kind": "navigate", "url": "https://other.local/phish"},
        {"kind": "done", "summary": "went"},
    ]
    policy = Policy(allowed_domains=("demo.local",))
    _, _, report = _run(DEMO_PAGES, steps, policy=policy)
    assert report.stop_reason == "denied", report.summary


# ---------------------------------------------------------------------------
# Stuck detection
# ---------------------------------------------------------------------------


def case_stuck_repeated_action():
    steps = [{"kind": "click", "target": 1}] * 10
    _, _, report = _run(TOGGLE_PAGES, steps, start="https://toggle.local/a")
    assert report.stop_reason == "stuck", report.summary


def case_stuck_unchanged_map():
    steps = [{"kind": "click", "target": 1}] * 10
    _, _, report = _run(NOOP_PAGES, steps)
    assert report.stop_reason == "stuck", report.summary


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------


def case_accounting_counts():
    _, trail, report = _run(DEMO_PAGES, demo_steps())
    acct = trail.accounting()
    executed = sum(1 for e in trail.events if e["type"] == "execute")
    assert acct["steps"] == executed == report.steps
    total_by_class = sum(acct["actions_by_class"].values())
    govern_events = sum(1 for e in trail.events if e["type"] == "govern")
    assert total_by_class == govern_events


def case_accounting_token_estimate():
    events = [
        {"type": "perceive", "step": 1, "map_chars": 1000},
        {"type": "perceive", "step": 2, "map_chars": 400},
        {"type": "execute", "step": 1},
    ]
    acct = accounting(events)
    assert acct["map_chars"] == 1400 and acct["est_tokens"] == 350
    assert acct["steps"] == 1


# ---------------------------------------------------------------------------
# Replay export
# ---------------------------------------------------------------------------


def _demo_trail():
    _, trail, _ = _run(DEMO_PAGES, demo_steps())
    return trail.events


def case_export_parses():
    src = export_ghost_hands_script(_demo_trail())
    ast.parse(src)  # raises if invalid


def case_export_encodes_steps():
    src = export_ghost_hands_script(_demo_trail())
    assert "https://demo.local/todo" in src  # the navigate step
    assert "Ship Ghost Hands" in src  # the typed text
    assert '"click"' in src


def case_export_is_ghost_hands_script():
    src = export_ghost_hands_script(_demo_trail())
    assert "from ghost_hands import" in src
    assert "ChromiumDriver" in src
    assert "playwright" not in src.lower()
    assert "selenium" not in src.lower()


# ---------------------------------------------------------------------------
# MCP smoke
# ---------------------------------------------------------------------------


def case_mcp_initialize():
    s = HandsSession()
    resp = handle_request(
        s, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert resp["result"]["protocolVersion"] == "2024-11-05"
    assert resp["result"]["serverInfo"]["name"] == "ghost-hands"


def case_mcp_tools_list():
    s = HandsSession()
    resp = handle_request(s, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in resp["result"]["tools"]]
    assert names == ["hands_perceive", "hands_act", "hands_run", "hands_trail"]


def case_mcp_perceive_and_act():
    s = HandsSession()
    resp = handle_request(
        s,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "hands_perceive", "arguments": {}},
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert "Search" in text
    resp = handle_request(
        s,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "hands_act",
                "arguments": {"action": {"kind": "type", "target": 1, "text": "ghost"}},
            },
        },
    )
    assert "typed into" in resp["result"]["content"][0]["text"]
    resp = handle_request(
        s,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "hands_trail", "arguments": {}},
        },
    )
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["accounting"]["steps"] >= 1


# ---------------------------------------------------------------------------
# RuleDecider
# ---------------------------------------------------------------------------


def case_rule_go_to():
    d = RuleDecider()
    m = ElementMap.from_html(DEMO_PAGES["https://demo.local/"], url="https://demo.local/")
    a = d.decide("go to https://demo.local/todo", m, [])
    assert a.kind == "navigate" and a.url == "https://demo.local/todo"


def case_rule_click():
    d = RuleDecider()
    m = ElementMap.from_html(DEMO_PAGES["https://demo.local/"])
    a = d.decide("click Open the todo list", m, [])
    assert a.kind == "click" and m.get(a.target).name.startswith("Open the todo")


def case_rule_type_into():
    d = RuleDecider()
    m = ElementMap.from_html(DEMO_PAGES["https://demo.local/"])
    a = d.decide("type ghost into Search the demo web", m, [])
    assert a.kind == "type" and a.text == "ghost"


def case_rule_search_for():
    driver = FakeDriver(DEMO_PAGES)
    runner = Runner(driver, RuleDecider(), trail=Trail())
    report = runner.run("search for ghost")
    assert report.stop_reason == "done", report.summary
    assert driver.current_url() == "https://demo.local/results"


# ---------------------------------------------------------------------------
# ChromiumDriver (our own CDP stack)
# ---------------------------------------------------------------------------


def case_chrome_discovery():
    found = find_chrome()
    assert found is not None, "expected a Chrome binary on this machine class"
    assert find_chrome("/definitely/not/a/browser") is None
    assert find_chrome(found) == found


def case_cdp_framing():
    msg = {"id": 7, "method": "Page.enable", "params": {}}
    framed = encode_cdp_message(msg)
    assert framed.endswith(b"\x00")
    assert json.loads(framed[:-1]) == msg


def case_cdp_buffer_matching():
    buf = CDPMessageBuffer()
    a = encode_cdp_message({"id": 1, "result": {"ok": True}})
    b = encode_cdp_message({"id": 2, "result": {"ok": True}})
    # split mid-frame: nothing complete yet
    assert buf.feed(a[:5]) == []
    out = buf.feed(a[5:] + b)
    assert [m["id"] for m in out] == [1, 2]


def case_live_chrome_smoke():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    fixture_html = (
        "<!doctype html><html><head><title>Fixture</title></head><body>"
        '<input id="q" type="text" placeholder="Search the fixture">'
        '<button id="go" onclick="document.body.setAttribute('
        "'data-clicked','yes')\">Go now</button>"
        "</body></html>"
    )
    url = "data:text/html," + urllib.parse.quote(fixture_html)
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(url)
        m = ElementMap.from_html(driver.snapshot_html(), url=driver.current_url())
        names = [e.name for e in m.elements]
        assert any("Search the fixture" in n for n in names), names
        box = m.find_by_text("Search the fixture")
        btn = m.find_by_text("Go now")
        assert box is not None and btn is not None
        driver.act(A.type(box.number, "ghost hands"), box)
        value = driver.act(A.extract(box.number), box)
        assert value == "ghost hands", value
        driver.act(A.click(btn.number), btn)
        after = driver.snapshot_html()
        assert 'data-clicked="yes"' in after
    finally:
        driver.close()
    return True


# ---------------------------------------------------------------------------
# Shared HTTP fixture server (session / tabs / screenshot / export cases)
# ---------------------------------------------------------------------------


def _serve_pages(pages_by_path: dict):
    """Serve {path: html} over 127.0.0.1. Values may also be a
    (body, content_type, headers) tuple for binary/download fixtures.
    The dict is read per-request, so callers may fill it after learning
    the base URL. Returns (server, base_url)."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler API
            path = self.path.split("?", 1)[0]
            body = pages_by_path.get(path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"not found")
                return
            if isinstance(body, tuple):
                payload, ctype, headers = body
                data = payload if isinstance(payload, bytes) else payload.encode("utf-8")
            else:
                data, ctype, headers = body.encode("utf-8"), "text/html; charset=utf-8", {}
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _data_url(page: str) -> str:
    return "data:text/html," + urllib.parse.quote(page)


def _color_page() -> str:
    divs = "".join(
        f'<div style="background:hsl({(i * 7) % 360},70%,55%);height:26px"></div>'
        for i in range(80)
    )
    return (
        "<!doctype html><html><head><title>Color Fixture</title></head><body>"
        f"<h1>Screenshot fixture</h1>{divs}</body></html>"
    )


STATE_PAGES = {
    "/": (
        "<!doctype html><html><head><title>State Fixture</title></head><body>"
        "<h1>State fixture</h1><button>State action</button></body></html>"
    ),
    "/one": (
        "<!doctype html><html><head><title>Tab One</title></head><body>"
        "<h1>Tab one</h1><button>Tab One Action</button></body></html>"
    ),
    "/two": (
        "<!doctype html><html><head><title>Tab Two</title></head><body>"
        "<h1>Tab two</h1><button>Tab Two Action</button></body></html>"
    ),
    "/color": _color_page(),
}


# ---------------------------------------------------------------------------
# Decider wire protocol (OpenAI-compatible decider vs a local stub LLM)
# ---------------------------------------------------------------------------


class _StubLLMHandler(BaseHTTPRequestHandler):
    """A scripted /chat/completions endpoint. Queue items are action dicts
    (sent as the assistant message JSON) or raw strings (sent verbatim, to
    prove the decider rejects non-JSON replies honestly)."""

    queue: list = []
    seen: list = []

    def do_POST(self):  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw)
        except ValueError:
            body = {}
        type(self).seen.append(
            {"auth": self.headers.get("Authorization"), "body": body, "path": self.path}
        )
        item = (
            type(self).queue.pop(0)
            if type(self).queue
            else {"kind": "done", "summary": "stub queue empty"}
        )
        content = item if isinstance(item, str) else json.dumps(item)
        payload = {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def _start_stub(actions) -> tuple:
    _StubLLMHandler.queue = list(actions)
    _StubLLMHandler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubLLMHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def case_stub_decider_wire_e2e():
    server, base = _start_stub(
        [
            {"kind": "type", "target": 1, "text": "ghost"},
            {"kind": "click", "target": 2},
            {"kind": "done", "summary": "stub finished the run"},
        ]
    )
    try:
        decider = OpenAICompatibleDecider(
            base_url=base, api_key="wire-test-key", model="stub-1"
        )
        driver = FakeDriver(DEMO_PAGES)
        runner = Runner(driver, decider, trail=Trail())
        report = runner.run("search the demo web")
        assert report.stop_reason == "done", report.summary
        assert report.summary == "stub finished the run"
        assert driver.current_url() == "https://demo.local/results"
        seen = _StubLLMHandler.seen
        assert len(seen) == 3, f"expected 3 decider calls, got {len(seen)}"
        first = seen[0]
        assert first["path"] == "/chat/completions", first["path"]
        assert first["auth"] == "Bearer wire-test-key"
        assert first["body"]["model"] == "stub-1"
        user_msg = first["body"]["messages"][1]["content"]
        assert "Goal: search the demo web" in user_msg
        assert "Search the demo web" in user_msg  # the numbered map went over the wire
    finally:
        server.shutdown()
    return True


def case_stub_decider_env_gating():
    saved = {
        k: os.environ.get(k)
        for k in ("GHOST_HANDS_BASE_URL", "GHOST_HANDS_API_KEY", "GHOST_HANDS_MODEL")
    }
    try:
        os.environ.pop("GHOST_HANDS_BASE_URL", None)
        os.environ.pop("GHOST_HANDS_API_KEY", None)
        try:
            OpenAICompatibleDecider()
            raise AssertionError("expected HandsError with no env at all")
        except HandsError as exc:
            assert "GHOST_HANDS_BASE_URL" in str(exc)
        os.environ["GHOST_HANDS_BASE_URL"] = "http://127.0.0.1:9"
        try:
            OpenAICompatibleDecider()
            raise AssertionError("expected HandsError with no key")
        except HandsError as exc:
            assert "GHOST_HANDS_API_KEY" in str(exc)
        os.environ["GHOST_HANDS_API_KEY"] = "env-key"
        decider = OpenAICompatibleDecider()
        assert decider.base_url == "http://127.0.0.1:9"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return True


def case_stub_decider_bad_reply():
    server, base = _start_stub(["this is not a JSON action"])
    try:
        decider = OpenAICompatibleDecider(
            base_url=base, api_key="wire-test-key", model="stub-1"
        )
        runner = Runner(FakeDriver(DEMO_PAGES), decider, trail=Trail())
        report = runner.run("anything")
        assert report.stop_reason == "error", report.summary
        assert "decider" in report.summary
    finally:
        server.shutdown()
    return True


# ---------------------------------------------------------------------------
# Self-healing targets (page mutates between perception and action)
# ---------------------------------------------------------------------------

HEAL_URL = "https://heal.local/todo"
HEAL_V1 = f"""<!doctype html><html><head><title>Heal Todo</title></head>
<body>
<h1>Heal Todo</h1>
<input id="new-item" name="new-item" type="text" placeholder="New item">
<button data-append-to="items" data-from-field="new-item">Add item</button>
<ul id="items"><li>Existing item</li></ul>
</body></html>"""
HEAL_V2 = f"""<!doctype html><html><head><title>Heal Todo</title></head>
<body>
<h1>Heal Todo</h1>
<button>Dismiss banner</button>
<input id="new-item" name="new-item" type="text" placeholder="New item">
<button data-append-to="items" data-from-field="new-item">Add item</button>
<ul id="items"><li>Existing item</li></ul>
</body></html>"""
HEAL_GONE = """<!doctype html><html><head><title>Heal Todo</title></head>
<body><h1>Everything changed</h1><p>No form here anymore.</p></body></html>"""


def case_heal_descriptor_lookup():
    m = ElementMap.from_html(HEAL_V2)
    el = m.find_by_descriptor({"tag": "input", "role": "textbox", "name": "New item"})
    assert el is not None and el.number == 2
    btn = m.find_by_descriptor({"tag": "button", "role": "button", "name": "Add item"})
    assert btn is not None and btn.number == 3
    assert m.find_by_descriptor({"tag": "input", "role": "textbox", "name": "Nope"}) is None


def case_heal_type_after_mutation():
    driver = FakeDriver({HEAL_URL: HEAL_V1}, start_url=HEAL_URL)
    driver.mutate_hook = lambda d: d.pages.__setitem__(HEAL_URL, HEAL_V2)
    trail = Trail()
    runner = Runner(
        driver,
        ScriptedDecider(
            [
                {"kind": "type", "target": 1, "text": "healed task"},
                {"kind": "done", "summary": "typed"},
            ]
        ),
        trail=trail,
    )
    report = runner.run("heal bench: type")
    assert report.stop_reason == "done", report.summary
    assert driver.fields.get("new-item") == "healed task", driver.fields
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1, heals
    assert heals[0]["found"] is True
    assert heals[0]["from_target"] == 1 and heals[0]["to_target"] == 2


def case_heal_click_after_mutation():
    driver = FakeDriver({HEAL_URL: HEAL_V1}, start_url=HEAL_URL)
    driver.fields["new-item"] = "seeded item"
    driver.mutate_hook = lambda d: d.pages.__setitem__(HEAL_URL, HEAL_V2)
    trail = Trail()
    runner = Runner(
        driver,
        ScriptedDecider(
            [
                {"kind": "click", "target": 2},
                {"kind": "done", "summary": "clicked"},
            ]
        ),
        trail=trail,
    )
    report = runner.run("heal bench: click")
    assert report.stop_reason == "done", report.summary
    assert "seeded item" in driver.snapshot_html()
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1 and heals[0]["found"] is True
    assert heals[0]["from_target"] == 2 and heals[0]["to_target"] == 3


def case_heal_gone_element():
    driver = FakeDriver({HEAL_URL: HEAL_V1}, start_url=HEAL_URL)
    driver.mutate_hook = lambda d: d.pages.__setitem__(HEAL_URL, HEAL_GONE)
    trail = Trail()
    runner = Runner(
        driver,
        ScriptedDecider(
            [
                {"kind": "type", "target": 1, "text": "nowhere to go"},
                {"kind": "done", "summary": "typed"},
            ]
        ),
        trail=trail,
    )
    report = runner.run("heal bench: gone")
    assert report.stop_reason == "error", report.summary
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1 and heals[0]["found"] is False


# ---------------------------------------------------------------------------
# Proxy resolution (plain passthrough / authenticated relay / disabled)
# ---------------------------------------------------------------------------


def case_proxy_plain_arg():
    d = ChromiumDriver(proxy="http://proxy.local:8080")
    assert d._proxy_launch_arg() == "http://proxy.local:8080"
    assert d._relay is None


def case_proxy_auth_uses_relay():
    d = ChromiumDriver(proxy="http://user:secret@proxy.local:3128")
    try:
        arg = d._proxy_launch_arg()
        assert arg is not None and arg.startswith("http://127.0.0.1:"), arg
        assert "secret" not in arg and "proxy.local" not in arg
        assert d._relay is not None
    finally:
        if d._relay is not None:
            d._relay.stop()


def case_proxy_disabled():
    d = ChromiumDriver(proxy="")
    assert d._proxy_launch_arg() is None
    assert d._relay is None


# ---------------------------------------------------------------------------
# ChromiumDriver state: session roundtrip, tabs, screenshot file
# ---------------------------------------------------------------------------


def case_chromium_session_roundtrip():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    server, base = _serve_pages(STATE_PAGES)
    path = os.path.join(tempfile.mkdtemp(), "session.json")
    try:
        d1 = ChromiumDriver(chrome_path=chrome)
        try:
            d1.open(base + "/")
            d1.set_cookie("gh_session", "abc123")
            d1.set_local_storage({"gh_key": "gh_value"})
            d1.save_session(path)
        finally:
            d1.close()
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
        assert saved["tool"] == "ghost-hands" and saved["version"] == 1
        assert any(c["name"] == "gh_session" for c in saved["cookies"])
        d2 = ChromiumDriver(chrome_path=chrome)
        try:
            d2.load_session(path)
            assert d2.local_storage().get("gh_key") == "gh_value", d2.local_storage()
            cookies = {c["name"]: c["value"] for c in d2.get_cookies()}
            assert cookies.get("gh_session") == "abc123", cookies
        finally:
            d2.close()
    finally:
        server.shutdown()
    return True


def case_chromium_tabs():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    server, base = _serve_pages(STATE_PAGES)
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(base + "/one")
        m = ElementMap.from_html(driver.snapshot_html(), url=driver.current_url())
        assert m.find_by_text("Tab One Action") is not None
        idx = driver.open_tab(base + "/two")
        assert idx == 1
        tabs = driver.list_tabs()
        assert len(tabs) == 2 and tabs[1]["active"] is True
        m = ElementMap.from_html(driver.snapshot_html(), url=driver.current_url())
        assert m.find_by_text("Tab Two Action") is not None
        assert m.find_by_text("Tab One Action") is None
        driver.switch_tab(tabs[0]["target_id"])
        m = ElementMap.from_html(driver.snapshot_html(), url=driver.current_url())
        assert m.find_by_text("Tab One Action") is not None
        driver.switch_tab(1)
        m = ElementMap.from_html(driver.snapshot_html(), url=driver.current_url())
        assert m.find_by_text("Tab Two Action") is not None
    finally:
        driver.close()
        server.shutdown()
    return True


def case_chromium_screenshot_file():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    server, base = _serve_pages(STATE_PAGES)
    path = os.path.join(tempfile.mkdtemp(), "shot.png")
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(base + "/color")
        result = driver.act(A.screenshot(path=path), None)
        assert "saved" in result, result
        raw = Path(path).read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
        assert len(raw) > 1024, f"PNG suspiciously small: {len(raw)} bytes"
        assert f"({len(raw)} bytes)" in result
    finally:
        driver.close()
        server.shutdown()
    return True


# ---------------------------------------------------------------------------
# Export execution proof: a graduated script, actually executed on Chromium
# ---------------------------------------------------------------------------


def _export_fixture(base: str) -> dict:
    return {
        base + "/": f"""<!doctype html><html><head><title>Export Home</title></head>
<body>
<h1>Export fixture home</h1>
<input id="q" name="q" type="text" placeholder="Search the fixture">
<a href="{base}/results">Search</a>
</body></html>""",
        base + "/results": f"""<!doctype html><html><head><title>Export Results</title></head>
<body>
<h1>Results</h1>
<a href="{base}/article">Fixture Article</a>
<a href="{base}/">Back home</a>
</body></html>""",
        base + "/article": f"""<!doctype html><html><head><title>Fixture Article</title></head>
<body>
<h1>Fixture Article</h1>
<p>Graduated scripts run here.</p>
<a href="{base}/todo">Try the todo list</a>
</body></html>""",
        base + "/todo": """<!doctype html><html><head><title>Export Todo</title></head>
<body>
<h1>Export Todo</h1>
<input id="new-item" name="new-item" type="text" placeholder="New item">
<button data-append-to="items" data-from-field="new-item" onclick="ghAdd()">Add item</button>
<ul id="items"><li>Existing item</li></ul>
<script>function ghAdd(){var v=document.getElementById('new-item').value;if(v){var li=document.createElement('li');li.textContent=v;document.getElementById('items').appendChild(li);}}</script>
</body></html>""",
    }


def case_export_executed_on_chromium():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    server_pages: dict = {}
    server, base = _serve_pages(server_pages)
    try:
        pages = _export_fixture(base)
        for url, html in pages.items():
            server_pages[url[len(base):] or "/"] = html
        steps = [
            {"kind": "type", "target": 1, "text": "ghost"},
            {"kind": "click", "target": 2},
            {"kind": "click", "target": 1},
            {"kind": "extract"},
            {"kind": "navigate", "url": base + "/todo"},
            {"kind": "type", "target": 1, "text": "Ship Ghost Hands"},
            {"kind": "click", "target": 2},
            {"kind": "extract"},
            {"kind": "done", "summary": "export fixture recorded"},
        ]
        driver = FakeDriver(pages, start_url=base + "/")
        trail = Trail()
        report = Runner(driver, ScriptedDecider(steps), trail=trail).run("export proof")
        assert report.stop_reason == "done", report.summary

        src = export_ghost_hands_script(
            trail.events, driver="chromium", start_url=base + "/"
        )
        script_path = os.path.join(tempfile.mkdtemp(), "replay.py")
        Path(script_path).write_text(src, encoding="utf-8")

        import ghost_hands

        src_dir = str(Path(ghost_hands.__file__).resolve().parent.parent)
        env = {
            k: v for k, v in os.environ.items() if not k.startswith("GHOST_HANDS_")
        }
        env["PYTHONPATH"] = src_dir
        env["GHOST_HANDS_CHROME"] = chrome
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        assert proc.returncode == 0, f"replay failed: {proc.stderr[-800:]}"
        assert "done" in proc.stdout and "script complete" in proc.stdout, proc.stdout
    finally:
        server.shutdown()
    return True


# ---------------------------------------------------------------------------
# v0.3 capabilities
# ---------------------------------------------------------------------------

V3_FORM_PAGE = """<!doctype html><html><head><title>Profile</title></head><body>
<form action="{done}">
<label for="fn">Full name</label>
<input id="fn" name="fullname" type="text">
<label>Email address <input name="email" type="email"></label>
<input name="city" type="text" placeholder="City">
<label for="plan">Plan</label>
<select id="plan" name="plan"><option>Free</option><option>Pro</option></select>
<button type="submit">Save profile</button>
</form>
</body></html>"""

V3_FILL_FIELDS = {
    "Full name": "Ryan Cotten",
    "Email": "ryan@example.com",
    "City": "Oakdale",
    "Plan": "Pro",
    "No such field": "x",
}


def case_v3_classifier_pack():
    # download and raw-coordinate clicks are writes; fill_form is a write
    # that turns consequential when it submits; set_file is a write;
    # pdf / set_viewport / hover stay readonly.
    assert classify(A.download(url="https://x.local/f.bin")) == "write"
    assert classify(A.click_at(5, 5)) == "write"
    assert classify(A.fill_form({"Name": "R"})) == "write"
    assert classify(A.fill_form({"Name": "R"}, submit=True)) == "consequential"
    _, file_el = _el('<input type="file" name="attachment">', "attachment")
    assert classify(A.set_file(file_el.number, "/tmp/a.txt"), file_el) == "write"
    assert classify(A.pdf()) == "readonly"
    assert classify(A.set_viewport("mobile")) == "readonly"
    _, menu_el = _el("<button>Menu</button>", "Menu")
    assert classify(A.hover(menu_el.number), menu_el) == "readonly"
    d = Governor().evaluate(A.fill_form({"Name": "R"}, submit=True))
    assert d.outcome == "ask" and d.classification == "consequential"


def case_v3_fake_fill_form():
    pages = {
        "https://form.local/": V3_FORM_PAGE.format(done="https://form.local/done"),
        "https://form.local/done": "<html><body><h1>Saved</h1></body></html>",
    }
    steps = [
        {"kind": "fill_form", "fields": dict(V3_FILL_FIELDS)},
        {"kind": "done", "summary": "filled"},
    ]
    driver, _, report = _run(pages, steps)
    assert report.stop_reason == "done", report.summary
    result = report.results[0]
    assert "filled 4/5 fields" in result, result
    assert "'No such field' FAILED" in result, result
    assert driver.fields.get("fullname") == "Ryan Cotten"
    assert driver.fields.get("plan") == "Pro"
    # submit=True with no approver is denied before anything runs.
    steps[0] = {"kind": "fill_form", "fields": {"City": "Oakdale"}, "submit": True}
    driver, _, report = _run(pages, steps)
    assert report.stop_reason == "denied", report.summary
    assert driver.submissions == []


def case_v3_fake_extract_structures():
    page = (
        "<html><body>"
        '<a href="/a">Alpha</a><a href="/b">Beta</a>'
        "<table><tr><th>N</th></tr><tr><td>1</td></tr><tr><td>2</td></tr></table>"
        "</body></html>"
    )
    driver = FakeDriver({"https://x.local/": page}, start_url="https://x.local/")
    assert json.loads(driver.act(A.extract(mode="list"), None)) == [
        {"text": "Alpha", "href": "/a"},
        {"text": "Beta", "href": "/b"},
    ]
    assert json.loads(driver.act(A.extract(mode="table"), None)) == [{"N": "1"}, {"N": "2"}]
    net = json.loads(driver.act(A.extract(mode="network"), None))
    assert net and net[0]["status"] == 200


BASIC_FIXTURE = (
    "<!doctype html><html><head><title>Basic</title></head><body>"
    '<input id="q" type="text" placeholder="Search the fixture">'
    '<button id="go" onclick="document.body.setAttribute('
    "'data-clicked','yes')\">Go now</button>"
    "</body></html>"
)


def case_v3_ax_eyes():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    driver = ChromiumDriver(chrome_path=chrome, eyes="ax")
    try:
        driver.open(_data_url(BASIC_FIXTURE))
        m = driver.perceive()
        box = m.find_by_text("Search the fixture")
        btn = m.find_by_text("Go now")
        assert box is not None and btn is not None, [e.name for e in m.elements]
        assert btn.backend_id is not None
        driver.act(A.type(box.number, "ax typed"), box)
        assert driver.act(A.extract(box.number), box) == "ax typed"
        driver.act(A.click(btn.number), btn)
        assert 'data-clicked="yes"' in driver.snapshot_html()
    finally:
        driver.close()
    return True


def case_v3_shadow_dom():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    page = """<!doctype html><html><head><title>Shadow</title></head><body>
<my-widget></my-widget>
<script>
class W extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({mode: 'open'});
    root.innerHTML = '<button id="sb">Shadow Button</button>';
    root.querySelector('#sb').addEventListener('click', () => {
      document.body.setAttribute('data-shadow-clicked', 'yes');
    });
  }
}
customElements.define('my-widget', W);
</script>
</body></html>"""
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(_data_url(page))
        m = driver.perceive()
        btn = m.find_by_text("Shadow Button")
        assert btn is not None, [e.name for e in m.elements]
        snap_map = ElementMap.from_html(driver.snapshot_html())
        assert snap_map.find_by_text("Shadow Button") is None  # HTML alone is blind to it
        driver.act(A.click(btn.number), btn)
        assert 'data-shadow-clicked="yes"' in driver.snapshot_html()
    finally:
        driver.close()
    return True


def case_v3_iframe():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    inner = (
        '<input id="inner" type="text" placeholder="Inner field">'
        '<button onclick="parent.document.body.setAttribute('
        "'data-inner','clicked')\">Inner Go</button>"
    )
    page = (
        "<!doctype html><html><head><title>Frames</title></head><body>"
        f'<iframe id="f1" srcdoc="{html_lib.escape(inner, quote=True)}"></iframe>'
        "</body></html>"
    )
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(_data_url(page))
        m = driver.perceive()
        box = m.find_by_text("Inner field")
        assert box is not None, [e.name for e in m.elements]
        assert box.frame == "f1", box.descriptor()
        driver.act(A.type(box.number, "inside the frame"), box)
        assert driver.act(A.extract(box.number), box) == "inside the frame"
        btn = m.find_by_text("Inner Go")
        driver.act(A.click(btn.number), btn)
        assert 'data-inner="clicked"' in driver.snapshot_html()
    finally:
        driver.close()
    return True


def case_v3_dialogs():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    page = (
        "<!doctype html><html><head><title>Dialog</title></head><body>"
        '<button id="ask" onclick="var r = confirm(\'Proceed?\');'
        "document.body.setAttribute('data-dialog', r ? 'confirmed' : 'cancelled');"
        '">Ask me</button>'
        "</body></html>"
    )
    for policy, expected in (("dismiss", "cancelled"), ("accept", "confirmed")):
        driver = ChromiumDriver(chrome_path=chrome, dialog_policy=policy)
        trail = Trail()
        try:
            steps = [
                {"kind": "click", "target": 1},
                {"kind": "done", "summary": "handled"},
            ]
            runner = Runner(
                driver, ScriptedDecider(steps), trail=trail, start_url=_data_url(page)
            )
            report = runner.run("dialog bench")
            assert report.stop_reason == "done", report.summary
            assert f'data-dialog="{expected}"' in driver.snapshot_html()
            dialogs = [e for e in trail.events if e["type"] == "dialog"]
            assert len(dialogs) == 1, [e["type"] for e in trail.events]
            assert dialogs[0]["dialog_type"] == "confirm"
            want = "accepted" if policy == "accept" else "dismissed"
            assert dialogs[0]["decision"] == want
        finally:
            driver.close()
    return True


def case_v3_download():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    payload = bytes(range(256)) * 8
    pages = {
        "/": (
            "<!doctype html><html><head><title>DL</title></head><body>"
            '<a href="/fixture.bin" download>Get the file</a>'
            "</body></html>"
        ),
        "/fixture.bin": (
            payload,
            "application/octet-stream",
            {"Content-Disposition": 'attachment; filename="fixture.bin"'},
        ),
    }
    server, base = _serve_pages(pages)
    dl_dir = tempfile.mkdtemp()
    driver = ChromiumDriver(chrome_path=chrome, download_dir=dl_dir)
    trail = Trail()
    try:
        steps = [
            {"kind": "download", "target": 1},
            {"kind": "done", "summary": "got it"},
        ]
        runner = Runner(
            driver, ScriptedDecider(steps), trail=trail, start_url=base + "/"
        )
        report = runner.run("download bench")
        assert report.stop_reason == "done", report.summary
        assert "fixture.bin (2048 bytes)" in report.results[0], report.results
        landed = Path(dl_dir) / "fixture.bin"
        assert landed.is_file() and landed.stat().st_size == 2048
        events = [e for e in trail.events if e["type"] == "download"]
        assert len(events) == 1 and events[0]["bytes"] == 2048, events
    finally:
        driver.close()
        server.shutdown()
    return True


def case_v3_upload():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    page = (
        "<!doctype html><html><head><title>Upload</title></head><body>"
        '<input type="file" id="up" name="attachment">'
        "<script>document.getElementById('up').addEventListener('change', (e) => {"
        "const f = e.target.files[0];"
        "document.body.setAttribute('data-file', f.name + ':' + f.size);});</script>"
        "</body></html>"
    )
    upload = Path(tempfile.mkdtemp()) / "hello.txt"
    upload.write_bytes(b"hands up" * 10)
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(_data_url(page))
        m = driver.perceive()
        out = driver.act(A.set_file(m.elements[0].number, str(upload)), m.elements[0])
        assert "hello.txt (80 bytes)" in out
        assert 'data-file="hello.txt:80"' in driver.snapshot_html()
    finally:
        driver.close()
    return True


def case_v3_network_capture():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    pages = {
        "/": (
            "<!doctype html><html><head><title>Net</title></head><body>"
            '<img src="/pixel.png">'
            "<script>fetch('/api.json').then(r => r.json()).then(() => {"
            "document.body.setAttribute('data-fetched', 'yes');});</script>"
            "</body></html>"
        ),
        "/pixel.png": (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png", {}),
        "/api.json": (json.dumps({"ok": True}), "application/json", {}),
    }
    server, base = _serve_pages(pages)
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(base + "/")
        deadline = time.time() + 5
        while time.time() < deadline:
            if any(e["url"].endswith("/api.json") for e in driver.network_log):
                break
            time.sleep(0.1)
        by_name = {e["url"].rsplit("/", 1)[-1]: e for e in driver.network_log}
        assert by_name["api.json"]["status"] == 200, driver.network_log
        assert by_name["pixel.png"]["status"] == 200
        parsed = json.loads(driver.act(A.extract(mode="network"), None))
        assert any(e["url"].endswith("/api.json") and e["status"] == 200 for e in parsed)
    finally:
        driver.close()
        server.shutdown()
    return True


def case_v3_fill_form_chromium():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    server, base = _serve_pages(
        {
            "/": V3_FORM_PAGE.format(done="/done"),
            "/done": "<html><body><h1>Saved</h1></body></html>",
        }
    )
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(base + "/")
        out = driver.act(A.fill_form(dict(V3_FILL_FIELDS)), None)
        assert "filled 4/5 fields" in out, out
        assert "'No such field' FAILED" in out, out
        values = {}
        for el in driver.perceive().elements:
            if el.attr_name:
                values[el.attr_name] = driver.act(A.extract(el.number), el)
        assert values.get("fullname") == "Ryan Cotten", values
        assert values.get("email") == "ryan@example.com", values
        assert values.get("city") == "Oakdale", values
        assert values.get("plan") == "Pro", values
    finally:
        driver.close()
        server.shutdown()
    return True


def case_v3_extract_structures_chromium():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    page = """<!doctype html><html><head><title>Data</title></head><body>
<ul>
<li><a href="/alpha">Alpha</a></li>
<li><a href="/beta">Beta</a></li>
<li><a href="/gamma">Gamma</a></li>
</ul>
<table>
<tr><th>Name</th><th>City</th><th>Zip</th></tr>
<tr><td>Alpha</td><td>Oakdale</td><td>37829</td></tr>
<tr><td>Beta</td><td>Harriman</td><td>37748</td></tr>
<tr><td>Gamma</td><td>Kingston</td><td>37763</td></tr>
</table>
</body></html>"""
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(_data_url(page))
        assert json.loads(driver.act(A.extract(mode="list"), None)) == [
            {"text": "Alpha", "href": "/alpha"},
            {"text": "Beta", "href": "/beta"},
            {"text": "Gamma", "href": "/gamma"},
        ]
        assert json.loads(driver.act(A.extract(mode="table"), None)) == [
            {"Name": "Alpha", "City": "Oakdale", "Zip": "37829"},
            {"Name": "Beta", "City": "Harriman", "Zip": "37748"},
            {"Name": "Gamma", "City": "Kingston", "Zip": "37763"},
        ]
    finally:
        driver.close()
    return True


def case_v3_hover_reveal():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    page = """<!doctype html><html><head><title>Hover</title>
<style>
#menu .item { display: none; }
#menu:hover .item { display: block; }
</style></head><body>
<div id="menu">
<button id="top">Menu</button>
<button class="item" id="sub" onclick="document.body.setAttribute('data-sub','clicked')">Sub action</button>
</div>
</body></html>"""
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(_data_url(page))
        m = driver.perceive()
        assert m.find_by_text("Sub action") is None
        top = m.find_by_text("Menu")
        driver.act(A.hover(top.number), top)
        m2 = driver.perceive()
        sub = m2.find_by_text("Sub action")
        assert sub is not None, [e.name for e in m2.elements]
        driver.act(A.click(sub.number), sub)
        assert 'data-sub="clicked"' in driver.snapshot_html()
    finally:
        driver.close()
    return True


def case_v3_drag():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    page = """<!doctype html><html><head><title>Drag</title></head>
<body style="margin:0">
<button id="drag" style="width:80px;height:44px">Drag me</button>
<div style="height:120px"></div>
<button id="drop" style="width:140px;height:80px">Drop here</button>
<script>
let dragging = false;
const dragEl = document.getElementById('drag'), dropEl = document.getElementById('drop');
dragEl.addEventListener('mousedown', () => { dragging = true; });
window.addEventListener('mouseup', (e) => {
  if (!dragging) return;
  const r = dropEl.getBoundingClientRect();
  if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
    document.body.setAttribute('data-dropped', 'yes');
  }
  dragging = false;
});
</script>
</body></html>"""
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(_data_url(page))
        m = driver.perceive()
        src = m.find_by_text("Drag me")
        dst = m.find_by_text("Drop here")
        driver.act(A.drag(src.number, dst.number), src)
        assert 'data-dropped="yes"' in driver.snapshot_html()
    finally:
        driver.close()
    return True


def case_v3_input_extras():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    page = """<!doctype html><html><head><title>Input</title></head><body>
<input id="t" type="text" placeholder="Text here">
<button id="d" ondblclick="document.body.setAttribute('data-dbl','yes')">Double</button>
<button id="r" oncontextmenu="event.preventDefault(); document.body.setAttribute('data-rc','yes')">Right</button>
</body></html>"""
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(_data_url(page))
        m = driver.perceive()
        box = m.find_by_text("Text here")
        driver.act(A.type(box.number, "hello world"), box)
        driver.act(A.press("Control+a"), None)
        driver.act(A.type(box.number, "replaced"), box)
        assert driver.act(A.extract(box.number), box) == "replaced"
        d = m.find_by_text("Double")
        r = m.find_by_text("Right")
        driver.act(A.double_click(d.number), d)
        driver.act(A.right_click(r.number), r)
        snap = driver.snapshot_html()
        assert 'data-dbl="yes"' in snap and 'data-rc="yes"' in snap
    finally:
        driver.close()
    return True


def case_v3_pdf():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    server, base = _serve_pages(STATE_PAGES)
    out_path = Path(tempfile.mkdtemp()) / "page.pdf"
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(base + "/color")
        result = driver.act(A.pdf(str(out_path)), None)
        raw = out_path.read_bytes()
        assert raw[:4] == b"%PDF", raw[:16]
        assert len(raw) > 1024, len(raw)
        assert f"({len(raw)} bytes)" in result
    finally:
        driver.close()
        server.shutdown()
    return True


def case_v3_viewport():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    page = """<!doctype html><html><head><title>Viewport</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
.mobile-only { display: none; }
@media (max-width: 600px) {
  .mobile-only { display: block; }
  .desktop-only { display: none; }
}
</style></head><body>
<button class="desktop-only">Desktop CTA</button>
<button class="mobile-only">Mobile CTA</button>
</body></html>"""
    driver = ChromiumDriver(chrome_path=chrome)
    try:
        driver.open(_data_url(page))
        names = [e.name for e in driver.perceive().elements]
        assert "Desktop CTA" in names and "Mobile CTA" not in names, names
        driver.act(A.set_viewport("mobile"), None)
        names = [e.name for e in driver.perceive().elements]
        assert "Mobile CTA" in names and "Desktop CTA" not in names, names
    finally:
        driver.close()
    return True


def case_v3_wait_for():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    page = """<!doctype html><html><head><title>Wait</title></head><body>
<div id="later"></div>
<script>
setTimeout(() => {
  const b = document.createElement('button');
  b.textContent = 'Loaded action';
  document.getElementById('later').appendChild(b);
}, 600);
</script>
</body></html>"""
    driver = ChromiumDriver(chrome_path=chrome)
    trail = Trail()
    try:
        driver.open(_data_url(page))
        out = driver.act(A.wait_for(text="Loaded action", timeout=5), None)
        assert "wait satisfied" in out, out
        # ...and a wait for the impossible stops honestly, on the record.
        steps = [
            {"kind": "wait", "text": "Ghost text that never comes", "seconds": 1},
            {"kind": "done", "summary": "unreached"},
        ]
        runner = Runner(driver, ScriptedDecider(steps), trail=trail)
        report = runner.run("wait bench")
        assert report.stop_reason == "error", report.summary
        failures = [e for e in trail.events if e["type"] == "result" and not e["ok"]]
        assert failures and "wait timed out" in failures[0]["result"]
    finally:
        driver.close()
    return True


def case_v3_click_at_raw():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    page = """<!doctype html><html><head><title>Pad</title></head>
<body style="margin:0">
<div id="pad" style="position:absolute;left:0;top:0;width:400px;height:300px;background:#222"
     onclick="document.body.setAttribute('data-click', event.clientX + ',' + event.clientY)"></div>
</body></html>"""
    driver = ChromiumDriver(chrome_path=chrome)
    trail = Trail()
    try:
        steps = [
            {"kind": "click_at", "x": 120, "y": 90},
            {"kind": "done", "summary": "padded"},
        ]
        runner = Runner(
            driver, ScriptedDecider(steps), trail=trail, start_url=_data_url(page)
        )
        report = runner.run("raw click bench")
        assert report.stop_reason == "done", report.summary
        snap = driver.snapshot_html()
        assert 'data-click="' in snap
        coords = snap.split('data-click="', 1)[1].split('"', 1)[0]
        cx, cy = (int(v) for v in coords.split(","))
        assert abs(cx - 120) <= 2 and abs(cy - 90) <= 2, coords
        executes = [e for e in trail.events if e["type"] == "execute"]
        assert executes[0].get("raw") is True
    finally:
        driver.close()
    return True


def case_v3_mcp_fill_form():
    s = HandsSession()
    resp = handle_request(
        s,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "hands_perceive",
                "arguments": {
                    "pages": {
                        "https://form.local/": V3_FORM_PAGE.format(done="/done")
                    },
                    "url": "https://form.local/",
                },
            },
        },
    )
    assert "Save profile" in resp["result"]["content"][0]["text"]
    resp = handle_request(
        s,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "hands_act",
                "arguments": {
                    "action": {
                        "kind": "fill_form",
                        "fields": {"Full name": "Ryan Cotten", "City": "Oakdale"},
                    }
                },
            },
        },
    )
    text = resp["result"]["content"][0]["text"]
    assert "filled 2/2 fields" in text, text
    assert s.driver.fields.get("fullname") == "Ryan Cotten"
    assert s.driver.fields.get("city") == "Oakdale"


# ---------------------------------------------------------------------------
# LIVE cases (real network) — only with `ghost-hands bench --live`.
# A network that cannot be reached SKIPs with the reason; a reached page
# that fails an assertion is a genuine FAIL.
# ---------------------------------------------------------------------------


def case_live_example_com():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    driver = ChromiumDriver(chrome_path=chrome, ignore_cert_errors=True)
    try:
        try:
            driver.open("https://example.com")
        except HandsError as exc:
            return (SKIP, f"live network unavailable: {exc}")
        if "chrome-error" in driver.current_url():
            return (SKIP, "live network unavailable: browser error page")
        m = ElementMap.from_html(driver.snapshot_html(), url=driver.current_url())
        assert m.title == "Example Domain", f"unexpected title: {m.title!r}"
        # example.com's link to IANA: historically labelled "More
        # information"; the page as served in Oct 2026 labels it
        # "Learn more". Match the label first, then the IANA href.
        link = m.find_by_text("More information") or m.find_by_text("Learn more")
        if link is None:
            link = next(
                (e for e in m.elements if e.href and "iana.org" in e.href), None
            )
        assert link is not None, "no link to IANA in the map"
        driver.act(A.click(link.number), link)
        deadline = time.time() + 25
        while time.time() < deadline and "iana.org" not in driver.current_url():
            time.sleep(0.5)
        assert "iana.org" in driver.current_url(), driver.current_url()
        html = driver.snapshot_html()
        assert "Example Domains" in html, "IANA example-domains page did not load"
    finally:
        driver.close()
    return True


def case_live_wikipedia_search():
    chrome = find_chrome()
    if not chrome:
        return (SKIP, "no Chromium binary found")
    driver = ChromiumDriver(chrome_path=chrome, ignore_cert_errors=True)
    try:
        try:
            driver.open("https://en.wikipedia.org")
        except HandsError as exc:
            return (SKIP, f"live network unavailable: {exc}")
        if "chrome-error" in driver.current_url():
            return (SKIP, "live network unavailable: browser error page")
        runner = Runner(
            driver,
            RuleDecider(),
            approver=lambda a: True,  # a search submit is consequential-class; approved for this task
            trail=Trail(),
            step_budget=12,
        )
        report = runner.run("search for Oakdale, Tennessee")
        assert report.stop_reason == "done", report.summary
        html = driver.snapshot_html()
        assert "Oakdale" in html, "the results/article page never mentions Oakdale"
        m = ElementMap.from_html(html, url=driver.current_url())
        assert "Oakdale" in m.title, f"unexpected results title: {m.title!r}"
    finally:
        driver.close()
    return True


LIVE_CASES = [
    ("live: example.com — navigate, perceive, follow the link", case_live_example_com),
    ("live: wikipedia — RuleDecider searches 'Oakdale, Tennessee'", case_live_wikipedia_search),
]


# ---------------------------------------------------------------------------
# Bench runner
# ---------------------------------------------------------------------------

CASES = [
    ("map: button text parsed", case_map_button),
    ("map: link href captured", case_map_link_href),
    ("map: input placeholder is the name", case_map_input_placeholder),
    ("map: aria-label wins over text", case_map_aria_label),
    ("map: role=button div is interactive", case_map_role_button),
    ("map: onclick element is interactive", case_map_onclick),
    ("map: budget truncates with a note", case_map_budget_truncation),
    ("map: select + textarea included", case_map_select_textarea),
    ("classifier: plain link click is readonly", case_cls_link_readonly),
    ("classifier: form submit is consequential", case_cls_submit_consequential),
    ("classifier: delete-account click is consequential", case_cls_delete_consequential),
    ("classifier: typing in a field is write", case_cls_type_write),
    ("classifier: typing in a payment field is consequential", case_cls_type_payment_consequential),
    ("classifier: navigate is readonly", case_cls_navigate_readonly),
    ("classifier: navigate to a delete URL is consequential", case_cls_navigate_delete_consequential),
    ("classifier: extract/screenshot/scroll are readonly", case_cls_extract_screenshot_readonly),
    ("classifier: plain button click is write", case_cls_plain_button_write),
    ("governor: readonly allowed by default", case_gov_readonly_allowed),
    ("governor: consequential asks by default", case_gov_consequential_asks),
    ("governor: consequential denied when policy says deny", case_gov_consequential_denied_by_policy),
    ("governor: blocked domain denied", case_gov_blocked_domain),
    ("governor: allowlist excludes other domains", case_gov_allowlist_excludes),
    ("governor: write denied when policy says deny", case_gov_write_denied_by_policy),
    ("governor: navigate to blocked domain denied", case_gov_navigate_blocked_domain),
    ("e2e: demo search flow completes", case_e2e_search_flow),
    ("e2e: login form fills and submits", case_e2e_login_form),
    ("e2e: todo add appends the item", case_e2e_todo_add),
    ("e2e: checkout blocked without approval", case_e2e_checkout_blocked),
    ("e2e: checkout allowed with approver", case_e2e_checkout_allowed_with_approver),
    ("e2e: external domain denied by allowlist", case_e2e_external_domain_denied),
    ("stuck: repeated action detected", case_stuck_repeated_action),
    ("stuck: unchanged map detected", case_stuck_unchanged_map),
    ("accounting: steps and classes counted", case_accounting_counts),
    ("accounting: token estimate is chars/4", case_accounting_token_estimate),
    ("export: script is valid Python", case_export_parses),
    ("export: script encodes the same steps", case_export_encodes_steps),
    ("export: script is a Ghost Hands script", case_export_is_ghost_hands_script),
    ("mcp: initialize handshake", case_mcp_initialize),
    ("mcp: tools/list exposes the four tools", case_mcp_tools_list),
    ("mcp: perceive + act + trail", case_mcp_perceive_and_act),
    ("rules: 'go to' navigates", case_rule_go_to),
    ("rules: 'click <text>' clicks the match", case_rule_click),
    ("rules: 'type <text> into <field>'", case_rule_type_into),
    ("rules: 'search for <q>' end to end", case_rule_search_for),
    ("chromium: binary discovery", case_chrome_discovery),
    ("chromium: CDP framing is NUL-delimited JSON", case_cdp_framing),
    ("chromium: CDP buffer reassembles + matches ids", case_cdp_buffer_matching),
    ("chromium: LIVE smoke — perceive, type, click, observe", case_live_chrome_smoke),
    ("decider wire: stub LLM drives a full runner loop", case_stub_decider_wire_e2e),
    ("decider wire: env gating for the LLM decider", case_stub_decider_env_gating),
    ("decider wire: non-JSON reply stops the run honestly", case_stub_decider_bad_reply),
    ("heal: descriptor lookup re-finds a shifted element", case_heal_descriptor_lookup),
    ("heal: type lands after the page mutates", case_heal_type_after_mutation),
    ("heal: click lands after the page mutates", case_heal_click_after_mutation),
    ("heal: vanished element stops the run with an error", case_heal_gone_element),
    ("proxy: plain proxy passes through as an arg", case_proxy_plain_arg),
    ("proxy: authenticated proxy goes through the local relay", case_proxy_auth_uses_relay),
    ("proxy: empty proxy string disables proxying", case_proxy_disabled),
    ("state: session save/load roundtrip (cookies + localStorage)", case_chromium_session_roundtrip),
    ("state: tabs perceive their own pages", case_chromium_tabs),
    ("state: screenshot saves a real PNG", case_chromium_screenshot_file),
    ("export: graduated script executes on Chromium", case_export_executed_on_chromium),
    ("v3 classifier: new kinds (download/fill_form/set_file write, pdf/viewport/hover readonly)", case_v3_classifier_pack),
    ("v3 fake: fill_form fills, failures named, submit gated", case_v3_fake_fill_form),
    ("v3 fake: structured extract (list/table/network)", case_v3_fake_extract_structures),
    ("v3 ax eyes: AX-tree map matches DOM controls; type + click land", case_v3_ax_eyes),
    ("v3 shadow DOM: open shadow button perceived + clicked", case_v3_shadow_dom),
    ("v3 frames: type into an iframe input, read back, click through", case_v3_iframe),
    ("v3 dialogs: dismiss->cancelled, accept->confirmed, both on the trail", case_v3_dialogs),
    ("v3 downloads: file completes on disk, trail event, size reported", case_v3_download),
    ("v3 uploads: set_file lands the file in the page", case_v3_upload),
    ("v3 network: page requests captured with statuses; extract network", case_v3_network_capture),
    ("v3 fill_form on Chromium: 4 fields, one failure named, values read back", case_v3_fill_form_chromium),
    ("v3 extract on Chromium: link list + table, exact structures", case_v3_extract_structures_chromium),
    ("v3 hover: reveals the hidden menu item, then click lands", case_v3_hover_reveal),
    ("v3 drag: source dropped onto target (data attribute)", case_v3_drag),
    ("v3 input extras: Control+a chord replaces text; double/right click land", case_v3_input_extras),
    ("v3 pdf: real PDF written (%PDF magic, >1KB)", case_v3_pdf),
    ("v3 viewport: media-query layout flips desktop->mobile", case_v3_viewport),
    ("v3 wait_for: delayed element caught; impossible wait times out on the record", case_v3_wait_for),
    ("v3 click_at: raw coordinates land; trail marks raw:true", case_v3_click_at_raw),
    ("v3 mcp: fill_form over hands_act", case_v3_mcp_fill_form),
]


def _run_cases(cases, out) -> tuple[int, int]:
    passed = 0
    failed = 0
    for name, fn in cases:
        try:
            result = fn()
        except Exception as exc:
            failed += 1
            print(f"FAIL {name} — {type(exc).__name__}: {exc}", file=out)
            continue
        if isinstance(result, tuple) and result and result[0] == SKIP:
            passed += 1
            print(f"SKIP {name} — {result[1]}", file=out)
        elif result is False:
            failed += 1
            print(f"FAIL {name}", file=out)
        else:
            passed += 1
            print(f"PASS {name}", file=out)
    return passed, failed


def run_bench(out=None, live: bool = False) -> int:
    out = out or sys.stdout
    passed, failed = _run_cases(CASES, out)
    print(f"GHOST HANDS BENCH: {passed}/{passed + failed} passed", file=out)
    rc = 0 if failed == 0 else 1
    if live:
        print(
            "— live cases (real network; SKIP means the network could not be reached) —",
            file=out,
        )
        lp, lf = _run_cases(LIVE_CASES, out)
        print(f"GHOST HANDS BENCH (LIVE): {lp}/{lp + lf} passed", file=out)
        if lf:
            rc = 1
    return rc
