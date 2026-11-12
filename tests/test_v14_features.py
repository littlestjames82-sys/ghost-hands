"""v1.4 features: page-change notes for the decider, the fallback
decider, and the costs aggregation CLI."""

from __future__ import annotations

import json

import pytest

from ghost_hands import cli, mcp_server
from ghost_hands.actions import Action
from ghost_hands.deciders import (
    FallbackDecider,
    OpenAICompatibleDecider,
    ScriptedDecider,
)
from ghost_hands.drivers import DEMO_PAGES, FakeDriver
from ghost_hands.errors import HandsError
from ghost_hands.eyes import ElementMap
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail


# ---------------------------------------------------------------------------
# Page-change notes
# ---------------------------------------------------------------------------


def test_diff_note_reports_navigation_and_element_changes():
    a = ElementMap.from_html(
        '<html><body><a href="/x">Go</a><button>Buy</button></body></html>',
        url="https://s.local/",
    )
    b = ElementMap.from_html(
        '<html><body><a href="/x">Go</a>'
        '<input type="text" placeholder="Search"></body></html>',
        url="https://s.local/results",
    )
    note = b.diff_note(a)
    assert note is not None
    assert "navigated to https://s.local/results" in note
    assert "Search" in note and "Buy" in note
    assert a.diff_note(a) is None
    assert a.diff_note(None) is None


class RecordingDecider:
    def __init__(self, steps):
        self._inner = ScriptedDecider(steps)
        self.histories: list[list] = []

    def decide(self, goal, element_map, history):
        self.histories.append([dict(h) for h in history])
        return self._inner.decide(goal, element_map, history)


def test_runner_tells_decider_what_changed():
    steps = [
        {"kind": "type", "target": 1, "text": "ghost"},
        {"kind": "click", "target": 2},
        {"kind": "done", "summary": "searched"},
    ]
    decider = RecordingDecider(steps)
    report = Runner(FakeDriver(DEMO_PAGES), decider, trail=Trail()).run(
        "search the demo web"
    )
    assert report.stop_reason == "done", report.summary
    notes = [
        h.get("note", "")
        for hist in decider.histories
        for h in hist
        if h.get("kind") == "system"
    ]
    assert any(n.startswith("Page change: navigated to") for n in notes), notes


# ---------------------------------------------------------------------------
# FallbackDecider
# ---------------------------------------------------------------------------


class RaisingDecider:
    batch = False
    usage = {"prompt_tokens": 5, "completion_tokens": 1, "calls": 1}

    def decide(self, goal, element_map, history):
        raise HandsError("primary endpoint down")


class FixedDecider:
    batch = False

    def __init__(self):
        self.calls = 0
        self.usage = {"prompt_tokens": 7, "completion_tokens": 2, "calls": 0}

    def decide(self, goal, element_map, history):
        self.calls += 1
        self.usage["calls"] += 1
        return Action.from_dict({"kind": "done", "summary": "fallback answered"})


def test_fallback_answers_and_usage_sums():
    fallback = FixedDecider()
    decider = FallbackDecider(RaisingDecider(), fallback)
    action = decider.decide("g", None, [])
    assert action.kind == "done"
    assert decider.failovers == 1
    assert fallback.calls == 1
    assert decider.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 3,
        "calls": 2,
        "retries": 0,
    }


def test_fallback_untouched_when_primary_healthy():
    fallback = FixedDecider()
    decider = FallbackDecider(FixedDecider(), fallback)
    decider.decide("g", None, [])
    assert decider.failovers == 0
    assert fallback.calls == 0


def test_fallback_batch_failover():
    class BatchRaiser:
        batch = True

        def decide_batch(self, goal, element_map, history):
            raise HandsError("batch endpoint down")

        def decide(self, goal, element_map, history):
            raise HandsError("down")

    class BatchFixed:
        batch = True

        def decide_batch(self, goal, element_map, history):
            return [Action.from_dict({"kind": "extract"})]

        def decide(self, goal, element_map, history):
            raise HandsError("unused")

    decider = FallbackDecider(BatchRaiser(), BatchFixed())
    actions = decider.decide_batch("g", None, [])
    assert [a.kind for a in actions] == ["extract"]
    assert decider.failovers == 1


# ---------------------------------------------------------------------------
# MCP goal-driven run with a failing primary endpoint
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, payload):
        self._body = (
            payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        )

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


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


def test_mcp_goal_run_fails_over_to_fallback_endpoint(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_DECIDER", "openai")
    monkeypatch.setenv("GHOST_HANDS_BASE_URL", "http://primary.local")
    monkeypatch.setenv("GHOST_HANDS_API_KEY", "k")
    monkeypatch.setenv("GHOST_HANDS_FALLBACK_BASE_URL", "http://fallback.local")

    def router(req, timeout=None):
        if "primary.local" in req.full_url:
            return _Resp(b"this is not json")
        content = json.dumps({"kind": "done", "summary": "fallback drove it"})
        return _Resp(
            {
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 1},
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", router)
    session = mcp_server.HandsSession()
    out = json.loads(_call(session, "hands_run", {"goal": "survive outage"}))
    assert out["stop_reason"] == "done", out
    assert out["summary"] == "fallback drove it"


# ---------------------------------------------------------------------------
# costs CLI
# ---------------------------------------------------------------------------


def _write_trail(path, events):
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


def test_costs_aggregates_reported_usage_only(tmp_path, capsys):
    trail_a = Trail()
    trail_a.record("perceive", 0, goal="first goal", url="https://x.local/")
    trail_a.record(
        "stop",
        1,
        reason="done",
        summary="ok",
        llm_prompt_tokens=120,
        llm_completion_tokens=30,
        llm_calls=2,
    )
    trail_b = Trail()
    trail_b.record("perceive", 0, goal="scripted goal", url="https://x.local/")
    trail_b.record("stop", 1, reason="done", summary="ok")
    fa = tmp_path / "a.jsonl"
    fb = tmp_path / "b.jsonl"
    _write_trail(fa, trail_a.events)
    _write_trail(fb, trail_b.events)
    rc = cli.main(["costs", str(fa), str(fb)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "150 tok / 2 calls" in out
    assert "usage not reported" in out
    assert "TOTAL: 150 tokens (120 prompt + 30 completion)" in out
    assert "1 run(s) reported no usage" in out


def test_costs_json_and_missing_file(tmp_path, capsys):
    rc = cli.main(["costs", str(tmp_path / "nope.jsonl")])
    assert rc == 2
