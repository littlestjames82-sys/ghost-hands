"""Approvals by phone, end to end (v0.6): the UNCHANGED Runner +
Governor, with PhoneApprover as the approver and the fake MrGhosty
bridge as 'the phone'. The simulated decision arrives from another
thread, after a delay — the way Ryan's tap would.

- Android body: the notes scenario's consequential delete-all is
  approved on the phone mid-run and executes; denied, it never runs.
- Web body (FakeDriver): a shop checkout's submit is approved on the
  phone and the order lands — the phone approves web runs too.
- CLI: `phone-approval-test` in a subprocess (env-configured) exits 0
  on a simulated approval and 1 on a denial; `run --approver phone`
  drives a whole scripted checkout; conflicting approver flags exit 2.
"""

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from ghost_hands import actions as A
from ghost_hands.android_driver import AndroidDriver
from ghost_hands.cli import main as cli_main
from ghost_hands.deciders import Decider, ScriptedDecider
from ghost_hands.drivers import FakeDriver
from ghost_hands.errors import HandsError
from ghost_hands.governor import Governor
from ghost_hands.phone_approver import ObservingDecider, PhoneApprover
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail

from fake_android_bridge import TOKEN, make_bridge
from pages_fixtures import SHOP_PAGES


@pytest.fixture
def bridge():
    server, url, state = make_bridge()
    yield url, state
    server.shutdown()


def phone_finger(state, approved, delay=0.3):
    """Ryan's finger, simulated: watch for a pending approval and
    decide it from another thread after a human-ish delay."""

    def run():
        deadline = time.time() + 15
        while time.time() < deadline:
            ids = state.pending_approval_ids()
            if ids:
                time.sleep(delay)
                state.decide_approval(ids[0], approved)
                return
            time.sleep(0.02)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def make_phone(url, trail=None, timeout=30):
    return PhoneApprover(
        bridge_url=url, token=TOKEN, timeout=timeout, poll_interval=0.05,
        trail=trail,
    )


# ---------------------------------------------------------------------------
# Android body: the notes scenario, decided on the phone
# ---------------------------------------------------------------------------

SCENARIO = [
    ("click", "Notes", None),
    ("click", "New note", None),
    ("type", "Note text", "hello ghost"),
    ("click", "Save", None),
    ("click", "Delete all", None),
]


class IntentDecider(Decider):
    """Same intent-resolving pattern as test_android_e2e: targets are
    found by name in the live map at decision time."""

    def __init__(self, scenario):
        self._steps = list(scenario)
        self._i = 0

    def decide(self, goal, element_map, history):
        if self._i >= len(self._steps):
            return A.done("scenario complete")
        verb, name, text = self._steps[self._i]
        self._i += 1
        el = element_map.find_by_text(name)
        if el is None:
            raise HandsError(f"scenario target {name!r} not on this map")
        if verb == "click":
            return A.click(el.number)
        if verb == "type":
            return A.type(el.number, text or "")
        raise HandsError(f"unknown scenario verb {verb!r}")


def run_notes_scenario(url, approver):
    driver = AndroidDriver(bridge_url=url, token=TOKEN)
    trail = approver.trail
    runner = Runner(
        driver,
        ObservingDecider(IntentDecider(SCENARIO), approver),
        governor=Governor(),
        approver=approver,
        trail=trail,
    )
    return runner.run("phone-approval notes scenario"), trail


def test_android_delete_approved_on_phone_executes(bridge):
    url, state = bridge
    trail = Trail()
    approver = make_phone(url, trail=trail)
    finger = phone_finger(state, approved=True)
    report, trail = run_notes_scenario(url, approver)
    finger.join(timeout=5)
    assert report.stop_reason == "done", report.summary
    assert state.notes == []  # the delete really executed
    events = [ev for ev in trail.events if ev["type"] == "approval"]
    assert len(events) == 2
    assert events[0]["channel"] == "phone" and events[0]["phase"] == "request"
    assert "Delete all notes" in events[0]["summary"]
    assert events[1]["approved"] is True and events[1]["decided_by"] == "phone"
    # The wire payload for the ask carried the descriptor, not a selector.
    payload = state.approval_payloads[-1]
    assert payload["target"]["name"] == "Delete all notes"
    assert payload["kind"] == "click"
    assert payload["classification"] == "consequential"


def test_android_delete_denied_on_phone_never_executes(bridge):
    url, state = bridge
    trail = Trail()
    approver = make_phone(url, trail=trail)
    finger = phone_finger(state, approved=False)
    report, trail = run_notes_scenario(url, approver)
    finger.join(timeout=5)
    assert report.stop_reason == "denied", report.summary
    assert state.notes == ["hello ghost"]  # the note survives
    executes = [ev for ev in trail.events if ev["type"] == "execute"]
    results = [ev for ev in trail.events if ev["type"] == "result"]
    # Four actions executed (open Notes, new note, type, save); the
    # denied delete never reached the driver.
    assert len(executes) == 4 and len(results) == 4
    decision = [ev for ev in trail.events if ev["type"] == "approval"][-1]
    assert decision["approved"] is False and decision["decided_by"] == "phone"


# ---------------------------------------------------------------------------
# Web body: the phone approves a browser-side run too
# ---------------------------------------------------------------------------


def test_fake_web_checkout_approved_on_phone(bridge):
    url, state = bridge
    trail = Trail()
    approver = make_phone(url, trail=trail)
    driver = FakeDriver(SHOP_PAGES, start_url="https://shop.local/")
    finger = phone_finger(state, approved=True)
    runner = Runner(
        driver,
        ObservingDecider(
            ScriptedDecider(
                [
                    {"kind": "click", "target": 1},  # View cart (readonly)
                    {"kind": "click", "target": 1},  # Place order (consequential)
                ]
            ),
            approver,
        ),
        approver=approver,
        trail=trail,
    )
    report = runner.run("check out")
    finger.join(timeout=5)
    assert report.stop_reason == "done", report.summary
    assert driver.current_url() == "https://shop.local/thanks"
    assert len(driver.submissions) == 1
    payload = state.approval_payloads[-1]
    assert payload["classification"] == "consequential"
    assert "Place order" in payload["summary"]


# ---------------------------------------------------------------------------
# CLI: phone-approval-test + run --approver phone
# ---------------------------------------------------------------------------


def _cli_env(url):
    env = dict(os.environ)
    env["GHOST_HANDS_ANDROID_BRIDGE"] = url
    env["GHOST_HANDS_ANDROID_TOKEN"] = TOKEN
    return env


def test_cli_phone_approval_test_approved(bridge):
    url, state = bridge
    finger = phone_finger(state, approved=True)
    proc = subprocess.run(
        [
            sys.executable, "-m", "ghost_hands.cli", "phone-approval-test",
            "--timeout", "30", "--poll-interval", "0.1",
        ],
        capture_output=True, text=True, env=_cli_env(url), timeout=60,
    )
    finger.join(timeout=5)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "APPROVED" in proc.stdout
    payload = state.approval_payloads[-1]
    assert payload["kind"] == "test" and payload["test"] is True
    assert "runs nothing" in payload["summary"]


def test_cli_phone_approval_test_denied(bridge):
    url, state = bridge
    finger = phone_finger(state, approved=False)
    proc = subprocess.run(
        [
            sys.executable, "-m", "ghost_hands.cli", "phone-approval-test",
            "--timeout", "30", "--poll-interval", "0.1",
        ],
        capture_output=True, text=True, env=_cli_env(url), timeout=60,
    )
    finger.join(timeout=5)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "DENIED" in proc.stdout


def test_cli_run_with_phone_approver(bridge, tmp_path):
    url, state = bridge
    script = tmp_path / "steps.json"
    script.write_text(
        json.dumps(
            {
                "pages": SHOP_PAGES,
                "start": "https://shop.local/",
                "steps": [
                    {"kind": "click", "target": 1},
                    {"kind": "click", "target": 1},
                ],
            }
        ),
        encoding="utf-8",
    )
    trail_path = tmp_path / "trail.jsonl"
    finger = phone_finger(state, approved=True)
    rc = cli_main(
        [
            "run",
            "--script", str(script),
            "--approver", "phone",
            "--android-bridge", url,
            "--android-token", TOKEN,
            "--trail", str(trail_path),
        ]
    )
    finger.join(timeout=5)
    assert rc == 0
    events = [
        json.loads(line)
        for line in trail_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    approvals = [ev for ev in events if ev["type"] == "approval"]
    assert approvals and approvals[0]["channel"] == "phone"
    assert approvals[-1]["approved"] is True


def test_cli_conflicting_approver_flags_exit_2(tmp_path):
    script = tmp_path / "steps.json"
    script.write_text(json.dumps({"steps": []}), encoding="utf-8")
    rc = cli_main(
        [
            "run",
            "--script", str(script),
            "--approve-all",
            "--approver", "phone",
        ]
    )
    assert rc == 2
