"""MCP server: Ghost Hands over stdio, stdlib only.

JSON-RPC 2.0, newline-delimited, protocolVersion 2024-11-05. Exposes the
governed surface other tools leave raw:

- ``hands_perceive`` — open a page (or swap in a fake web) and return the
  numbered element map.
- ``hands_act`` — perform ONE action through the Governor with the default
  policy. Consequential actions need approval; there is no approver on
  this channel, so they are denied with an explanation — never executed.
- ``hands_run`` — run scripted steps through a full Runner (FakeDriver, or
  ChromiumDriver when driver="chromium").
- ``hands_trail`` — return this session's trail and accounting.

Policy pack: set ``GHOST_HANDS_POLICY`` (readonly | standard | strict)
to run the whole MCP session under a named pack from
``ghost_hands.policies``. An unknown name fails loudly at startup —
never a silent fall back to the default policy.
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
        "description": "Perform one governed action (e.g. {\"kind\":\"click\",\"target\":3}) on the perceived page. Consequential actions are denied without an approver.",
        "inputSchema": {
            "type": "object",
            "properties": {"action": {"type": "object"}},
            "required": ["action"],
        },
    },
    {
        "name": "hands_run",
        "description": "Run scripted steps through the governed Runner. driver: 'fake' (default) or 'chromium'.",
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


class HandsSession:
    def __init__(self) -> None:
        self.driver = FakeDriver(DEMO_PAGES)
        self.trail = Trail()
        pack_name = os.environ.get("GHOST_HANDS_POLICY")
        self.policy_pack = load_pack(pack_name) if pack_name else None
        self.governor = (
            Governor(self.policy_pack.policy) if self.policy_pack else Governor()
        )
        self.last_map: Optional[ElementMap] = None
        self.step = 0
        self._wire_sink(self.driver)

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
        session.driver = FakeDriver(args["pages"], start_url=args.get("url"))
        session._wire_sink(session.driver)
    elif args.get("url"):
        session.driver.open(args["url"])
    session.step += 1
    html = session.driver.snapshot_html()
    url = session.driver.current_url()
    element_map = ElementMap.from_html(html, url=url)
    session.last_map = element_map
    rendered = element_map.render()
    session.trail.record(
        "perceive",
        session.step,
        url=url,
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
    if decision.outcome == ASK:
        decision_reason = "approval required, no approver on the MCP channel"
        session.trail.record(
            "govern",
            session.step,
            action=action.to_dict(),
            classification=decision.classification,
            outcome="deny",
            reason=decision_reason,
        )
        return _text(
            f"DENIED ({decision.classification}): consequential actions need "
            f"approval and the MCP channel has no approver. {decision.reason}."
        )
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
    return _text(result)


def _run(session: HandsSession, args: dict) -> dict:
    steps = args["steps"]
    approver = (lambda action: True) if args.get("approve_all") else None
    trail = Trail()
    if args.get("driver") == "chromium":
        driver = ChromiumDriver()
        try:
            runner = Runner(
                driver,
                ScriptedDecider(steps),
                governor=session.governor,
                approver=approver,
                trail=trail,
                start_url=args.get("start_url"),
            )
            report = runner.run(args.get("goal", ""))
        finally:
            driver.close()
    else:
        pages = args.get("pages") or DEMO_PAGES
        driver = FakeDriver(pages, start_url=args.get("start_url"))
        runner = Runner(
            driver,
            ScriptedDecider(steps),
            governor=session.governor,
            approver=approver,
            trail=trail,
            start_url=args.get("start_url"),
        )
        report = runner.run(args.get("goal", ""))
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
