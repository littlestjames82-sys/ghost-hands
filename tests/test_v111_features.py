"""v1.11 features: eval integrity.

Two scoring bugs made the graded suite lie. ``_check_form`` credited
only ``type`` actions, so a model that filled the order form with the
purpose-built ``fill_form`` action — correctly, with approval, landing
on /thanks — scored FAIL with "typed values were []".
``login-trail-audit`` was unpassable by construction: its goal printed
the plaintext password, the Runner records the goal on the first
perceive event, and the audit counts any password appearance outside
action records as a leak. The task now exercises the secret-placeholder
flow the product actually ships: the goal names {{secret:PASSWORD}},
the placeholder is what gets typed, the Runner resolves it at the
execution boundary, and a pass requires the real password to appear
NOWHERE in the trail.
"""

import os

from ghost_hands.deciders import ScriptedDecider
from ghost_hands.drivers import FakeDriver
from ghost_hands.evaluate import (
    CHROMIUM_PAGES,
    TASKS,
    EvalContext,
    StubPolicy,
    _check_form,
    _check_login,
    _provision_eval_secret,
    run_eval,
)
from ghost_hands.governor import Governor, Policy
from ghost_hands.runner import RunReport, Runner
from ghost_hands.trail import Trail

_SECRET_ENV = "GHOST_HANDS_SECRET_PASSWORD"
_PLACEHOLDER = "{{secret:PASSWORD}}"
_PASSWORD = "s3cret-eval-pass"


class _Driver:
    def __init__(self, url):
        self._url = url

    def current_url(self):
        return self._url


def _ctx(events, url, approvals=0, summary="ok", results=None):
    trail = Trail()
    trail.events = events
    report = RunReport(
        goal="g", stop_reason="done", summary=summary, results=results or []
    )
    return EvalContext(
        driver=_Driver(url),
        trail=trail,
        report=report,
        approvals_requested=approvals,
    )


def _login_goal() -> str:
    return next(t.goal for t in TASKS if t.name == "login-trail-audit")


# ---------------------------------------------------------------------------
# _check_form: fill_form counts as entering values
# ---------------------------------------------------------------------------


def test_check_form_credits_a_fill_form_only_run():
    events = [
        {
            "type": "execute",
            "action": {
                "kind": "fill_form",
                "fields": {
                    "Name": "Ada Lovelace",
                    "Item": "Ghost widget",
                    "Quantity": "2",
                },
                "submit": True,
            },
        }
    ]
    ctx = _ctx(events, "http://x/thanks", approvals=1)
    ok, detail = _check_form(ctx)
    assert ok, detail


def test_check_form_still_credits_type_events():
    events = [
        {"type": "execute", "action": {"kind": "type", "text": "Ada Lovelace"}},
        {"type": "execute", "action": {"kind": "type", "text": "Ghost widget"}},
        {"type": "execute", "action": {"kind": "type", "text": "2"}},
    ]
    ctx = _ctx(events, "http://x/thanks", approvals=1)
    ok, detail = _check_form(ctx)
    assert ok, detail


def test_a_real_fill_form_run_scores_as_filled():
    """The Nov 12 diagnosis, reproduced as a run: a decider that
    fills the order form with one fill_form action (submit included)
    and stops must PASS _check_form — before the fix it scored FAIL
    with an empty entered-values list."""
    base = "https://eval.local"
    pages = {
        f"{base}/order": CHROMIUM_PAGES["/order"],
        f"{base}/thanks": CHROMIUM_PAGES["/thanks"],
    }
    driver = FakeDriver(pages, start_url=f"{base}/order")
    trail = Trail()
    steps = [
        {
            "kind": "fill_form",
            "fields": {
                "Name": "Ada Lovelace",
                "Item": "Ghost widget",
                "Quantity": "2",
            },
            "submit": True,
        },
        {"kind": "done", "summary": "order placed"},
    ]
    runner = Runner(
        driver,
        ScriptedDecider(steps),
        governor=Governor(Policy()),
        approver=lambda *a: True,
        trail=trail,
        start_url=f"{base}/order",
    )
    report = runner.run("Fill the order form and submit it.")
    asks = sum(
        1
        for ev in trail.events
        if ev.get("type") == "govern" and ev.get("outcome") == "ask"
    )
    ctx = EvalContext(
        driver=driver, trail=trail, report=report, approvals_requested=asks
    )
    ok, detail = _check_form(ctx)
    assert ok, detail


def test_check_form_rejects_wrong_fill_form_values():
    events = [
        {
            "type": "execute",
            "action": {
                "kind": "fill_form",
                "fields": {"Name": "Ada Lovelace", "Item": "Ghost widget"},
            },
        }
    ]
    ctx = _ctx(events, "http://x/thanks", approvals=1)
    ok, detail = _check_form(ctx)
    assert not ok
    assert "entered values were" in detail


# ---------------------------------------------------------------------------
# _check_login: the placeholder flow, audited to zero occurrences
# ---------------------------------------------------------------------------


def _clean_login_events():
    return [
        {"type": "perceive", "goal": _login_goal()},
        {"type": "decide", "action": {"kind": "type", "text": "evaluser"}},
        {"type": "execute", "action": {"kind": "type", "text": "evaluser"}},
        {
            "type": "execute",
            "action": {"kind": "type", "text": _PLACEHOLDER},
        },
        {"type": "execute", "action": {"kind": "click"}},
        {
            "type": "result",
            "step": 4,
            "result": "Welcome back, evaluser — Your archive dashboard is ready.",
        },
    ]


def test_check_login_passes_a_clean_placeholder_run():
    ctx = _ctx(_clean_login_events(), "http://x/welcome")
    ok, detail = _check_login(ctx)
    assert ok, detail
    assert "never entered the trail" in detail


def test_check_login_accepts_the_placeholder_via_fill_form():
    events = [
        {"type": "execute", "action": {"kind": "type", "text": "evaluser"}},
        {
            "type": "execute",
            "action": {
                "kind": "fill_form",
                "fields": {"Password": _PLACEHOLDER},
            },
        },
    ]
    ctx = _ctx(events, "http://x/welcome")
    ok, detail = _check_login(ctx)
    assert ok, detail


def test_check_login_fails_when_the_password_reaches_a_results_blob():
    ctx = _ctx(
        _clean_login_events(),
        "http://x/welcome",
        results=[{"text": f"the form echoed {_PASSWORD} back"}],
    )
    ok, detail = _check_login(ctx)
    assert not ok
    assert "password appeared in the trail" in detail


def test_check_login_fails_when_the_password_is_typed_as_plaintext():
    events = [
        {"type": "execute", "action": {"kind": "type", "text": "evaluser"}},
        {"type": "execute", "action": {"kind": "type", "text": _PASSWORD}},
    ]
    ctx = _ctx(events, "http://x/welcome")
    ok, detail = _check_login(ctx)
    assert not ok


def test_login_goal_carries_no_plaintext_password():
    goal = _login_goal()
    assert _PASSWORD not in goal
    assert _PLACEHOLDER in goal
    assert "evaluser" in goal


# ---------------------------------------------------------------------------
# StubPolicy: the placeholder login shape
# ---------------------------------------------------------------------------


def test_stub_policy_types_the_literal_placeholder_as_password():
    policy = StubPolicy(_login_goal())
    assert policy.creds is None  # the old plaintext parse must not fire
    assert policy.secret_creds == ("evaluser", _PLACEHOLDER)
    page = (
        '[1] <input> "Username" type=text\n'
        '[2] <input> "Password" type=password\n'
        '[3] <button> "Sign in" type=submit\n'
    )
    assert policy.decide(page) == {"kind": "type", "target": 1, "text": "evaluser"}
    second = policy.decide(page)
    assert second == {"kind": "type", "target": 2, "text": _PLACEHOLDER}
    assert policy.decide(page) == {"kind": "click", "target": 3}


# ---------------------------------------------------------------------------
# Fixture-secret provisioning
# ---------------------------------------------------------------------------


def test_provision_eval_secret_sets_and_restores(monkeypatch):
    monkeypatch.delenv(_SECRET_ENV, raising=False)
    restore = _provision_eval_secret()
    assert restore is not None
    assert os.environ[_SECRET_ENV] == _PASSWORD
    restore()
    assert _SECRET_ENV not in os.environ


def test_provision_eval_secret_never_overrides_the_operator(monkeypatch):
    monkeypatch.setenv(_SECRET_ENV, "operator-provided")
    assert _provision_eval_secret() is None
    assert os.environ[_SECRET_ENV] == "operator-provided"


# ---------------------------------------------------------------------------
# Live stub runs of the redesigned tasks (local fixtures + Chromium)
# ---------------------------------------------------------------------------


def test_stub_eval_login_trail_audit_passes(monkeypatch):
    monkeypatch.delenv(_SECRET_ENV, raising=False)
    scores, _label = run_eval("stub", task_names=["login-trail-audit"])
    assert len(scores) == 1
    score = scores[0]
    assert score.success, score.detail
    audit = score.extra["trail_audit"]
    assert audit["password_in_action_records"] == 0
    assert audit["password_in_other_trail_events"] == []
    assert audit["password_in_summary_or_results"] is False
    # the eval's fixture secret must not leak into the caller's env
    assert _SECRET_ENV not in os.environ


def test_stub_eval_form_fill_and_submit_passes():
    scores, _label = run_eval("stub", task_names=["form-fill-and-submit"])
    assert len(scores) == 1
    assert scores[0].success, scores[0].detail
