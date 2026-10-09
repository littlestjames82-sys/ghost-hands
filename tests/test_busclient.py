"""BusClient (v0.4) against an in-process stub HTTP server: URL
building (root vs /w/<id>/), auth headers, token storage, error
mapping, and request shapes. The real-server behavior is covered in
test_busagent.py."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ghost_hands.busclient import BusClient, BusError

REQUESTS = []


class StubHandler(BaseHTTPRequestHandler):
    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        REQUESTS.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": json.loads(raw) if raw else None,
            }
        )
        if "/boom" in self.path:
            payload, status = {"error": "task #9 cannot be claimed (status: done)"}, 400
        elif self.path.endswith("/api/agents") and self.command == "POST":
            payload, status = (
                {"agent": {"name": "ghost-hands"}, "rejoined": False, "token": "tok-123"},
                200,
            )
        elif "/api/wait" in self.path:
            payload, status = {"events": [], "headSeq": 7, "waited": True}, 200
        elif "/api/tasks" in self.path and self.command == "GET":
            payload, status = {"tasks": [{"id": 1, "status": "queued"}]}, 200
        else:
            payload, status = {"ok": True}, 200
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle

    def log_message(self, *args):
        pass


@pytest.fixture
def stub():
    REQUESTS.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _headers_lower(req):
    return {k.lower(): v for k, v in req["headers"].items()}


def test_single_workspace_urls_and_key_header(stub):
    client = BusClient(stub, key="sekret", agent_name="ghost-hands")
    client.list_tasks(status="queued")
    (req,) = REQUESTS
    assert req["path"].startswith("/api/tasks?")
    assert "status=queued" in req["path"]
    assert _headers_lower(req).get("x-bus-key") == "sekret"


def test_hosted_workspace_prefix(stub):
    client = BusClient(stub, workspace="studio", agent_name="ghost-hands")
    client.list_tasks()
    assert REQUESTS[0]["path"].startswith("/w/studio/api/tasks")


def test_base_url_with_prefix_used_verbatim(stub):
    client = BusClient(stub + "/w/already", agent_name="ghost-hands")
    client.list_tasks()
    assert REQUESTS[0]["path"].startswith("/w/already/api/tasks")


def test_register_stores_token_and_sends_it_after(stub):
    client = BusClient(stub, agent_name="ghost-hands")
    out = client.register(role="hands", capabilities=["click"])
    assert out["token"] == "tok-123"
    assert client.token == "tok-123"
    body = REQUESTS[0]["body"]
    assert body["agent"] == "ghost-hands"
    assert body["role"] == "hands"
    assert body["capabilities"] == ["click"]
    client.heartbeat()
    assert _headers_lower(REQUESTS[1]).get("x-agent-token") == "tok-123"


def test_error_maps_to_buserror_with_status(stub):
    client = BusClient(stub + "/boom", agent_name="ghost-hands")
    with pytest.raises(BusError) as excinfo:
        client.list_tasks()
    assert excinfo.value.status == 400
    assert "cannot be claimed" in str(excinfo.value)


def test_wait_events_params_and_shape(stub):
    client = BusClient(stub, agent_name="ghost-hands")
    events, head, waited = client.wait_events(since_seq=3, timeout=5)
    assert events == [] and head == 7 and waited is True
    assert "sinceSeq=3" in REQUESTS[0]["path"]
    assert "agent=ghost-hands" in REQUESTS[0]["path"]


def test_unreachable_bus_is_a_buserror():
    client = BusClient("http://127.0.0.1:1", agent_name="ghost-hands", timeout=2)
    with pytest.raises(BusError):
        client.health()


def test_key_and_token_are_never_in_urls(stub):
    client = BusClient(stub, key="sekret", agent_name="ghost-hands")
    client.register()
    client.list_tasks()
    for req in REQUESTS:
        assert "sekret" not in req["path"]
        assert "tok-123" not in req["path"]
