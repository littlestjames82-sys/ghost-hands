"""Policy packs (v0.4): readonly / standard / strict as loadable data,
plus the CLI and MCP wiring that consumes them."""

import json

import pytest

from ghost_hands import actions as A
from ghost_hands.errors import HandsError
from ghost_hands.governor import ALLOW, ASK, DENY, Policy
from ghost_hands.policies import describe_packs, load_pack, pack_names
from pages_fixtures import LOGIN_PAGES, SHOP_PAGES


# -- the packs themselves -----------------------------------------------------


def test_pack_names():
    assert pack_names() == ["readonly", "standard", "strict"]


def test_standard_is_the_longstanding_default():
    pack = load_pack("standard")
    assert pack.policy == Policy()  # readonly/write allow, consequential ask
    assert pack.approval_timeout == 600.0


def test_strict_asks_for_writes_with_a_short_leash():
    pack = load_pack("strict")
    assert pack.policy.readonly == ALLOW
    assert pack.policy.write == ASK
    assert pack.policy.consequential == ASK
    assert pack.approval_timeout == 120.0


def test_readonly_denies_state_changes():
    pack = load_pack("readonly")
    assert pack.policy.readonly == ALLOW
    assert pack.policy.write == DENY
    assert pack.policy.consequential == DENY


def test_unknown_pack_is_a_loud_error():
    with pytest.raises(HandsError) as excinfo:
        load_pack("yolo")
    message = str(excinfo.value)
    assert "unknown policy pack" in message
    assert "readonly" in message and "strict" in message


def test_pack_merges_with_base_domains():
    base = Policy(allowed_domains=("example.com",), blocked_domains=("evil.com",))
    pack = load_pack("strict", base=base)
    assert pack.policy.blocked_domains == ("evil.com",)
    assert pack.policy.allowed_domains == ("example.com",)
    # ...but the pack's class outcomes win over the base's.
    assert pack.policy.write == ASK


def test_describe_packs_is_data():
    described = {p["name"]: p for p in describe_packs()}
    assert described["readonly"]["policy"]["write"] == "deny"
    assert described["strict"]["approval_timeout"] == 120.0


# -- behavior through the Runner ------------------------------------------------


def test_readonly_pack_denies_a_write_and_trails_it(run_fake):
    pack = load_pack("readonly")
    _driver, trail, report = run_fake(
        LOGIN_PAGES,
        [{"kind": "type", "target": 1, "text": "ghost"}],
        policy=pack.policy,
        start="https://app.local/login",
    )
    assert report.stop_reason == "denied"
    govern = [ev for ev in trail.events if ev["type"] == "govern"]
    assert govern[-1]["classification"] == "write"
    assert govern[-1]["outcome"] == "deny"
    assert not [ev for ev in trail.events if ev["type"] == "execute"]


def test_readonly_pack_still_allows_perception(run_fake):
    pack = load_pack("readonly")
    _driver, _trail, report = run_fake(
        SHOP_PAGES,
        [{"kind": "extract"}],
        policy=pack.policy,
        start="https://shop.local/",
    )
    assert report.stop_reason == "done"


def test_standard_pack_write_runs_consequential_asks(run_fake):
    pack = load_pack("standard")
    _driver, _trail, report = run_fake(
        SHOP_PAGES,
        [
            {"kind": "click", "target": 1},  # link -> cart (readonly)
            {"kind": "click", "target": 1},  # submit (consequential)
        ],
        policy=pack.policy,
        start="https://shop.local/",
    )
    # No approver: the consequential submit is denied, exactly as before.
    assert report.stop_reason == "denied"
    assert report.actions_by_class.get("consequential") == 1


def test_strict_pack_write_asks_then_runs_with_approver(run_fake):
    pack = load_pack("strict")
    seen = []

    def approver(action):
        seen.append(action.kind)
        return True

    _driver, _trail, report = run_fake(
        LOGIN_PAGES,
        [
            {"kind": "type", "target": 1, "text": "ghost"},
            {"kind": "type", "target": 2, "text": "hunter2"},
        ],
        approver=approver,
        policy=pack.policy,
        start="https://app.local/login",
    )
    assert report.stop_reason == "done"
    assert seen == ["type", "type"]  # every write asked under strict


def test_strict_pack_write_denied_without_approver(run_fake):
    pack = load_pack("strict")
    _driver, trail, report = run_fake(
        LOGIN_PAGES,
        [{"kind": "type", "target": 1, "text": "ghost"}],
        policy=pack.policy,
        start="https://app.local/login",
    )
    assert report.stop_reason == "denied"
    govern = [ev for ev in trail.events if ev["type"] == "govern"]
    assert govern[-1]["outcome"] == "deny"
    assert "no approver" in govern[-1]["reason"]


# -- CLI + MCP wiring -------------------------------------------------------------


def test_cli_run_with_policy_pack(tmp_path):
    from ghost_hands.cli import main

    steps = tmp_path / "steps.json"
    steps.write_text(json.dumps([{"kind": "type", "target": 1, "text": "x"}]))
    trail = tmp_path / "trail.jsonl"
    rc = main(
        [
            "run",
            "--script",
            str(steps),
            "--trail",
            str(trail),
            "--policy-pack",
            "readonly",
        ]
    )
    # The demo-web search field is a write; readonly denies it.
    assert rc == 1
    events = [json.loads(line) for line in trail.read_text().splitlines()]
    assert any(
        ev["type"] == "govern" and ev["outcome"] == "deny" for ev in events
    )


def test_cli_run_policy_and_pack_conflict(tmp_path, capsys):
    from ghost_hands.cli import main

    steps = tmp_path / "steps.json"
    steps.write_text("[]")
    policy = tmp_path / "policy.json"
    policy.write_text("{}")
    rc = main(
        [
            "run",
            "--script",
            str(steps),
            "--policy",
            str(policy),
            "--policy-pack",
            "strict",
            "--trail",
            str(tmp_path / "t.jsonl"),
        ]
    )
    assert rc == 2
    assert "alternatives" in capsys.readouterr().err


def test_mcp_honors_policy_env(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_POLICY", "strict")
    from ghost_hands.mcp_server import HandsSession

    session = HandsSession()
    assert session.policy_pack is not None
    assert session.policy_pack.name == "strict"
    assert session.governor.policy.write == ASK


def test_mcp_unknown_policy_env_fails_loudly(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_POLICY", "wide-open")
    from ghost_hands.mcp_server import HandsSession

    with pytest.raises(HandsError):
        HandsSession()


def test_mcp_readonly_env_denies_hands_act(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_POLICY", "readonly")
    from ghost_hands.mcp_server import HandsSession, handle_request

    session = HandsSession()
    handle_request(
        session,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": "hands_perceive", "arguments": {}}},
    )
    resp = handle_request(
        session,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "hands_act",
                    "arguments": {"action": {"kind": "type", "target": 1, "text": "x"}}}},
    )
    text = resp["result"]["content"][0]["text"]
    assert text.startswith("DENIED (write)")
