"""MCP server: Ghost Hands over stdio, stdlib only.

JSON-RPC 2.0, newline-delimited, protocolVersion 2024-11-05. Exposes the
governed surface other tools leave raw:

- ``hands_perceive`` — open a page (or swap in a fake web) and return the
  numbered element map.
- ``hands_act`` — perform ONE action through the Governor. Consequential
  actions need approval: by default this channel has no approver, so
  they are denied with an explanation — never executed. With
  ``GHOST_HANDS_APPROVER=phone`` the ask is routed to Ryan's phone
  instead (see below), and the response says which channel decided.
- ``hands_run`` — run scripted steps through a full Runner. The body is
  the tool's ``driver`` argument, or the session body when omitted.
- ``hands_trail`` — return this session's trail and accounting.

Session configuration is per-server-process, via the environment —
switching bodies or approvers means starting a new server process:

- ``GHOST_HANDS_POLICY`` (readonly | standard | strict | seatbelt):
  run the whole session under a named pack from ``ghost_hands.policies``.
- ``GHOST_HANDS_DRIVER`` (fake | chromium | simphone | android, v0.8):
  the session body for ``hands_perceive`` / ``hands_act`` (and the
  default body for ``hands_run``). Default: fake (the demo web), which
  is what earlier versions always used. ``android`` is configured
  exactly like the bus agent's: ``GHOST_HANDS_ANDROID_BRIDGE`` /
  ``GHOST_HANDS_ANDROID_TOKEN`` (the token never appears in tool
  output or errors).
- ``GHOST_HANDS_START_URL``: opened by the session body on the first
  perceive when the caller passes no URL (chromium / android /
  simphone sessions).
- ``GHOST_HANDS_APPROVER`` (phone, v0.8): route "ask" verdicts through
  ``PhoneApprover`` — Ryan's phone decides via the MrGhosty bridge.
  Unset (the default): asks are denied with an explanation.

Unknown values for any of these fail loudly at startup — never a
silent fallback to a different body, pack, or approver.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

from .actions import Action
from .drivers import DEMO_PAGES, ChromiumDriver, FakeDriver
from .deciders import ScriptedDecider
from .errors import HandsError
from .eyes import ElementMap
from .governor import ASK, Governor
from .policies import load_pack
from .runner import Runner
from .trail import Trail

from . import __version__ as _pkg_version

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "ghost-hands", "version": _pkg_version}

TOOLS = [
    {
        "name": "hands_perceive",
        "description": "Open a URL (or install a fake web via 'pages') and return the numbered element map.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "pages": {"type": "object"},
            },
        },
    },
    {
        "name": "hands_act",
        "description": "Perform one governed action (e.g. {\"kind\":\"click\",\"target\":3}) on the perceived page. Consequential actions are denied without an approver (or decided by the phone when GHOST_HANDS_APPROVER=phone); the response states which channel decided.",
        "inputSchema": {
            "type": "object",
            "properties": {"action": {"type": "object"}},
            "required": ["action"],
        },
    },
    {
        "name": "hands_run",
        "description": "Run scripted steps through the governed Runner. driver: 'fake', 'chromium', 'simphone' or 'android' (default: the session body, GHOST_HANDS_DRIVER).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {"type": "array"},
                "pages": {"type": "object"},
                "start_url": {"type": "string"},
                "driver": {"type": "string"},
                "goal": {"type": "string"},
                "approve_all": {"type": "boolean"},
            },
            "required": ["steps"],
        },
    },
    {
        "name": "hands_trail",
        "description": "Return this session's trail events and per-run accounting.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


DRIVER_KINDS = ("fake", "chromium", "simphone", "android")
APPROVER_KINDS = ("phone",)


def make_driver(kind: str):
    """Construct (but do not open) a body by kind. Chromium and the
    Android bridge both connect lazily, on first use."""
    if kind == "fake":
        return FakeDriver(DEMO_PAGES)
    if kind == "chromium":
        return ChromiumDriver()
    if kind == "simphone":
        from .simphone import SimPhoneDriver

        return SimPhoneDriver()
    if kind == "android":
        from .android_driver import AndroidDriver

        # Bridge URL + pairing token come from the environment, exactly
        # like the bus agent's android factory; the token is held by
        # the driver and never reaches tool output or errors.
        return AndroidDriver()
    raise HandsError(
        f"unknown driver {kind!r}; expected one of: {', '.join(DRIVER_KINDS)}"
    )


class HandsSession:
    def __init__(self) -> None:
        self.trail = Trail()
        pack_name = os.environ.get("GHOST_HANDS_POLICY")
        self.policy_pack = load_pack(pack_name) if pack_name else None
        self.governor = (
            Governor(self.policy_pack.policy) if self.policy_pack else Governor()
        )

        kind = (os.environ.get("GHOST_HANDS_DRIVER") or "fake").strip().lower()
        if kind not in DRIVER_KINDS:
            raise HandsError(
                f"unknown GHOST_HANDS_DRIVER {kind!r}; expected one of: "
                f"{', '.join(DRIVER_KINDS)}"
            )
        self.driver_kind = kind
        self.start_url = os.environ.get("GHOST_HANDS_START_URL") or None
        self.driver = make_driver(kind)
        self._opened = False

        approver_name = (
            os.environ.get("GHOST_HANDS_APPROVER") or ""
        ).strip().lower()
        self.approver = None
        self.approver_channel: Optional[str] = None
        if approver_name in ("", "none", "deny"):
            pass  # the long-standing default: asks are denied, explained
        elif approver_name == "phone":
            from .phone_approver import PhoneApprover

            self.approver = PhoneApprover(trail=self.trail)
            self.approver_channel = "phone"
        else:
            raise HandsError(
                f"unknown GHOST_HANDS_APPROVER {approver_name!r}; "
                "expected 'phone' (or unset for the deny-with-explanation "
                "default)"
            )

        self.last_map: Optional[ElementMap] = None
        self.step = 0
        self._wire_sink(self.driver)

    def close(self) -> None:
        try:
            self.driver.close()
        except Exception:
            pass

    def _wire_sink(self, driver) -> None:
        """Driver-originated events (dialog / net / download) land in the
        session trail, exactly as the Runner wires them for its runs."""
        if hasattr(driver, "event_sink"):
            driver.event_sink = lambda etype, fields: self.trail.record(
                etype, self.step, **fields
            )


def _text(payload: str, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": payload}], "isError": is_error}


def _perceive(session: HandsSession, args: dict) -> dict:
    if args.get("pages"):
        if session.driver_kind != "fake":
            return _text(
                "ERROR: 'pages' installs a fake web and needs the fake "
                f"body; this session's body is {session.driver_kind!r} "
                "(GHOST_HANDS_DRIVER). Start the server with "
                "GHOST_HANDS_DRIVER=fake to use 'pages'.",
                is_error=True,
            )
        session.driver = FakeDriver(args["pages"], start_url=args.get("url"))
        session._wire_sink(session.driver)
        session._opened = True
    elif args.get("url"):
        session.driver.open(args["url"])
        session._opened = True
    elif not session._opened and session.start_url:
        session.driver.open(session.start_url)
        session._opened = True
    session.step += 1
    # Bodies with native eyes (Chromium DOM/AX, the Android bridge, the
    # sim phone) perceive through them — the same preference the Runner
    # applies — and HTML-snapshot bodies fall back to the parser.
    perceive = getattr(session.driver, "perceive", None)
    if callable(perceive):
        element_map = perceive()
        url = element_map.url or session.driver.current_url()
    else:
        html = session.driver.snapshot_html()
        url = session.driver.current_url()
        element_map = ElementMap.from_html(html, url=url)
    session.last_map = element_map
    rendered = element_map.render()
    session.trail.record(
        "perceive",
        session.step,
        url=url,
        title=element_map.title,
        elements=len(element_map),
        map_chars=len(rendered),
        signature=element_map.signature(),
    )
    return _text(f"URL: {url}\n{rendered}")


def _act(session: HandsSession, args: dict) -> dict:
    action = Action.from_dict(args["action"])
    if session.last_map is None:
        _perceive(session, {})
    element_map = session.last_map
    assert element_map is not None
    element = element_map.get(action.target)
    session.step += 1
    session.trail.record("decide", session.step, action=action.to_dict())
    decision = session.governor.evaluate(
        action, element, page_url=session.driver.current_url()
    )
    channel_note = ""
    if decision.outcome == ASK:
        if session.approver is None:
            # The default MCP posture, unchanged since v0.1: no
            # approver on this channel, so an ask is a denial — with
            # the reason in the reply, never a silent execution.
            decision_reason = (
                "approval required, no approver (MCP default)"
            )
            session.trail.record(
                "govern",
                session.step,
                action=action.to_dict(),
                classification=decision.classification,
                outcome="deny",
                reason=decision_reason,
            )
            return _text(
                f"DENIED ({decision.classification}): no approver "
                "(MCP default) — consequential actions need approval "
                "and this MCP session has no approval channel. Set "
                "GHOST_HANDS_APPROVER=phone to route asks to the "
                f"phone. {decision.reason}."
            )
        # An approval channel is configured (phone): record the ask,
        # let the channel decide, and say which channel decided.
        session.trail.record(
            "govern",
            session.step,
            action=action.to_dict(),
            classification=decision.classification,
            outcome="ask",
            reason=decision.reason,
        )
        observe = getattr(session.approver, "observe", None)
        if callable(observe):
            observe(element_map)
        if not session.approver(action):
            status = getattr(session.approver, "last_status", None)
            detail = f" ({status})" if status else ""
            return _text(
                f"DENIED ({decision.classification}): denied via "
                f"{session.approver_channel}{detail} — the action did "
                "not run."
            )
        channel_note = f"approved via {session.approver_channel} — "
    else:
        session.trail.record(
            "govern",
            session.step,
            action=action.to_dict(),
            classification=decision.classification,
            outcome=decision.outcome,
            reason=decision.reason,
        )
    if decision.outcome == "deny":
        return _text(f"DENIED ({decision.classification}): {decision.reason}")
    session.trail.record(
        "execute",
        session.step,
        action=action.to_dict(),
        classification=decision.classification,
        element=element.descriptor() if element is not None else None,
    )
    try:
        result = session.driver.act(action, element)
    except Exception as exc:
        session.trail.record("result", session.step, ok=False, result=f"error: {exc}")
        return _text(f"ERROR: {exc}", is_error=True)
    session.trail.record("result", session.step, ok=True, result=result)
    _perceive(session, {})  # refresh the map for the next call
    return _text(channel_note + result)


def _run(session: HandsSession, args: dict) -> dict:
    steps = args["steps"]
    kind = (args.get("driver") or session.driver_kind or "fake").strip().lower()
    if kind not in DRIVER_KINDS:
        return _text(
            f"ERROR: unknown driver {kind!r}; expected one of: "
            f"{', '.join(DRIVER_KINDS)}",
            is_error=True,
        )
    start_url = args.get("start_url") or session.start_url
    trail = Trail()
    approver = None
    decider = ScriptedDecider(steps)
    if args.get("approve_all"):
        approver = lambda action: True  # explicit client opt-in
    elif session.approver_channel == "phone":
        # Runs decide on their own trail, so the phone approver binds
        # to it (the CLI's --approver phone pattern) and the decider's
        # live maps keep the approval summaries in context.
        from .phone_approver import ObservingDecider, PhoneApprover

        approver = PhoneApprover(trail=trail)
        decider = ObservingDecider(decider, approver)
    if kind == "fake":
        driver = FakeDriver(args.get("pages") or DEMO_PAGES, start_url=start_url)
    else:
        driver = make_driver(kind)
    try:
        runner = Runner(
            driver,
            decider,
            governor=session.governor,
            approver=approver,
            trail=trail,
            start_url=start_url,
        )
        report = runner.run(args.get("goal", ""))
    finally:
        driver.close()
    return _text(json.dumps(report.to_dict(), indent=2))


def _trail(session: HandsSession, args: dict) -> dict:
    payload = {
        "events": session.trail.events,
        "accounting": session.trail.accounting(),
    }
    return _text(json.dumps(payload, indent=2))


_TOOL_FUNCS = {
    "hands_perceive": _perceive,
    "hands_act": _act,
    "hands_run": _run,
    "hands_trail": _trail,
}


def handle_request(session: HandsSession, msg: dict) -> Optional[dict]:
    """Handle one JSON-RPC message; returns a response dict, or None for
    notifications."""
    method = msg.get("method", "")
    msg_id = msg.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    if method.startswith("notifications/"):
        return None
    if msg_id is None:
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name", "")
        args = params.get("arguments") or {}
        func = _TOOL_FUNCS.get(name)
        if func is None:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": f"unknown tool: {name}"},
            }
        try:
            result = func(session, args)
        except (HandsError, ValueError, KeyError) as exc:
            result = _text(f"ERROR: {exc}", is_error=True)
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def serve(stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    session = HandsSession()
    try:
        _serve_loop(session, stdin, stdout)
    finally:
        session.close()


def _serve_loop(session: HandsSession, stdin, stdout) -> None:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "parse error"},
            }
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
            continue
        response = handle_request(session, msg)
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


def main() -> None:
    serve()
