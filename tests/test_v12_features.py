"""v1.2 features: batched decider calls under per-action governance,
real LLM token accounting, the require-verify gate, and the MCP
hands_verify_trail tool."""

from __future__ import annotations

import json

from ghost_hands import mcp_server
from ghost_hands.actions import Action
from ghost_hands.deciders import OpenAICompatibleDecider, ScriptedDecider
from ghost_hands.drivers import DEMO_PAGES, FakeDriver, demo_steps
from ghost_hands.report import summarize
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail


# ---------------------------------------------------------------------------
# Batched decisions
# ---------------------------------------------------------------------------


class BatchDecider:
    """Returns a whole batch on the first call; a plain done after."""

    batch = True

    def __init__(self, actions):
        self._actions = actions
        self.batch_calls = 0
        self.decide_calls = 0

    def decide_batch(self, goal, element_map, history):
        self.batch_calls += 1
        return [Action.from_dict(a) for a in self._actions]

    def decide(self, goal, element_map, history):
        self.decide_calls += 1
        return Action.from_dict({"kind": "done", "summary": "fallback done"})


def test_batch_runs_all_actions_governed_with_one_decider_call():
    decider = BatchDecider(
        [
            {"kind": "type", "target": 1, "text": "ghost"},
            {"kind": "click", "target": 2},
            {"kind": "done", "summary": "batched"},
        ]
    )
    trail = Trail()
    report = Runner(FakeDriver(DEMO_PAGES), decider, trail=trail).run("search")
    assert report.stop_reason == "done", report.summary
    assert decider.batch_calls == 1
    assert decider.decide_calls == 0
    decides = [ev for ev in trail.events if ev.get("type") == "decide"]
    assert decides and all(ev.get("source") == "batch" for ev in decides)
    executes = [ev for ev in trail.events if ev.get("type") == "execute"]
    governs = [ev for ev in trail.events if ev.get("type") == "govern"]
    assert len(executes) == 2 and len(governs) == 2


def test_batch_stale_target_drops_queue_and_falls_back():
    decider = BatchDecider(
        [
            {"kind": "extract"},
            {"kind": "click", "target": 99},  # no such element
            {"kind": "done", "summary": "never reached from batch"},
        ]
    )
    trail = Trail()
    report = Runner(FakeDriver(DEMO_PAGES), decider, trail=trail).run("x")
    assert report.stop_reason == "done", report.summary
    assert report.summary == "fallback done"
    assert decider.decide_calls == 1


def test_batch_off_by_default_for_openai_decider(monkeypatch):
    monkeypatch.delenv("GHOST_HANDS_BATCH", raising=False)
    d = OpenAICompatibleDecider(base_url="http://x.local", api_key="k")
    assert d.batch is False
    monkeypatch.setenv("GHOST_HANDS_BATCH", "1")
    d2 = OpenAICompatibleDecider(base_url="http://x.local", api_key="k")
    assert d2.batch is True


# ---------------------------------------------------------------------------
# Real token accounting (stubbed HTTP)
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


def _stub_urlopen(contents, usage=None):
    state = {"i": 0}

    def fake(req, timeout=None):
        content = contents[min(state["i"], len(contents) - 1)]
        state["i"] += 1
        payload = {"choices": [{"message": {"content": content}}]}
        if usage:
            payload["usage"] = usage
        return _Resp(payload)

    return fake


def test_openai_batch_parse_and_usage(monkeypatch):
    content = json.dumps(
        {
            "actions": [
                {"kind": "extract"},
                {"kind": "done", "summary": "ok"},
            ]
        }
    )
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _stub_urlopen([content], usage={"prompt_tokens": 120, "completion_tokens": 7}),
    )
    d = OpenAICompatibleDecider(base_url="http://x.local", api_key="k", batch=True)
    from ghost_hands.eyes import ElementMap

    m = ElementMap.from_html(DEMO_PAGES["https://demo.local/"], url="https://demo.local/")
    actions = d.decide_batch("g", m, [])
    assert [a.kind for a in actions] == ["extract", "done"]
    assert d.usage == {"prompt_tokens": 120, "completion_tokens": 7, "calls": 1}


def test_usage_lands_on_report_trail_and_html_summary(monkeypatch):
    contents = [
        json.dumps({"kind": "extract"}),
        json.dumps({"kind": "done", "summary": "fin"}),
    ]
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _stub_urlopen(contents, usage={"prompt_tokens": 100, "completion_tokens": 5}),
    )
    d = OpenAICompatibleDecider(base_url="http://x.local", api_key="k")
    trail = Trail()
    report = Runner(FakeDriver(DEMO_PAGES), d, trail=trail).run("g")
    assert report.stop_reason == "done", report.summary
    assert report.llm_calls == 2
    assert report.llm_prompt_tokens == 200
    assert report.llm_completion_tokens == 10
    stop = [ev for ev in trail.events if ev.get("type") == "stop"][0]
    assert stop["llm_prompt_tokens"] == 200
    assert summarize(trail.events)["llm_usage"]["llm_calls"] == 2


# ---------------------------------------------------------------------------
# Require-verify gate
# ---------------------------------------------------------------------------


def test_require_verify_rejects_done_until_verified():
    steps = [
        {"kind": "done", "summary": "trust me"},
        {"kind": "verify", "url": "demo.local"},
        {"kind": "done", "summary": "proven"},
    ]
    trail = Trail()
    report = Runner(
        FakeDriver(DEMO_PAGES),
        ScriptedDecider(steps),
        trail=trail,
        require_verify=True,
    ).run("g")
    assert report.stop_reason == "done", report.summary
    assert report.summary == "proven"
    assert any(
        "done rejected" in str(ev.get("result", ""))
        for ev in trail.events
        if ev.get("type") == "result"
    )


def test_require_verify_three_unproven_dones_error_out():
    steps = [{"kind": "done", "summary": "x"}] * 3
    report = Runner(
        FakeDriver(DEMO_PAGES),
        ScriptedDecider(steps),
        trail=Trail(),
        require_verify=True,
    ).run("g")
    assert report.stop_reason == "error"
    assert "without a passing verify" in report.summary


def test_done_unaffected_without_require_verify():
    report = Runner(
        FakeDriver(DEMO_PAGES),
        ScriptedDecider([{"kind": "done", "summary": "plain"}]),
        trail=Trail(),
    ).run("g")
    assert report.stop_reason == "done"


# ---------------------------------------------------------------------------
# MCP: hands_verify_trail + hands_run require_verify
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


def test_mcp_verify_trail_session_and_tampered():
    session = mcp_server.HandsSession()
    _call(session, "hands_perceive", {})
    verdict = json.loads(_call(session, "hands_verify_trail", {}))
    assert verdict["ok"] is True
    events = [dict(ev) for ev in session.trail.events]
    events[0]["type"] = "forged"
    verdict2 = json.loads(_call(session, "hands_verify_trail", {"events": events}))
    assert verdict2["ok"] is False
    bad = _call(session, "hands_verify_trail", {"events": "nope"})
    assert bad.startswith("ERROR")


def test_mcp_hands_run_require_verify():
    session = mcp_server.HandsSession()
    out = json.loads(
        _call(
            session,
            "hands_run",
            {
                "goal": "gated",
                "require_verify": True,
                "steps": [
                    {"kind": "verify", "url": "demo.local"},
                    {"kind": "done", "summary": "proven"},
                ],
            },
        )
    )
    assert out["stop_reason"] == "done", out
    out2 = json.loads(
        _call(
            session,
            "hands_run",
            {
                "goal": "ungated claim",
                "require_verify": True,
                "steps": [{"kind": "done", "summary": "trust me"}],
            },
        )
    )
    assert out2["stop_reason"] != "done", out2
