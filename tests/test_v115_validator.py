"""v1.15 features (evidence-checklist validator, roadmap P1-4):

With ``validate`` on, a ``done`` claim must satisfy an explicit
checklist built from the goal: each item is marked satisfied or not
WITH cited evidence drawn from the perception render and the run's
results. Exact values stated in the goal are compared IN CODE — a
judge claiming them satisfied cannot override a code check that
cannot find the value. An unsatisfied item loops the run back to
work with the objections (it is not a failure); after the
validation cap the run stops ``unverified``, never ``done``. The
checklist and every verdict are hash-chained trail events.
Validation is opt-in: off, behavior is the old behavior exactly.
"""

from __future__ import annotations

import pytest

from ghost_hands.deciders import FallbackDecider, ScriptedDecider
from ghost_hands.drivers import FakeDriver
from ghost_hands.errors import HandsError
from ghost_hands.runner import STOP_REASONS, Runner
from ghost_hands.trail import Trail, verify_trail
from ghost_hands.validator import (
    UNTRUSTED_DATA_BOUNDARY,
    ChecklistItem,
    DeterministicValidator,
    LLMValidator,
    ScriptedValidator,
    decompose_goal,
    validate_enabled,
)

# --------------------------------------------------------------------------
# Fixture: an application form whose submit paints a success banner
# immediately but only actually files (issuing a reference) when the
# required name field is filled — the premature-done trap.
# --------------------------------------------------------------------------

FORM_PAGE = """<!doctype html><html><head><title>Apply</title></head><body>
<h1>Expense application</h1>
<form>
<label>Full name <input id="name" name="name" type="text"></label>
<button type="submit">Submit application</button>
</form>
</body></html>"""

BANNER_PAGE = """<!doctype html><html><head><title>Apply</title></head><body>
<h1>Expense application</h1>
<p>Application received. Thank you!</p>
<form>
<label>Full name <input id="name" name="name" type="text"></label>
<button type="submit">Submit application</button>
</form>
</body></html>"""

CONFIRM_PAGE = """<!doctype html><html><head><title>Confirmed</title></head><body>
<h1>Application filed</h1>
<p>Reference: CONF-2026</p>
</body></html>"""

URL = "https://apply.local/form"
GOAL = "File the expense application and report confirmation reference CONF-2026"


class ApplicationDriver(FakeDriver):
    """Submit always paints the banner; the filing (and its
    reference) only happens when a field was actually filled."""

    def __init__(self, pages, start_url=None):
        super().__init__(pages, start_url=start_url)
        self.saved = False

    def act(self, action, element):
        result = super().act(action, element)
        if action.kind == "click" and element is not None and element.type == "submit":
            if any(str(v).strip() for v in self.fields.values()):
                self.saved = True
                self._overrides[self._url] = CONFIRM_PAGE
            else:
                self._overrides[self._url] = BANNER_PAGE
        return result


def _driver():
    return ApplicationDriver({URL: FORM_PAGE}, start_url=URL)


def _events(trail, type_):
    return [e for e in trail.events if e["type"] == type_]


NAIVE_STEPS = [
    {"kind": "click", "target": 2},
    {"kind": "done", "summary": "submitted"},
]
RESCUE_STEPS = [
    {"kind": "click", "target": 2},
    {"kind": "done", "summary": "submitted"},
    {"kind": "type", "target": 1, "text": "Ada Lovelace"},
    {"kind": "click", "target": 2},
    {"kind": "done", "summary": "filed, reference CONF-2026"},
]
CORRECT_STEPS = [
    {"kind": "type", "target": 1, "text": "Ada Lovelace"},
    {"kind": "click", "target": 2},
    {"kind": "done", "summary": "filed, reference CONF-2026"},
]


# --------------------------------------------------------------------------
# Runner behavior
# --------------------------------------------------------------------------


def test_premature_done_rejected_then_loop_back_to_real_done():
    driver, trail = _driver(), Trail()
    report = Runner(
        driver,
        ScriptedDecider(RESCUE_STEPS),
        approver=lambda a: True,
        trail=trail,
        validate=True,
    ).run(GOAL)
    assert report.stop_reason == "done", report.summary
    assert driver.saved  # the run did the real work after the loop-back
    # The checklist was built once; two verdicts: reject, then pass.
    assert len(_events(trail, "checklist")) == 1
    verdicts = _events(trail, "validation")
    assert [v["ok"] for v in verdicts] == [False, True]
    assert report.validations == 2 and report.validation_failures == 1
    # The rejection is on the record in the require-verify shape.
    rejections = [
        e for e in trail.events
        if e["type"] == "result" and not e["ok"] and "validator" in e["result"]
    ]
    assert len(rejections) == 1 and "CONF-2026" in rejections[0]["result"]


def test_checklist_and_verdicts_on_trail_and_chain_verifies():
    trail = Trail()
    Runner(
        _driver(),
        ScriptedDecider(RESCUE_STEPS),
        approver=lambda a: True,
        trail=trail,
        validate=True,
    ).run(GOAL)
    checklist = _events(trail, "checklist")[0]
    assert checklist["source"] == "DeterministicValidator"
    exacts = [i["exact"] for i in checklist["items"] if i["exact"]]
    assert exacts == ["CONF-2026"]
    passing = _events(trail, "validation")[1]
    item = next(i for i in passing["items"] if i["exact"] == "CONF-2026")
    assert item["satisfied"] is True
    assert "CONF-2026" in item["evidence"]  # cited verbatim from the page
    failing = _events(trail, "validation")[0]
    item0 = next(i for i in failing["items"] if i["exact"] == "CONF-2026")
    assert item0["satisfied"] is False and item0["note"]
    assert failing["objections"] and "CONF-2026" in failing["objections"][0]
    assert verify_trail(trail.events)["ok"]


def test_validation_cap_exhausted_stops_unverified_never_done():
    trail = Trail()
    report = Runner(
        _driver(),
        ScriptedDecider(NAIVE_STEPS + [{"kind": "done", "summary": "x"}] * 2),
        approver=lambda a: True,
        trail=trail,
        validate=True,
    ).run(GOAL)
    assert report.stop_reason == "unverified"
    assert "unverified" in STOP_REASONS and report.stop_reason != "done"
    assert "CONF-2026" in report.summary  # names the never-satisfied item
    assert report.validations == 3 and report.validation_failures == 3


def test_validation_attempts_is_configurable():
    report = Runner(
        _driver(),
        ScriptedDecider(NAIVE_STEPS),
        approver=lambda a: True,
        trail=Trail(),
        validate=True,
        validation_attempts=1,
    ).run(GOAL)
    assert report.stop_reason == "unverified"
    assert report.validations == 1


def test_activation_off_is_the_old_behavior():
    driver, trail = _driver(), Trail()
    report = Runner(
        driver,
        ScriptedDecider(NAIVE_STEPS),
        approver=lambda a: True,
        trail=trail,
    ).run(GOAL)
    # The false done is accepted, exactly as before v1.15 — the
    # trap is what the opt-in gate exists to catch.
    assert report.stop_reason == "done" and not driver.saved
    assert _events(trail, "checklist") == [] and _events(trail, "validation") == []
    assert report.validations == 0
    types = [e["type"] for e in trail.events]
    assert types == [
        "perceive", "decide", "govern", "execute", "result",
        "perceive", "decide", "stop",
    ]


def test_looped_back_actions_still_traverse_approval():
    calls = []

    def approver(action):
        calls.append(action.kind)
        return len(calls) == 1  # approve the first submit, decline the rest

    report = Runner(
        _driver(),
        ScriptedDecider(RESCUE_STEPS),
        approver=approver,
        trail=Trail(),
        validate=True,
    ).run(GOAL)
    # The validator sent the run back to work; the corrective submit
    # went through the normal approval path and was declined there.
    assert report.stop_reason == "denied"
    assert calls == ["click", "click"]


def test_explicit_scripted_validator_with_needle():
    validator = ScriptedValidator(
        [ChecklistItem(text="the filed confirmation is shown", needle="Application filed")]
    )
    report = Runner(
        _driver(),
        ScriptedDecider(CORRECT_STEPS),
        approver=lambda a: True,
        trail=Trail(),
        validate=True,
        validator=validator,
    ).run(GOAL)
    assert report.stop_reason == "done", report.summary
    assert report.validations == 1 and report.validation_failures == 0


# --------------------------------------------------------------------------
# The LLM path: a scripted decider with a complete() channel
# --------------------------------------------------------------------------


class FakeLLMDecider(ScriptedDecider):
    """Scripted actions plus a canned complete() channel. Each
    completion charges the usage ledger like a real endpoint, so
    the run resolves an LLMValidator over this decider."""

    def __init__(self, steps, decompose_reply, check_reply):
        super().__init__(steps)
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "retries": 0}
        self._decompose_reply = decompose_reply
        self._check_reply = check_reply
        self.complete_systems = []

    def complete(self, system_prompt, user_prompt):
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += 120
        self.usage["completion_tokens"] += 30
        self.complete_systems.append(system_prompt)
        if "validation planner" in system_prompt:
            return self._decompose_reply
        return self._check_reply


DECOMPOSE_ONE = '{"items": [{"text": "the application is filed", "exact": null}]}'


def test_code_check_overrides_a_judge_that_claims_satisfied():
    # The judge claims BOTH items satisfied and even "cites" the
    # reference — but CONF-2026 is nowhere in the real evidence
    # (the name was never typed, so no filing happened).
    decider = FakeLLMDecider(
        NAIVE_STEPS + [{"kind": "done", "summary": "x"}] * 2,
        DECOMPOSE_ONE,
        '{"items": [{"index": 0, "satisfied": true, "evidence": "filed"},'
        ' {"index": 1, "satisfied": true, "evidence": "Reference: CONF-2026"}]}',
    )
    trail = Trail()
    report = Runner(
        decider=decider, driver=_driver(), approver=lambda a: True,
        trail=trail, validate=True,
    ).run(GOAL)
    assert report.stop_reason == "unverified"
    verdict = _events(trail, "validation")[0]
    exact_item = next(i for i in verdict["items"] if i["exact"] == "CONF-2026")
    assert exact_item["satisfied"] is False
    assert exact_item["judge_satisfied"] is True  # the overruled claim is recorded
    assert "code check overrides" in exact_item["note"]


def test_validator_tokens_count_toward_run_totals():
    decider = FakeLLMDecider(
        CORRECT_STEPS,
        DECOMPOSE_ONE,
        '{"items": [{"index": 0, "satisfied": true, "evidence": "filed"},'
        ' {"index": 1, "satisfied": true, "evidence": "Reference: CONF-2026"}]}',
    )
    report = Runner(
        driver=_driver(), decider=decider, approver=lambda a: True,
        trail=Trail(), validate=True,
    ).run(GOAL)
    assert report.stop_reason == "done", report.summary
    # The scripted decide() spends nothing: every counted token is
    # validation spend (one decomposition + one check call).
    assert report.llm_calls == 2
    assert report.llm_prompt_tokens == 240
    assert report.llm_completion_tokens == 60
    # Both validator prompts opened with the untrusted-data boundary.
    assert all(s.startswith(UNTRUSTED_DATA_BOUNDARY) for s in decider.complete_systems)


def test_validation_spend_is_inside_the_token_budget_cap():
    decider = FakeLLMDecider(
        NAIVE_STEPS,
        DECOMPOSE_ONE,
        '{"items": [{"index": 0, "satisfied": false, "evidence": ""},'
        ' {"index": 1, "satisfied": false, "evidence": ""}]}',
    )
    report = Runner(
        driver=_driver(), decider=decider, approver=lambda a: True,
        trail=Trail(), validate=True, max_llm_tokens=200,
    ).run(GOAL)
    # One decomposition + one check = 300 tokens > the 200 cap: the
    # rejection loops back, and the between-steps budget check stops
    # the run — validation never silently extends the cap.
    assert report.stop_reason == "budget", report.summary


# --------------------------------------------------------------------------
# Validator units
# --------------------------------------------------------------------------


def test_decompose_goal_extracts_exact_values():
    items = decompose_goal('Pay $42.50 for order "rush-7" and note reference CONF-2026')
    exacts = [i.exact for i in items]
    assert "42.50" in exacts
    assert "rush-7" in exacts
    assert "CONF-2026" in exacts
    assert "2026" not in exacts  # covered by the code, not re-extracted


def test_decompose_goal_without_values_falls_to_floor_item():
    items = decompose_goal("tidy up the page")
    assert len(items) == 1 and items[0].exact is None and items[0].needle is None


def test_deterministic_check_cites_evidence_and_objects():
    validator = DeterministicValidator()
    items = validator.build_checklist(GOAL)
    missing = validator.check(GOAL, items, "a page with a banner", ["submitted form"])
    assert not missing.ok and "CONF-2026" in missing.objections[0]
    found = validator.check(
        GOAL, items, "Application filed\nReference: CONF-2026", ["submitted form"]
    )
    assert found.ok
    assert "CONF-2026" in found.items[0].evidence


def test_scripted_validator_requires_items():
    with pytest.raises(ValueError):
        ScriptedValidator([])


class _Client:
    """A bare complete() client for LLMValidator units."""

    def __init__(self, replies):
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "retries": 0}
        self._replies = list(replies)

    def complete(self, system_prompt, user_prompt):
        self.usage["calls"] += 1
        self.usage["prompt_tokens"] += 10
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def test_llm_validator_merges_code_exacts_the_model_missed():
    client = _Client([DECOMPOSE_ONE])
    validator = LLMValidator(client)
    items = validator.build_checklist(GOAL)
    exacts = [i.exact for i in items if i.exact]
    assert exacts == ["CONF-2026"]  # appended in code, though the model omitted it
    assert validator.checklist_degraded is False
    assert validator.last_usage["calls"] == 1


def test_llm_validator_degrades_to_code_decomposition():
    client = _Client([HandsError("endpoint down")])
    validator = LLMValidator(client)
    items = validator.build_checklist(GOAL)
    assert validator.checklist_degraded is True
    assert [i.exact for i in items if i.exact] == ["CONF-2026"]


def test_llm_validator_judge_error_still_code_checks():
    client = _Client([HandsError("judge down")])
    validator = LLMValidator(client)
    items = [
        ChecklistItem(text="reference shown", exact="CONF-2026"),
        ChecklistItem(text="a human would say it looks filed"),
    ]
    verdict = validator.check(GOAL, items, "Reference: CONF-2026", [])
    by_text = {iv.text: iv for iv in verdict.items}
    # The exact item is decided in code even with the judge down…
    assert by_text["reference shown"].satisfied is True
    # …and the judged item is unsatisfied, never silently passed.
    assert by_text["a human would say it looks filed"].satisfied is False
    assert "validator error" in by_text["a human would say it looks filed"].note
    assert verdict.ok is False


def test_fallback_decider_complete_fails_over():
    class _NoComplete:
        pass

    class _Completer:
        def __init__(self):
            self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0, "retries": 0}

        def complete(self, system_prompt, user_prompt):
            return "ok"

    fb = FallbackDecider(_NoComplete(), _Completer())
    assert fb.complete("s", "u") == "ok"


def test_validate_enabled_env(monkeypatch):
    monkeypatch.delenv("GHOST_HANDS_VALIDATE", raising=False)
    assert validate_enabled(False) is False
    assert validate_enabled(True) is True
    monkeypatch.setenv("GHOST_HANDS_VALIDATE", "1")
    assert validate_enabled(False) is True
    monkeypatch.setenv("GHOST_HANDS_VALIDATE", "0")
    assert validate_enabled(False) is False
