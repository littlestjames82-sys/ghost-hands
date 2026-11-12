"""v1.0 features: tamper-evident trail, secret placeholders, the
verify action, and the governed plan cache."""

from __future__ import annotations

import json
import os

import pytest

from ghost_hands import (
    FakeDriver,
    Governor,
    PlanCache,
    Runner,
    ScriptedDecider,
    SecretProvider,
    Trail,
    verify_trail,
)
from ghost_hands.actions import verify as verify_action
from ghost_hands.cache import plan_key
from ghost_hands.governor import classify
from ghost_hands.secrets import action_secret_refs, resolve_action
from ghost_hands.actions import Action

PAGES = {
    "https://app.local/login": """<!doctype html><html><head><title>Login</title></head>
<body>
<h1>Sign in</h1>
<input id="user" name="user" type="text" placeholder="Username">
<input id="pw" name="pw" type="password" placeholder="Password">
<button data-nav="https://app.local/home">Sign in</button>
</body></html>""",
    "https://app.local/home": """<!doctype html><html><head><title>Home</title></head>
<body>
<h1>Welcome back</h1>
<p>Your dashboard is ready.</p>
<a href="https://app.local/login">Sign out</a>
</body></html>""",
}

LOGIN_STEPS = [
    {"kind": "type", "target": 1, "text": "ryan"},
    {"kind": "type", "target": 2, "text": "{{secret:PASSWORD}}"},
    {"kind": "click", "target": 3},
    {"kind": "verify", "text": "Welcome back"},
    {"kind": "done", "summary": "logged in and verified"},
]

SECRET = "correct-horse-battery-42"


def _run(pages=None, steps=None, goal="log in", **runner_kwargs):
    driver = FakeDriver(pages or PAGES, start_url="https://app.local/login")
    trail = Trail()
    runner = Runner(
        driver,
        ScriptedDecider(steps if steps is not None else LOGIN_STEPS),
        trail=trail,
        **runner_kwargs,
    )
    report = runner.run(goal)
    return driver, trail, report


# ---------------------------------------------------------------------------
# Tamper-evident trail
# ---------------------------------------------------------------------------


def test_trail_chain_verifies_intact():
    trail = Trail()
    trail.record("perceive", 1, url="https://x.local")
    trail.record("decide", 1, action={"kind": "extract"})
    trail.record("stop", 1, reason="done", summary="ok")
    verdict = verify_trail(trail.events)
    assert verdict["ok"] is True
    assert verdict["hashed"] == 3
    assert trail.events[0]["prev"] == "genesis"
    assert trail.events[1]["prev"] == trail.events[0]["hash"]


def test_trail_chain_detects_edit():
    trail = Trail()
    trail.record("perceive", 1, url="https://x.local")
    trail.record("decide", 1, action={"kind": "click", "target": 2})
    trail.record("stop", 1, reason="done", summary="ok")
    tampered = json.loads(json.dumps(trail.events))
    tampered[1]["action"]["target"] = 99
    verdict = verify_trail(tampered)
    assert verdict["ok"] is False
    assert verdict["first_bad"] == 1


def test_trail_chain_detects_deletion():
    trail = Trail()
    for i in range(3):
        trail.record("perceive", i + 1, url="https://x.local")
    cut = trail.events[:1] + trail.events[2:]
    assert verify_trail(cut)["ok"] is False


def test_trail_legacy_reported_not_passed():
    legacy = [{"type": "perceive", "step": 1}, {"type": "stop", "step": 1}]
    verdict = verify_trail(legacy)
    assert verdict["ok"] is False
    assert verdict["hashed"] == 0
    assert "legacy" in verdict["reason"]


def test_real_run_trail_chain_verifies(tmp_path):
    path = tmp_path / "trail.jsonl"
    driver = FakeDriver(PAGES, start_url="https://app.local/login")
    runner = Runner(
        driver,
        ScriptedDecider(LOGIN_STEPS),
        trail=Trail(path),
        secrets=SecretProvider({"PASSWORD": SECRET}),
    )
    report = runner.run("log in")
    assert report.stop_reason == "done"
    from ghost_hands import read_trail

    verdict = verify_trail(read_trail(path))
    assert verdict["ok"] is True, verdict


def test_verify_trail_cli(tmp_path):
    from ghost_hands.cli import main

    path = tmp_path / "t.jsonl"
    trail = Trail(path)
    trail.record("perceive", 1, url="https://x.local")
    assert main(["verify-trail", str(path)]) == 0
    # break the chain on disk
    lines = path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    event["url"] = "https://evil.local"
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    assert main(["verify-trail", str(path)]) == 1


# ---------------------------------------------------------------------------
# Secret placeholders
# ---------------------------------------------------------------------------


def test_secret_refs_and_resolve():
    action = Action(kind="type", target=2, text="{{secret:PASSWORD}}")
    assert action_secret_refs(action) == ["PASSWORD"]
    resolved, values, missing = resolve_action(
        action, SecretProvider({"PASSWORD": SECRET})
    )
    assert resolved.text == SECRET
    assert values == [SECRET]
    assert missing == []
    # the original action is untouched
    assert action.text == "{{secret:PASSWORD}}"


def test_secret_reaches_driver_but_never_trail():
    driver, trail, report = _run(
        secrets=SecretProvider({"PASSWORD": SECRET})
    )
    assert report.stop_reason == "done"
    # the driver got the real value
    assert SECRET in driver.fields.values()
    # the trail (the whole record) never did
    blob = json.dumps(trail.events)
    assert SECRET not in blob
    assert "{{secret:PASSWORD}}" in blob


def test_secret_from_environment(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_SECRET_PASSWORD", SECRET)
    driver, trail, report = _run()
    assert report.stop_reason == "done"
    assert SECRET in driver.fields.values()
    assert SECRET not in json.dumps(trail.events)


def test_missing_secret_is_an_honest_error():
    driver, trail, report = _run(secrets=SecretProvider({}))
    assert report.stop_reason == "error"
    assert "PASSWORD" in report.summary
    # and the placeholder was never typed anywhere
    assert all("{{secret" not in v for v in driver.fields.values())


def test_fill_form_secret():
    pages = {
        "https://f.local/": """<!doctype html><html><head><title>F</title></head>
<body>
<label>Api key <input id="k" name="k" type="text"></label>
<button>Save</button>
</body></html>"""
    }
    steps = [
        {"kind": "fill_form", "fields": {"Api key": "{{secret:API_KEY}}"}},
        {"kind": "done", "summary": "filled"},
    ]
    driver = FakeDriver(pages, start_url="https://f.local/")
    trail = Trail()
    report = Runner(
        driver, ScriptedDecider(steps), trail=trail,
        secrets=SecretProvider({"API_KEY": "key-123-secret"}),
    ).run("fill")
    assert report.stop_reason == "done"
    assert "key-123-secret" in driver.fields.values()
    assert "key-123-secret" not in json.dumps(trail.events)


# ---------------------------------------------------------------------------
# Verify action
# ---------------------------------------------------------------------------


def test_verify_is_readonly():
    assert classify(verify_action(text="x")) == "readonly"


def test_verify_success_and_failure():
    ok_steps = [
        # one verify action may carry several conditions; all must hold
        {"kind": "verify", "text": "Sign in", "target": 1,
         "url": "app.local/login"},
        {"kind": "done", "summary": "all verified"},
    ]
    _, _, report = _run(steps=ok_steps)
    assert report.stop_reason == "done"

    bad_steps = [
        {"kind": "verify", "text": "Text that is nowhere"},
        {"kind": "done", "summary": "should not reach"},
    ]
    _, trail, report = _run(steps=bad_steps)
    assert report.stop_reason == "error"
    assert "verification failed" in report.summary
    results = [e for e in trail.events if e["type"] == "result"]
    assert results and results[-1]["ok"] is False


def test_verify_no_conditions_fails_honestly():
    steps = [{"kind": "verify"}, {"kind": "done", "summary": "x"}]
    _, _, report = _run(steps=steps)
    assert report.stop_reason == "error"
    assert "at least one" in report.summary


# ---------------------------------------------------------------------------
# Governed plan cache
# ---------------------------------------------------------------------------


class CountingDecider:
    """Wraps ScriptedDecider and counts calls."""

    def __init__(self, steps):
        self.inner = ScriptedDecider(steps)
        self.calls = 0

    def decide(self, goal, element_map, history):
        self.calls += 1
        return self.inner.decide(goal, element_map, history)


def test_plan_cache_replays_without_decider_calls():
    cache = PlanCache()
    # First run: learns the plan.
    driver1 = FakeDriver(PAGES, start_url="https://app.local/login")
    decider1 = CountingDecider(LOGIN_STEPS)
    runner1 = Runner(
        driver1, decider1, trail=Trail(), plan_cache=cache,
        secrets=SecretProvider({"PASSWORD": SECRET}),
    )
    assert runner1.run("log in").stop_reason == "done"
    assert len(cache) == 1
    assert decider1.calls == len(LOGIN_STEPS)

    # Second run: the decider would do the wrong thing — but a fully
    # replayed plan is itself the proof (it only exists because the
    # proving run ended done, and its verify step re-passed), so the
    # decider is never consulted at all.
    wrong = CountingDecider(
        [{"kind": "done", "summary": "decider was consulted"}]
    )
    driver2 = FakeDriver(PAGES, start_url="https://app.local/login")
    trail2 = Trail()
    runner2 = Runner(
        driver2, wrong, trail=trail2, plan_cache=cache,
        secrets=SecretProvider({"PASSWORD": SECRET}),
    )
    report2 = runner2.run("log in")
    assert report2.stop_reason == "done"
    assert report2.summary == "logged in and verified"
    assert wrong.calls == 0
    assert driver2.fields.get("user") == "ryan"
    assert SECRET in driver2.fields.values()
    # cached steps are marked, and the replay's trail still verifies
    cache_events = [e for e in trail2.events if e["type"] == "cache"]
    assert any(e["phase"] == "hit" for e in cache_events)
    assert verify_trail(trail2.events)["ok"] is True
    # and the secret never entered either trail
    assert SECRET not in json.dumps(trail2.events)


def test_plan_cache_falls_back_when_page_changes():
    cache = PlanCache()
    driver1 = FakeDriver(PAGES, start_url="https://app.local/login")
    Runner(
        driver1, ScriptedDecider(LOGIN_STEPS), trail=Trail(),
        plan_cache=cache, secrets=SecretProvider({"PASSWORD": SECRET}),
    ).run("log in")
    assert len(cache) == 1

    # Same goal, but the login page lost its password field: the
    # cached type step cannot be re-found, so the live decider runs.
    changed = dict(PAGES)
    changed["https://app.local/login"] = """<!doctype html><html><head><title>Login</title></head>
<body>
<h1>Sign in</h1>
<input id="user" name="user" type="text" placeholder="Username">
<button data-nav="https://app.local/home">Sign in</button>
</body></html>"""
    fallback = CountingDecider(
        [
            {"kind": "type", "target": 1, "text": "ryan"},
            {"kind": "click", "target": 2},
            {"kind": "done", "summary": "fallback path"},
        ]
    )
    driver2 = FakeDriver(changed, start_url="https://app.local/login")
    trail2 = Trail()
    report = Runner(
        driver2, fallback, trail=trail2, plan_cache=cache,
    ).run("log in")
    assert report.stop_reason == "done"
    assert fallback.calls >= 2
    cache_events = [e for e in trail2.events if e["type"] == "cache"]
    assert any(e["phase"] == "fallback" for e in cache_events)


def test_failed_run_stores_nothing():
    cache = PlanCache()
    bad = [
        {"kind": "verify", "text": "nowhere to be found"},
        {"kind": "done", "summary": "unreached"},
    ]
    _run(steps=bad, plan_cache=cache)
    assert len(cache) == 0


def test_plan_cache_file_is_owner_only_and_roundtrips(tmp_path):
    path = tmp_path / "plans.json"
    cache = PlanCache(path)
    key = cache.store("Log In ", "https://app.local/login", [
        {"action": {"kind": "extract"}, "descriptor": None}
    ])
    assert key == plan_key("log in", "https://app.local/login")
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    reopened = PlanCache(path)
    entry = reopened.lookup("log in", "https://app.local/login")
    assert entry is not None and entry["steps"][0]["action"]["kind"] == "extract"
