"""GhostGuard backend tests (Phase B): the opt-in GhostGuard governor
for Ghost Hands, and its parity with the shared conformance fixtures.

The parity tests drive every case in
``ghostguard/conformance/fixtures.json`` through this package's
GhostGuardGovernor.gate() — the Bridge Spec v1 client over the real
gate bridge — and assert the shared fields (verdict, rule_id,
handoff_to) match the fixture exactly, including the hostile-overlay
case where the locked no-spend-ever rule must hold.

Skipped (loudly, via pytest.skip at collection of the fixture-
dependent class) only when the sibling GhostGuard checkout is not
present next to this repo — this is a stack test, not a unit test of
something Ghost Hands ships alone.
"""

import json
from pathlib import Path

import pytest

from ghost_hands import actions as A
from ghost_hands.errors import HandsError
from ghost_hands.eyes import ElementMap
from ghost_hands.ghostguard import (
    GhostGuardBridgeError,
    GhostGuardGovernor,
    default_bridge_path,
    ghostguard_dir,
    governor_from_env,
)
from ghost_hands.governor import Governor


def _fixtures_path() -> Path:
    return ghostguard_dir() / "conformance" / "fixtures.json"


FIXTURES_PATH = _fixtures_path()
FIXTURES = None
if FIXTURES_PATH.exists() and default_bridge_path().exists():
    FIXTURES = json.loads(FIXTURES_PATH.read_text(encoding="utf8"))

requires_stack = pytest.mark.skipif(
    FIXTURES is None,
    reason="GhostGuard checkout (conformance fixtures + gate bridge) not found beside this repo",
)


def el(html, text):
    m = ElementMap.from_html(html)
    found = m.find_by_text(text)
    assert found is not None
    return found


@pytest.fixture()
def backend(tmp_path):
    return GhostGuardGovernor(audit_path=str(tmp_path / "ghostguard-audit.jsonl"))


# ── Conformance parity: the same fixtures the TS runner runs ────────


@requires_stack
class TestConformanceParity:
    def test_every_fixture_case_decides_identically(self, backend):
        cases = FIXTURES["cases"]
        assert len(cases) >= 10
        for case in cases:
            record = backend.gate(
                case["intent"],
                approved=case["approved"],
                policy_overlay=case["policy_overlay"],
            )
            expected = case["expected"]
            assert record["verdict"] == expected["verdict"], case["id"]
            assert record["rule_id"] == expected["rule_id"], case["id"]
            assert record["handoff_to"] == expected["handoff_to"], case["id"]

    def test_locked_no_spend_holds_against_hostile_overlay(self, backend):
        case = next(
            c for c in FIXTURES["cases"] if c["id"] == "deny-spend-hostile-overlay"
        )
        record = backend.gate(
            case["intent"],
            approved=case["approved"],
            policy_overlay=case["policy_overlay"],
        )
        assert record["verdict"] == "deny"
        assert record["rule_id"] == "no-spend-ever"

    def test_records_are_written_before_reply(self, backend, tmp_path):
        # Record-first across the process boundary: when gate()
        # returns, the record is already in the bridge-written audit,
        # in order, exactly as returned.
        audit = Path(backend.audit_path)
        returned = []
        for case in FIXTURES["cases"][:3]:
            returned.append(
                backend.gate(
                    case["intent"],
                    approved=case["approved"],
                    policy_overlay=case["policy_overlay"],
                )
            )
        lines = audit.read_text(encoding="utf8").strip().split("\n")
        assert [json.loads(line) for line in lines] == returned


# ── Native-action translation through evaluate() ────────────────────


@requires_stack
class TestNativeEvaluation:
    def test_readonly_navigation_allows(self, backend):
        d = backend.evaluate(A.navigate("https://fixture.test/"))
        assert d.outcome == "allow"
        assert d.classification == "readonly"
        assert "allow-read-only" in d.reason

    def test_submit_click_asks(self, backend):
        e = el(
            '<form action="/go"><button type="submit">Find stays</button></form>',
            "Find stays",
        )
        d = backend.evaluate(A.click(e.number), e, page_url="https://fixture.test/")
        assert d.outcome == "ask"
        assert "network-writes-ask" in d.reason

    def test_buy_click_denies_via_spend_upgrade(self, backend):
        e = el("<button>Buy gift card</button>", "Buy")
        d = backend.evaluate(A.click(e.number), e, page_url="https://fixture.test/")
        assert d.outcome == "deny"
        assert "no-spend-ever" in d.reason
        assert backend.last_record["permission_class"] == "SPEND"

    def test_fixture_fill_pre_approved(self, backend):
        e = el('<input placeholder="Destination">', "Destination")
        d = backend.evaluate(
            A.type(e.number, "Lisbon"), e, page_url="https://fixture.test/search"
        )
        assert d.outcome == "allow"
        assert "fixture-fills-standing-approval" in d.reason
        assert backend.last_record["verdict"] == "pre_approved"


# ── Fail-closed and backend selection ───────────────────────────────


def test_bridge_unavailable_denies_via_evaluate():
    backend = GhostGuardGovernor(bridge_path="/nonexistent/ghostguard-gate.mjs")
    d = backend.evaluate(A.navigate("https://fixture.test/"))
    assert d.outcome == "deny"
    assert "ghostguard" in d.reason


def test_bridge_unavailable_raises_via_gate():
    backend = GhostGuardGovernor(bridge_path="/nonexistent/ghostguard-gate.mjs")
    with pytest.raises(GhostGuardBridgeError):
        backend.gate(
            {
                "action_id": "x1",
                "kind": "browser.navigate",
                "permission_class": "READ",
                "actor": "test",
                "initiator": "person",
                "project_id": "",
                "action_payload": {},
            }
        )


def test_unwritable_audit_denies(tmp_path):
    # A directory used as the audit path can never be appended to:
    # the bridge fails to write the record, replies nothing, and
    # evaluate() fails closed.
    backend = GhostGuardGovernor(audit_path=str(tmp_path))
    d = backend.evaluate(A.navigate("https://fixture.test/"))
    assert d.outcome == "deny"


def test_governor_from_env_defaults_to_native():
    gov = governor_from_env({})
    assert isinstance(gov, Governor)
    assert not isinstance(gov, GhostGuardGovernor)


@requires_stack
def test_governor_from_env_selects_ghostguard():
    gov = governor_from_env({"GHOST_HANDS_GOVERNOR_BACKEND": "ghostguard"})
    assert isinstance(gov, GhostGuardGovernor)


def test_governor_from_env_rejects_unknown_backend():
    with pytest.raises(HandsError):
        governor_from_env({"GHOST_HANDS_GOVERNOR_BACKEND": "playwright"})
