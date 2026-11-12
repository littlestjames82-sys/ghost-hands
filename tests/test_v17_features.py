"""v1.7 features: chain-linked resume, real usage in the eval
scorecard, and the doctor's deciders row."""

from __future__ import annotations

import json

from ghost_hands import doctor, evaluate
from ghost_hands.deciders import ScriptedDecider
from ghost_hands.drivers import DEMO_PAGES, FakeDriver
from ghost_hands.resume import load_resume_context
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail, verify_trail


def _dead_run(tmp_path):
    path = tmp_path / "parent.jsonl"
    trail = Trail(path)
    runner = Runner(
        FakeDriver(DEMO_PAGES),
        ScriptedDecider([{"kind": "click", "target": 999}]),
        trail=trail,
    )
    runner.run("book the thing")
    return path, trail


def test_resume_context_carries_parent_hash(tmp_path):
    path, trail = _dead_run(tmp_path)
    ctx = load_resume_context(path)
    assert trail.events[-1]["hash"]
    assert ctx["parent_hash"] == trail.events[-1]["hash"]


def test_resumed_trail_chains_to_parent(tmp_path):
    path, trail = _dead_run(tmp_path)
    ctx = load_resume_context(path)
    child = Trail()
    runner = Runner(
        FakeDriver(DEMO_PAGES, start_url=ctx["start_url"]),
        ScriptedDecider([{"kind": "done", "summary": "continued"}]),
        trail=child,
        parent_trail_hash=ctx["parent_hash"] or None,
    )
    report = runner.run(ctx["goal"] or "continue")
    assert report.stop_reason == "done"
    first = child.events[0]
    assert first["type"] == "perceive"
    assert first["parent_hash"] == trail.events[-1]["hash"]
    # The child's own chain still verifies end to end.
    assert verify_trail(child.events)["ok"] is True


def test_scorecard_shows_actual_usage_when_present():
    payload = {
        "decider": "openai (some-model)",
        "totals": {
            "tasks": 2,
            "succeeded": 2,
            "steps": 9,
            "wall_s": 3.5,
            "est_tokens": 4000,
            "approvals_requested": 1,
            "heals": 0,
            "llm_prompt_tokens": 8123,
            "llm_completion_tokens": 456,
            "llm_calls": 7,
        },
        "tasks": [],
    }
    card = evaluate.render_scorecard([payload])
    assert "Actual model usage (API-reported)" in card
    assert "8,123 prompt" in card
    payload["totals"]["llm_calls"] = 0
    card2 = evaluate.render_scorecard([payload])
    assert "Actual model usage" not in card2


def test_eval_scores_carry_usage_fields():
    scores, _label = evaluate.run_eval(
        decider_kind="rules",
        task_names=["multi-hop-fact", "trap-avoidance"],
    )
    for score in scores:
        d = score.to_dict()
        assert d["llm_calls"] == 0  # rules decider makes no model calls
        assert d["llm_prompt_tokens"] == 0
    totals = evaluate.suite_totals(scores)
    assert totals["llm_calls"] == 0


def test_doctor_deciders_row(monkeypatch):
    env = {
        "GHOST_HANDS_ANTHROPIC_API_KEY": "sk-x",
        "GHOST_HANDS_FALLBACK_BASE_URL": "http://backup.local/v1",
        "GHOST_HANDS_DECIDER": "anthropic",
    }
    row = doctor._check_deciders(env)
    assert row.status == doctor.PASS
    assert "anthropic" in row.detail
    assert "fallback endpoint set" in row.detail
    assert "sk-x" not in row.detail  # names only, never values
    empty = doctor._check_deciders({})
    assert empty.status == doctor.WARN
    names = [c.name for c in doctor.run_doctor(env={})]
    assert "deciders" in names
