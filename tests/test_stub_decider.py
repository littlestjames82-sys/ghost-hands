"""Item 2 (v0.2): the OpenAI-compatible decider's wire protocol, proven
against a local stub chat-completions server — no external keys."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ghost_hands import HandsError
from ghost_hands.deciders import OpenAICompatibleDecider
from ghost_hands.drivers import DEMO_PAGES, FakeDriver
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail


class StubHandler(BaseHTTPRequestHandler):
    queue: list = []
    seen: list = []

    def do_POST(self):
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
            "choices": [
                {"message": {"role": "assistant", "content": content}}
            ]
        }
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def stub():
    StubHandler.queue = []
    StubHandler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", StubHandler
    server.shutdown()


def test_env_gating(monkeypatch):
    monkeypatch.delenv("GHOST_HANDS_BASE_URL", raising=False)
    monkeypatch.delenv("GHOST_HANDS_API_KEY", raising=False)
    with pytest.raises(HandsError, match="GHOST_HANDS_BASE_URL"):
        OpenAICompatibleDecider()
    monkeypatch.setenv("GHOST_HANDS_BASE_URL", "http://127.0.0.1:9")
    with pytest.raises(HandsError, match="GHOST_HANDS_API_KEY"):
        OpenAICompatibleDecider()
    monkeypatch.setenv("GHOST_HANDS_API_KEY", "k")
    assert OpenAICompatibleDecider().base_url == "http://127.0.0.1:9"


def test_stub_drives_full_runner_loop(stub):
    base, handler = stub
    handler.queue = [
        {"kind": "type", "target": 1, "text": "ghost"},
        {"kind": "click", "target": 2},
        {"kind": "done", "summary": "stub finished"},
    ]
    decider = OpenAICompatibleDecider(base_url=base, api_key="test-key", model="stub-1")
    driver = FakeDriver(DEMO_PAGES)
    trail = Trail()
    report = Runner(driver, decider, trail=trail).run("search the demo web")
    assert report.stop_reason == "done"
    assert report.summary == "stub finished"
    assert driver.current_url() == "https://demo.local/results"
    assert len(handler.seen) == 3
    assert [e["type"] for e in trail.events].count("decide") == 3


def test_request_shape(stub):
    base, handler = stub
    handler.queue = [{"kind": "done", "summary": "ok"}]
    decider = OpenAICompatibleDecider(base_url=base, api_key="sekret", model="stub-9")
    driver = FakeDriver(DEMO_PAGES)
    Runner(driver, decider, trail=Trail()).run("inspect the page")
    assert len(handler.seen) == 1
    req = handler.seen[0]
    assert req["path"] == "/chat/completions"
    assert req["auth"] == "Bearer sekret"
    body = req["body"]
    assert body["model"] == "stub-9"
    assert body["messages"][0]["role"] == "system"
    user = body["messages"][1]["content"]
    assert "Goal: inspect the page" in user
    assert "Search the demo web" in user  # the numbered element map went over the wire


def test_non_json_reply_is_an_honest_error(stub):
    base, handler = stub
    handler.queue = ["definitely not json"]
    decider = OpenAICompatibleDecider(base_url=base, api_key="k", model="stub-1")
    report = Runner(FakeDriver(DEMO_PAGES), decider, trail=Trail()).run("x")
    assert report.stop_reason == "error"
    assert "decider" in report.summary


def test_unreachable_endpoint_is_an_honest_error():
    decider = OpenAICompatibleDecider(
        base_url="http://127.0.0.1:1", api_key="k", model="m", timeout=2.0
    )
    report = Runner(FakeDriver(DEMO_PAGES), decider, trail=Trail()).run("x")
    assert report.stop_reason == "error"
