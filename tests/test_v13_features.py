"""v1.3 features: governed recovery from stuck, the LLM token budget
cap, goal-driven hands_run over MCP, and the eval scorecard."""

from __future__ import annotations

import json

from ghost_hands import evaluate, mcp_server
from ghost_hands.deciders import OpenAICompatibleDecider, ScriptedDecider
from ghost_hands.drivers import DEMO_PAGES, FakeDriver
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail
from ghost_hands.trail import verify_trail


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------


def test_recovery_rescues_repeated_action_stuck():
    steps = [
        {"kind": "extract"},
        {"kind": "extract"},
        {"kind": "extract"},
        {"kind": "extract"},  # 4th repeat: stuck without recovery
        {"kind": "verify", "url": "demo.local"},
        {"kind": "done", "summary": "recovered"},
    ]
    trail = Trail()
    report = Runner(
        FakeDriver(DEMO_PAGES), ScriptedDecider(steps), trail=trail
    ).run("g")
    assert report.stop_reason == "done", report.summary
    assert report.summary == "recovered"
    rec = [ev for ev in trail.events if ev.get("type") == "recovery"]
    assert len(rec) == 1 and rec[0]["attempt"] == 1
    assert verify_trail(trail.events)["ok"] is True


def test_recovery_disabled_restores_stuck_stop():
    steps = [{"kind": "extract"}] * 5
    report = Runner(
        FakeDriver(DEMO_PAGES),
        ScriptedDecider(steps),
        trail=Trail(),
        recovery_attempts=0,
    ).run("g")
    assert report.stop_reason == "stuck"


def test_recovery_rescues_unchanged_map_stuck():
    steps = [
        {"kind": "extract"},
        {"kind": "extract"},
        {"kind": "extract"},
        {"kind": "verify", "url": "demo.local"},
        {"kind": "done", "summary": "moved on"},
    ]
    trail = Trail()
    report = Runner(
        FakeDriver(DEMO_PAGES), ScriptedDecider(steps), trail=trail
    ).run("g")
    assert report.stop_reason == "done", report.summary
    assert report.summary == "moved on"
    assert any(ev.get("type") == "recovery" for ev in trail.events)


# ---------------------------------------------------------------------------
# Token budget cap
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _stub(contents, usage):
    state = {"i": 0}

    def fake(req, timeout=None):
        content = contents[min(state["i"], len(contents) - 1)]
        state["i"] += 1
        return _Resp(
            {
                "choices": [{"message": {"content": content}}],
                "usage": usage,
            }
        )

    return fake


def test_budget_cap_stops_between_steps(monkeypatch):
    contents = [
        json.dumps({"kind": "extract"}),
        json.dumps({"kind": "extract"}),
        json.dumps({"kind": "done", "summary": "never decided"}),
    ]
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _stub(contents, {"prompt_tokens": 100, "completion_tokens": 0}),
    )
    d = OpenAICompatibleDecider(base_url="http://x.local", api_key="k")
    report = Runner(
        FakeDriver(DEMO_PAGES), d, trail=Trail(), max_llm_tokens=150
    ).run("g")
    assert report.stop_reason == "budget", report.summary
    assert report.llm_calls == 2  # the cap stopped the third decide
    assert "between steps" in report.summary


def test_budget_cap_ignores_deciders_without_usage():
    report = Runner(
        FakeDriver(DEMO_PAGES),
        ScriptedDecider([{"kind": "done", "summary": "fine"}]),
        trail=Trail(),
        max_llm_tokens=1,
    ).run("g")
    assert report.stop_reason == "done"


# ---------------------------------------------------------------------------
# MCP goal-driven mode
# ---------------------------------------------------------------------------


def _call(session, name, args, mid=1):
    resp = mcp_server.handle_request(
        session,
        {
            "jsonrpc": "2.0",
            "id": mid,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
    )
    return resp["result"]["content"][0]["text"]


def _llm_env(monkeypatch, contents):
    monkeypatch.setenv("GHOST_HANDS_DECIDER", "openai")
    monkeypatch.setenv("GHOST_HANDS_BASE_URL", "http://x.local")
    monkeypatch.setenv("GHOST_HANDS_API_KEY", "k")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _stub(contents, {"prompt_tokens": 10, "completion_tokens": 1}),
    )


def test_mcp_goal_driven_run(monkeypatch):
    _llm_env(
        monkeypatch,
        [
            json.dumps({"kind": "verify", "url": "demo.local"}),
            json.dumps({"kind": "done", "summary": "llm drove it"}),
        ],
    )
    session = mcp_server.HandsSession()
    out = json.loads(_call(session, "hands_run", {"goal": "check the demo"}))
    assert out["stop_reason"] == "done", out
    assert out["summary"] == "llm drove it"
    assert out["llm_calls"] == 2


def test_mcp_goal_driven_rejects_steps_and_missing_goal(monkeypatch):
    _llm_env(monkeypatch, [json.dumps({"kind": "done", "summary": "x"})])
    session = mcp_server.HandsSession()
    both = _call(
        session,
        "hands_run",
        {"goal": "g", "steps": [{"kind": "done", "summary": "s"}]},
    )
    assert both.startswith("ERROR")
    nogoal = _call(session, "hands_run", {})
    assert nogoal.startswith("ERROR")


def test_mcp_scripted_mode_still_requires_steps(monkeypatch):
    monkeypatch.delenv("GHOST_HANDS_DECIDER", raising=False)
    session = mcp_server.HandsSession()
    out = _call(session, "hands_run", {"goal": "g"})
    assert out.startswith("ERROR")


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------


def test_scorecard_render_keeps_deciders_separate_and_honest():
    payload = {
        "decider": "stub (deterministic local stub)",
        "totals": {
            "succeeded": 8,
            "tasks": 8,
            "steps": 31,
            "wall_s": 1.5,
            "est_tokens": 4200,
            "approvals_requested": 2,
            "heals": 1,
        },
        "tasks": [
            {
                "task": "search_form",
                "success": True,
                "steps": 4,
                "detail": "ok",
            }
        ],
        "verdict": "Verdict: harness verified end to end",
        "notes": ["stub note"],
    }
    card = evaluate.render_scorecard([payload])
    assert "eval scorecard" in card
    assert "8/8 tasks succeeded" in card
    assert "never averaged" in card
    assert "| search_form | PASS | 4 | ok |" in card
    assert "Verdict: harness verified end to end" in card
