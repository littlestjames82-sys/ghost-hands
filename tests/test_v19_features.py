"""v1.9 features: the HTTP JSON API and the completion webhook."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from ghost_hands import server as server_mod
from ghost_hands import webhook
from ghost_hands.trail import Trail


def _serve(**kw):
    srv = server_mod.make_server(host="127.0.0.1", port=0, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _req(url, payload=None, token=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"}
        | ({"Authorization": f"Bearer {token}"} if token else {}),
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_http_health_run_and_verify():
    srv, base = _serve()
    try:
        status, health = _req(base + "/health")
        assert status == 200 and health["ok"] is True
        status, out = _req(
            base + "/run",
            {
                "goal": "search it",
                "steps": [
                    {"kind": "type", "target": 1, "text": "ghost"},
                    {"kind": "click", "target": 2},
                    {"kind": "done", "summary": "via http"},
                ],
            },
        )
        assert status == 200, out
        assert out["report"]["stop_reason"] == "done"
        assert out["trail_summary"]["chain"]["ok"] is True
        # verify-trail over the wire with a tampered chain
        trail = Trail()
        trail.record("perceive", 1, url="https://x.local/")
        trail.record("stop", 1, reason="done", summary="s")
        events = [dict(e) for e in trail.events]
        status, verdict = _req(base + "/verify-trail", {"events": events})
        assert status == 200 and verdict["ok"] is True
        events[0]["url"] = "https://evil.local/"
        status, verdict = _req(base + "/verify-trail", {"events": events})
        assert verdict["ok"] is False
    finally:
        srv.shutdown()


def test_http_token_required_when_configured():
    srv, base = _serve(token="s3cret-token")
    try:
        status, _ = _req(base + "/health")
        assert status == 401
        status, health = _req(base + "/health", token="s3cret-token")
        assert status == 200 and health["ok"] is True
    finally:
        srv.shutdown()


def test_http_run_rejects_bad_steps():
    srv, base = _serve()
    try:
        status, out = _req(base + "/run", {"steps": []})
        assert status == 400 and "steps" in out["error"]
        status, out = _req(base + "/nope", {})
        assert status == 404
    finally:
        srv.shutdown()


class _Sink:
    def __init__(self):
        self.payloads = []
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        sink = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                sink.payloads.append(json.loads(self.rfile.read(n)))
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"

    def stop(self):
        self.server.shutdown()


def test_webhook_posts_and_never_raises(monkeypatch):
    sink = _Sink()
    try:
        assert webhook.post_webhook({"a": 1}, url=sink.url) is True
        assert sink.payloads == [{"a": 1}]
    finally:
        sink.stop()
    # dead endpoint: False, not an exception
    assert webhook.post_webhook({"a": 1}, url="http://127.0.0.1:1/x") is False
    # unconfigured: False
    monkeypatch.delenv("GHOST_HANDS_WEBHOOK_URL", raising=False)
    assert webhook.post_webhook({"a": 1}) is False


def test_http_run_fires_webhook(monkeypatch):
    sink = _Sink()
    monkeypatch.setenv("GHOST_HANDS_WEBHOOK_URL", sink.url)
    srv, base = _serve()
    try:
        status, out = _req(
            base + "/run",
            {"steps": [{"kind": "done", "summary": "hook me"}]},
        )
        assert status == 200
        assert sink.payloads and sink.payloads[0]["source"] == "http-run"
        assert sink.payloads[0]["report"]["stop_reason"] == "done"
    finally:
        srv.shutdown()
        sink.stop()
