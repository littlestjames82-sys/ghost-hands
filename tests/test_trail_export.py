import ast
import json

from ghost_hands.deciders import ScriptedDecider
from ghost_hands.drivers import DEMO_PAGES, FakeDriver, demo_steps
from ghost_hands.export import export_ghost_hands_script
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail, accounting, read_trail


def _demo_trail():
    trail = Trail()
    Runner(FakeDriver(DEMO_PAGES), ScriptedDecider(demo_steps()), trail=trail).run("demo")
    return trail


def test_trail_event_order_and_types():
    trail = _demo_trail()
    types = [e["type"] for e in trail.events]
    assert types[0] == "perceive"
    assert types[-1] == "stop"
    assert "govern" in types and "execute" in types and "result" in types
    assert all("ts" in e and "step" in e for e in trail.events)


def test_trail_file_roundtrip(tmp_path):
    path = tmp_path / "t.jsonl"
    trail = Trail(path)
    Runner(FakeDriver(DEMO_PAGES), ScriptedDecider(demo_steps()), trail=trail).run("demo")
    events = read_trail(path)
    assert len(events) == len(trail.events)
    assert events[-1]["type"] == "stop"


def test_accounting_counts():
    trail = _demo_trail()
    acct = accounting(trail.events)
    executed = sum(1 for e in trail.events if e["type"] == "execute")
    assert acct["steps"] == executed == 8
    govern = sum(1 for e in trail.events if e["type"] == "govern")
    assert sum(acct["actions_by_class"].values()) == govern


def test_accounting_token_estimate():
    events = [
        {"type": "perceive", "step": 1, "map_chars": 1000},
        {"type": "perceive", "step": 2, "map_chars": 400},
    ]
    acct = accounting(events)
    assert acct["map_chars"] == 1400
    assert acct["est_tokens"] == 350


def test_export_is_valid_python():
    src = export_ghost_hands_script(_demo_trail().events)
    ast.parse(src)


def test_export_encodes_same_steps():
    events = _demo_trail().events
    src = export_ghost_hands_script(events)
    executed = [e for e in events if e["type"] == "execute"]
    for ev in executed:
        assert json.dumps(ev["action"]) in src
    assert "https://demo.local/todo" in src
    assert "Ship Ghost Hands" in src


def test_export_is_ghost_hands_not_a_harness():
    src = export_ghost_hands_script(_demo_trail().events)
    assert "from ghost_hands import" in src
    assert "ChromiumDriver" in src
    assert "Graduated from a Ghost Hands trail" in src
    assert "playwright" not in src.lower()
    assert "selenium" not in src.lower()


def test_export_includes_descriptors():
    src = export_ghost_hands_script(_demo_trail().events)
    assert '# [1] <input> "Search the demo web"' in src or '<input>' in src
