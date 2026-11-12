"""v1.5 features: verify absent-text assertions, the check preflight
command, and resume-from-trail."""

from __future__ import annotations

import json

from ghost_hands import cli
from ghost_hands.actions import Action
from ghost_hands.deciders import ScriptedDecider
from ghost_hands.drivers import DEMO_PAGES, FakeDriver
from ghost_hands.resume import load_resume_context, resume_note
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail


# ---------------------------------------------------------------------------
# absent_text verify
# ---------------------------------------------------------------------------


def test_absent_text_passes_when_text_is_gone():
    steps = [
        {"kind": "verify", "absent_text": "No such error anywhere"},
        {"kind": "done", "summary": "clean"},
    ]
    report = Runner(
        FakeDriver(DEMO_PAGES), ScriptedDecider(steps), trail=Trail()
    ).run("g")
    assert report.stop_reason == "done", report.summary


def test_absent_text_fails_when_text_is_present():
    steps = [{"kind": "verify", "absent_text": "Ghost Demo"}]
    report = Runner(
        FakeDriver(DEMO_PAGES), ScriptedDecider(steps), trail=Trail()
    ).run("g")
    assert report.stop_reason == "error"
    assert "still on the page" in report.summary


def test_absent_text_round_trips_and_combines():
    action = Action.from_dict(
        {"kind": "verify", "text": "Search", "absent_text": "Error"}
    )
    assert action.absent_text == "Error"
    assert action.to_dict()["absent_text"] == "Error"
    steps = [
        {"kind": "verify", "text": "Search", "absent_text": "Error"},
        {"kind": "done", "summary": "both held"},
    ]
    report = Runner(
        FakeDriver(DEMO_PAGES), ScriptedDecider(steps), trail=Trail()
    ).run("g")
    assert report.stop_reason == "done", report.summary


# ---------------------------------------------------------------------------
# check preflight
# ---------------------------------------------------------------------------


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_check_clean_script_exits_zero(tmp_path, capsys):
    script = _write(
        tmp_path / "ok.json",
        {
            "steps": [
                {"kind": "type", "target": 1, "text": "ghost"},
                {"kind": "verify", "absent_text": "Error"},
                {"kind": "done", "summary": "x"},
            ]
        },
    )
    assert cli.main(["check", script]) == 0
    out = capsys.readouterr().out
    assert "3 step(s): 3 allow" in out


def test_check_missing_secret_and_invalid_step_exit_one(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GHOST_HANDS_SECRET_DEMO_PW", raising=False)
    script = _write(
        tmp_path / "bad.json",
        [
            {"kind": "type", "target": 1, "text": "{{secret:DEMO_PW}}"},
            {"kind": "teleport"},
        ],
    )
    assert cli.main(["check", script]) == 1
    out = capsys.readouterr().out
    assert "MISSING" in out
    assert "INVALID" in out


def test_check_ask_is_reported_not_failed(tmp_path, capsys):
    script = _write(
        tmp_path / "ask.json",
        [
            {
                "kind": "fill_form",
                "fields": {"Name": "ghost"},
                "submit": True,
            },
            {"kind": "done", "summary": "x"},
        ],
    )
    rc = cli.main(["check", script])
    out = capsys.readouterr().out
    assert "-> ask" in out
    assert rc == 0


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


def _write_trail(path, events):
    path.write_text(
        "\n".join(json.dumps(ev) for ev in events) + "\n", encoding="utf-8"
    )


def test_resume_context_and_seeded_run(tmp_path):
    # A first run that dies on its step budget at the results page.
    trail = Trail()
    first = Runner(
        FakeDriver(DEMO_PAGES),
        ScriptedDecider(
            [
                {"kind": "type", "target": 1, "text": "ghost"},
                {"kind": "click", "target": 2},
                {"kind": "extract"},
            ]
        ),
        trail=trail,
        step_budget=2,
    ).run("search the demo web")
    assert first.stop_reason == "budget"
    path = tmp_path / "dead.jsonl"
    _write_trail(path, trail.events)

    ctx = load_resume_context(path)
    assert ctx["goal"] == "search the demo web"
    assert ctx["start_url"] == "https://demo.local/results"
    assert ctx["executed_steps"] == 2
    assert ctx["stop_reason"] == "budget"
    note = resume_note(ctx)
    assert "Resumed run" in note and "budget" in note

    class Recording:
        def __init__(self, steps):
            self.inner = ScriptedDecider(steps)
            self.histories = []

        def decide(self, goal, element_map, history):
            self.histories.append([dict(h) for h in history])
            return self.inner.decide(goal, element_map, history)

    decider = Recording([{"kind": "done", "summary": "finished it"}])
    driver = FakeDriver(DEMO_PAGES, start_url=ctx["start_url"])
    second = Runner(
        driver,
        decider,
        trail=Trail(),
        initial_history=[{"kind": "system", "note": note}],
    ).run(ctx["goal"])
    assert second.stop_reason == "done", second.summary
    first_history = decider.histories[0]
    assert any(
        h.get("note", "").startswith("Resumed run") for h in first_history
    )
