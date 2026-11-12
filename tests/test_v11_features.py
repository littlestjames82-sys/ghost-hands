"""v1.1 features: focused perception, report trail integrity, and the
MCP surface for the hash chain + plan cache (GHOST_HANDS_CACHE)."""

from __future__ import annotations

import json

from ghost_hands import mcp_server
from ghost_hands.drivers import DEMO_PAGES, FakeDriver, demo_steps
from ghost_hands.eyes import ElementMap
from ghost_hands.report import render_report, summarize
from ghost_hands.runner import Runner
from ghost_hands.deciders import ScriptedDecider
from ghost_hands.trail import Trail

BIG_PAGE = (
    "<html><body>"
    + "".join(
        f'<a href="https://x.local/p{i}">Reference page {i}</a>' for i in range(40)
    )
    + "<button>Checkout now</button></body></html>"
)


# ---------------------------------------------------------------------------
# Focused perception
# ---------------------------------------------------------------------------


def test_focused_render_keeps_goal_element_past_the_cut():
    m = ElementMap.from_html(BIG_PAGE, budget=400)
    assert "Checkout now" not in m.render()
    focused = m.render_focused("proceed to checkout")
    assert "Checkout now" in focused
    # The kept element prints under its ORIGINAL number (41st element).
    line = [ln for ln in focused.splitlines() if "Checkout now" in ln][0]
    assert line.startswith("[41]")
    assert "focused:" in focused


def test_focused_render_is_plain_render_when_everything_fits():
    m = ElementMap.from_html(BIG_PAGE, budget=100_000)
    assert m.render_focused("checkout") == m.render()


def test_focused_render_respects_budget():
    m = ElementMap.from_html(BIG_PAGE, budget=300)
    assert len(m.render_focused("checkout")) <= 400  # budget + note slack


# ---------------------------------------------------------------------------
# Report integrity line
# ---------------------------------------------------------------------------


def _demo_events():
    trail = Trail()
    runner = Runner(
        FakeDriver(DEMO_PAGES), ScriptedDecider(demo_steps()), trail=trail
    )
    report = runner.run("demo")
    assert report.stop_reason == "done"
    return trail.events


def test_report_shows_intact_chain():
    events = _demo_events()
    assert summarize(events)["chain"]["ok"] is True
    assert "hash chain intact" in render_report(events)


def test_report_shows_broken_chain():
    events = [dict(ev) for ev in _demo_events()]
    events[1]["type"] = "forged"
    assert summarize(events)["chain"]["ok"] is False
    assert "CHAIN BROKEN" in render_report(events)


def test_report_shows_legacy_for_pre_chain_events():
    events = [{"type": "run", "step": 0, "goal": "old"}]
    assert "legacy trail" in render_report(events)


# ---------------------------------------------------------------------------
# MCP: trail integrity + hands_run plan cache via GHOST_HANDS_CACHE
# ---------------------------------------------------------------------------


def _call(session, name, args):
    resp = mcp_server.handle_request(
        session,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
    )
    return json.loads(resp["result"]["content"][0]["text"])


def test_mcp_trail_reports_integrity():
    session = mcp_server.HandsSession()
    mcp_server.handle_request(
        session,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "hands_perceive", "arguments": {}},
        },
    )
    payload = _call(session, "hands_trail", {})
    assert payload["trail_integrity"]["ok"] is True
    assert payload["trail_integrity"]["hashed"] == len(payload["events"])


def test_mcp_hands_run_uses_env_plan_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_CACHE", str(tmp_path / "plans.json"))
    session = mcp_server.HandsSession()
    steps = demo_steps()
    first = _call(
        session, "hands_run", {"goal": "mcp cache flow", "steps": steps}
    )
    assert first["stop_reason"] == "done", first
    # Empty script: the decider errors if consulted, so a done here
    # proves the cached plan drove the run.
    second = _call(session, "hands_run", {"goal": "mcp cache flow", "steps": []})
    assert second["stop_reason"] == "done", second
    assert second["steps"] > 0
