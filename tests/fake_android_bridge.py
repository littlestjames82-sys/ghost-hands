"""A fake MrGhosty Ghost Hands bridge for tests (NOT a test module).

Implements the exact HTTP/JSON contract of the Java bridge
(GhostHandsBridge in MrGhosty v1.6+, approvals v1.7+) — GET
/v1/status, POST /v1/perceive, POST /v1/act, POST /v1/approvals,
GET /v1/approvals/<id>, token auth via X-Ghost-Hands-Token — backed
by a small simulated Android state mirroring SimPhoneDriver's screens:
a launcher home, a Notes app (list → editor → saved note, plus a
consequential "Delete all notes"), and a Settings toggles app.

Element refs are child-index paths ("0/0", "0/1", ...) exactly like
the real bridge produces from its tree walk.

Approvals mirror the real bridge's structural rule: the wire can
CREATE an approval and READ its status, but there is NO decision
endpoint. Decisions are simulated by fixture-only hooks on the state
object (decide_approval / expire_approval) — Ryan's finger, not an
API — exactly as the phone's UI is the only decider on hardware.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "test-pairing-token-0123456789abcdef"

# A real 1x1 PNG, base64 — the fake bridge's "screenshot".
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

PACKAGES = {
    "home": "com.android.launcher3",
    "notes": "com.example.notes",
    "notes_edit": "com.example.notes",
    "settings": "com.android.settings",
    "app": "",
}


class FakeAndroidState:
    """The simulated phone behind the fake bridge."""

    def __init__(self, protocol=1, service_connected=True, api_level=34):
        self.protocol = protocol
        self.service_connected = service_connected
        self.api_level = api_level
        self.stack = ["home"]
        self.notes: list[str] = []
        self.draft = ""
        self.editing = None
        self.toggles = {"Wi-Fi": True, "Bluetooth": False}
        self.banner = False  # test hook: extra element atop the notes list
        self.foreground_package = PACKAGES["home"]
        self.seen_tokens: list = []
        # Approvals (v0.6 contract): id -> record. Raw POST bodies are
        # kept so tests can prove what did — and did NOT — cross the
        # wire (the redaction assertions read approval_payloads).
        self.approvals: dict = {}
        self.approval_payloads: list = []

    # -- approvals (store semantics mirror GhostHandsApprovals) --------

    def approval_view(self, rec) -> dict:
        """The wire view of one approval; a stale pending entry
        expires lazily on read, like the real store."""
        if (
            rec["status"] == "pending"
            and time.monotonic() - rec["created_mono"] > rec["timeout_seconds"]
        ):
            rec["status"] = "expired"
        out = {"ok": True, "id": rec["id"], "status": rec["status"]}
        if rec["status"] in ("approved", "denied"):
            out["decided_by"] = "phone"
            out["decided_at"] = rec["decided_at"]
        return out

    def pending_approval_ids(self) -> list:
        return [
            rid
            for rid, rec in self.approvals.items()
            if self.approval_view(rec)["status"] == "pending"
        ]

    # TEST HOOKS — Ryan's finger on the simulated phone. These are
    # methods on the fixture's state object, NOT HTTP endpoints: the
    # real bridge has no decision endpoint, and neither does this fake.

    def decide_approval(self, approval_id, approved) -> bool:
        rec = self.approvals.get(approval_id)
        if rec is None:
            return False
        self.approval_view(rec)  # a stale pending entry expires first
        if rec["status"] != "pending":
            return False
        rec["status"] = "approved" if approved else "denied"
        rec["decided_at"] = time.time()
        return True

    def expire_approval(self, approval_id) -> bool:
        rec = self.approvals.get(approval_id)
        if rec is None or rec["status"] != "pending":
            return False
        rec["status"] = "expired"
        return True

    @property
    def screen(self) -> str:
        return self.stack[-1]

    def elements(self) -> list[dict]:
        screen = self.screen
        items: list[dict] = []
        if screen == "home":
            items = [
                self._el("Button", "app_icon", "Notes", "app:notes"),
                self._el("Button", "app_icon", "Settings", "app:settings"),
            ]
        elif screen == "notes":
            if self.banner:
                items.append(self._el("TextView", "text", "Banner", "id:banner"))
            for i, note in enumerate(self.notes):
                items.append(
                    self._el("TextView", "note", note, f"note:item:{i}", value=note)
                )
            items.append(self._el("Button", "button", "New note", "note:new"))
            items.append(
                self._el("Button", "button", "Delete all notes", "note:delete-all")
            )
        elif screen == "notes_edit":
            items = [
                self._el(
                    "EditText", "text_field", "Note text", "field:note-text",
                    value=self.draft,
                ),
                self._el("Button", "button", "Save", "btn:save"),
            ]
        elif screen == "settings":
            for name, on in self.toggles.items():
                items.append(
                    self._el(
                        "Switch", "toggle", name, f"toggle:{name}",
                        checked=on,
                    )
                )
        # Assign child-index refs in document order, like the real walk.
        for i, item in enumerate(items):
            item["ref"] = f"0/{i}"
        return items

    def _el(self, tag, role, name, view_id, value=None, checked=None) -> dict:
        el = {
            "tag": tag,
            "role": role,
            "name": name,
            "id": f"{PACKAGES[self.screen]}:id/{view_id}",
            "bounds": [0, 100, 1080, 220],
            "attrs": {
                "package": PACKAGES[self.screen],
                "view_id": f"{PACKAGES[self.screen]}:id/{view_id}",
                "enabled": "true",
                "clickable": "true",
                "editable": "true" if role == "text_field" else "false",
                "scrollable": "false",
            },
        }
        if value is not None:
            el["value"] = value
        if checked is not None:
            el["attrs"]["checked"] = "true" if checked else "false"
        return el

    # -- acts ---------------------------------------------------------------

    def resolve(self, ref):
        for el in self.elements():
            if el["ref"] == ref:
                return el
        return None

    def apply_tap(self, el) -> str:
        vid = el["id"].rsplit("/", 1)[-1]
        if vid == "app:notes":
            self.stack.append("notes")
            self.foreground_package = PACKAGES["notes"]
            return 'tapped app icon "Notes" -> Notes'
        if vid == "app:settings":
            self.stack.append("settings")
            self.foreground_package = PACKAGES["settings"]
            return 'tapped app icon "Settings" -> Settings'
        if vid == "note:new":
            self.draft = ""
            self.editing = None
            self.stack.append("notes_edit")
            return 'tapped "New note" -> editor'
        if vid.startswith("note:item:"):
            idx = int(vid.rsplit(":", 1)[1])
            self.editing = idx
            self.draft = self.notes[idx]
            self.stack.append("notes_edit")
            return f"tapped note [{idx}] -> editor"
        if vid == "note:delete-all":
            count = len(self.notes)
            self.notes.clear()
            return f'tapped "Delete all notes" -> deleted {count} note(s)'
        if vid == "btn:save":
            if self.editing is not None and self.editing < len(self.notes):
                self.notes[self.editing] = self.draft
                verb = "updated"
            else:
                self.notes.append(self.draft)
                verb = "saved"
            text, self.draft, self.editing = self.draft, "", None
            self.stack.pop()
            return f'tapped "Save" -> note {verb}: {text!r}'
        if vid.startswith("toggle:"):
            name = vid.split(":", 1)[1]
            self.toggles[name] = not self.toggles[name]
            return f'tapped toggle "{name}" -> {"on" if self.toggles[name] else "off"}'
        return f'tapped "{el["name"]}" (no effect)'


def make_bridge(state: FakeAndroidState | None = None):
    """Start the fake bridge on an ephemeral loopback port.

    Returns (server, base_url, state). Caller shuts the server down."""
    state = state or FakeAndroidState()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep tests quiet
            pass

        def _send(self, code, obj):
            data = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _authed(self) -> bool:
            token = self.headers.get("X-Ghost-Hands-Token")
            state.seen_tokens.append(token)
            if token != TOKEN:
                self._send(401, {"error": "bad or missing pairing token"})
                return False
            return True

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8"))

        def do_GET(self):  # noqa: N802 - stdlib handler API
            if self.path.startswith("/v1/approvals/"):
                if not self._authed():
                    return
                approval_id = self.path[len("/v1/approvals/"):]
                rec = state.approvals.get(approval_id)
                if rec is None:
                    return self._send(
                        404, {"error": f"unknown approval id: {approval_id}"}
                    )
                return self._send(200, state.approval_view(rec))
            if self.path != "/v1/status":
                return self._send(404, {"error": f"unknown endpoint: {self.path}"})
            if not self._authed():
                return
            self._send(
                200,
                {
                    "ok": True,
                    "protocol": state.protocol,
                    "app": "MrGhosty",
                    "version": "1.7.0",
                    "service_connected": state.service_connected,
                    "foreground_package": state.foreground_package,
                    "api_level": state.api_level,
                },
            )

        def do_POST(self):  # noqa: N802 - stdlib handler API
            if self.path not in ("/v1/perceive", "/v1/act", "/v1/approvals"):
                return self._send(404, {"error": f"unknown endpoint: {self.path}"})
            if not self._authed():
                return
            if self.path == "/v1/approvals":
                return self._create_approval()
            if self.path == "/v1/perceive":
                return self._send(
                    200,
                    {
                        "ok": True,
                        "url": f"android://{state.foreground_package}",
                        "package": state.foreground_package,
                        "elements": state.elements(),
                    },
                )
            self._act()

        def _create_approval(self):
            body = self._body()
            state.approval_payloads.append(body)
            approval_id = body.get("id")
            kind = body.get("kind")
            summary = body.get("summary")
            timeout = body.get("timeout_seconds")
            if not isinstance(approval_id, str) or not approval_id:
                return self._send(400, {"error": "approval requires an id"})
            if not isinstance(kind, str) or not kind:
                return self._send(400, {"error": "approval requires a kind"})
            if not isinstance(summary, str) or not summary:
                return self._send(400, {"error": "approval requires a summary"})
            if (
                not isinstance(timeout, (int, float))
                or isinstance(timeout, bool)
                or timeout <= 0
            ):
                return self._send(
                    400, {"error": "approval requires timeout_seconds > 0"}
                )
            if approval_id in state.approvals:
                return self._send(
                    409, {"error": f"duplicate approval id: {approval_id}"}
                )
            state.approvals[approval_id] = {
                "id": approval_id,
                "kind": kind,
                "classification": body.get("classification") or "",
                "summary": summary,
                "target": body.get("target"),
                "url": body.get("url") or "",
                "timeout_seconds": float(timeout),
                "test": bool(body.get("test")),
                "created_mono": time.monotonic(),
                "created_at": time.time(),
                "status": "pending",
                "decided_at": None,
            }
            return self._send(
                201, {"ok": True, "id": approval_id, "status": "pending"}
            )

        def _act(self):
            body = self._body()
            action = body.get("action") or {}
            kind = action.get("kind")
            ref = body.get("target_ref")
            expected = body.get("expected")

            def resolve_target():
                if ref is None:
                    return None, None
                el = state.resolve(ref)
                if el is None:
                    self._send(
                        404,
                        {"error": f"target missing: ref {ref} no longer "
                                  "resolves on the current screen"},
                    )
                    return None, "sent"
                if expected:
                    key = (el["tag"], el["role"], el["name"])
                    want = (
                        expected.get("tag"),
                        expected.get("role"),
                        expected.get("name"),
                    )
                    if key != want:
                        self._send(
                            409,
                            {"error": f"target mismatch: ref {ref} was "
                                      f"{want}, now {key}"},
                        )
                        return None, "sent"
                return el, None

            if kind == "navigate":
                url = action.get("url") or ""
                if url.startswith("app://"):
                    package = url[len("app://"):]
                    for screen, pkg in PACKAGES.items():
                        if pkg == package and screen in (
                            "notes", "settings", "home",
                        ):
                            state.stack = ["home"] if screen == "home" else [
                                "home", screen,
                            ]
                            state.foreground_package = pkg
                            break
                    else:
                        state.stack = ["app"]
                        state.foreground_package = package
                    return self._send(
                        200, {"ok": True, "result": f"launched {package}"}
                    )
                if url.startswith(("http://", "https://")):
                    state.stack = ["app"]
                    state.foreground_package = "com.android.chrome"
                    return self._send(
                        200, {"ok": True, "result": f"opened {url}"}
                    )
                return self._send(
                    400, {"error": f"unsupported navigate URL: {url}"}
                )
            if kind == "click":
                el, sent = resolve_target()
                if sent:
                    return
                if el is None:
                    return self._send(400, {"error": "click requires a target"})
                return self._send(200, {"ok": True, "result": state.apply_tap(el)})
            if kind == "type":
                el, sent = resolve_target()
                if sent:
                    return
                if el is None or el["role"] != "text_field":
                    return self._send(
                        400, {"error": "target is not an editable node"}
                    )
                state.draft = action.get("text") or ""
                return self._send(
                    200,
                    {"ok": True, "result": f'typed into "{el["name"]}"'},
                )
            if kind == "press":
                key = (action.get("key") or "").strip()
                if key == "Back":
                    if len(state.stack) > 1:
                        state.stack.pop()
                        state.foreground_package = PACKAGES[state.screen]
                    return self._send(200, {"ok": True, "result": "back"})
                if key == "Home":
                    state.stack = ["home"]
                    state.foreground_package = PACKAGES["home"]
                    return self._send(200, {"ok": True, "result": "home"})
                if key in ("Recents", "Notifications", "QuickSettings"):
                    return self._send(200, {"ok": True, "result": f"pressed {key}"})
                return self._send(
                    400, {"error": f"unsupported key on Android body: {key}"}
                )
            if kind == "scroll":
                return self._send(
                    200,
                    {"ok": True,
                     "result": f"scrolled {action.get('direction') or 'down'}"},
                )
            if kind == "extract":
                texts = []
                for el in state.elements():
                    texts.append(el.get("value") or el["name"])
                joined = "\n".join(texts)
                return self._send(
                    200, {"ok": True, "result": joined, "text": joined}
                )
            if kind == "screenshot":
                if state.api_level < 30:
                    return self._send(
                        400,
                        {"error": "screenshot needs Android 11 (API 30)+; "
                                  f"this device reports API {state.api_level}"},
                    )
                raw = base64.b64decode(TINY_PNG_B64)
                return self._send(
                    200,
                    {"ok": True, "png_base64": TINY_PNG_B64, "bytes": len(raw)},
                )
            if kind == "wait":
                return self._send(200, {"ok": True, "result": "ok"})
            return self._send(
                400, {"error": f"unsupported on Android body: {kind}"}
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}", state
