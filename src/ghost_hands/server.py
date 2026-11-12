"""HTTP JSON API (v1.9): `ghost-hands serve`.

A small stdlib-only HTTP surface so other tools can drive Ghost
Hands without shelling out or speaking MCP:

- ``GET  /health``        -> {"ok": true, "version": ...}
- ``POST /run``           -> run scripted steps under the Governor
                             and return the report + trail summary
- ``POST /verify-trail``  -> verify a trail's hash chain

Everything a run does over HTTP is governed exactly like a local
run — same Runner, same policy, same trail. Asks are denied (an
HTTP caller cannot answer a prompt) unless the server was started
with ``--approve-all``. When ``GHOST_HANDS_SERVE_TOKEN`` is set,
every request needs ``Authorization: Bearer <token>``.

The default body is the fake/demo web; ``--driver chromium``
serves a real browser instead. Runs are serialized (one body per
server) so trails and reports never interleave.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from . import __version__
from .actions import Action
from .deciders import ScriptedDecider
from .drivers import DEMO_PAGES, ChromiumDriver, FakeDriver
from .errors import HandsError
from .governor import Policy
from .report import summarize
from .runner import Runner
from .trail import Trail, verify_trail

MAX_BODY_BYTES = 1_000_000


def make_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    driver_kind: str = "fake",
    approve_all: bool = False,
    token: Optional[str] = None,
) -> ThreadingHTTPServer:
    token = (
        token
        if token is not None
        else (os.environ.get("GHOST_HANDS_SERVE_TOKEN") or "")
    )
    run_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep the console clean
            pass

        def _send(self, code: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self) -> bool:
            if not token:
                return True
            return self.headers.get("Authorization") == f"Bearer {token}"

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise HandsError(f"request body too large (> {MAX_BODY_BYTES})")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError) as exc:
                raise HandsError(f"request body is not valid JSON: {exc}") from exc
            if not isinstance(parsed, dict):
                raise HandsError("request body must be a JSON object")
            return parsed

        def do_GET(self):  # noqa: N802 - stdlib handler API
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            if self.path.rstrip("/") == "/health":
                return self._send(
                    200, {"ok": True, "version": __version__, "driver": driver_kind}
                )
            return self._send(404, {"error": f"no route for {self.path}"})

        def do_POST(self):  # noqa: N802 - stdlib handler API
            if not self._authorized():
                return self._send(401, {"error": "unauthorized"})
            try:
                body = self._body()
                if self.path.rstrip("/") == "/verify-trail":
                    return self._send(200, self._verify(body))
                if self.path.rstrip("/") == "/run":
                    with run_lock:
                        return self._send(200, self._run(body))
                return self._send(404, {"error": f"no route for {self.path}"})
            except HandsError as exc:
                return self._send(400, {"error": str(exc)})
            except Exception as exc:  # a handler bug must still answer JSON
                return self._send(500, {"error": f"internal: {exc}"})

        def _verify(self, body: dict) -> dict:
            events = body.get("events")
            if not isinstance(events, list):
                raise HandsError("'events' must be an array of trail events")
            return verify_trail(events)

        def _run(self, body: dict) -> dict:
            steps = body.get("steps")
            if not isinstance(steps, list) or not steps:
                raise HandsError("'steps' must be a non-empty array of actions")
            try:
                actions = [Action.from_dict(s) for s in steps]
            except Exception as exc:
                raise HandsError(f"invalid step: {exc}") from exc
            goal = str(body.get("goal") or "http run")
            pages = body.get("pages")
            start = body.get("start")
            if driver_kind == "chromium":
                driver = ChromiumDriver()
                start_url = start
            else:
                driver = FakeDriver(pages or DEMO_PAGES, start_url=start)
                start_url = None
            trail = Trail()
            runner = Runner(
                driver,
                ScriptedDecider([a.to_dict() for a in actions]),
                policy=Policy(),
                approver=(lambda action: True) if approve_all else None,
                trail=trail,
                start_url=start_url,
                require_verify=bool(body.get("require_verify")),
            )
            try:
                report = runner.run(goal)
            finally:
                driver.close()
            payload = {
                "report": report.to_dict(),
                "trail_summary": summarize(trail.events),
                "events": len(trail.events),
            }
            from .webhook import post_webhook

            post_webhook({"source": "http-run", "report": payload["report"]})
            return payload

    return ThreadingHTTPServer((host, port), Handler)


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    driver_kind: str = "fake",
    approve_all: bool = False,
) -> None:
    server = make_server(host, port, driver_kind, approve_all)
    print(
        f"ghost-hands serve: http://{host}:{port} "
        f"(driver={driver_kind}, approve_all={approve_all})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
