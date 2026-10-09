"""phone-proof (v0.9): the guided on-device proof, end to end against
the fake MrGhosty bridge (tests/fake_android_bridge.py).

Covered: the full --yes flow with a simulated finger approving
(exit 0 + the "proven" summary), the denial path (exit 3), the
approval timeout ending gracefully with instructions (exit 3, no
traceback), the unconfigured stop (exit 2 + the exact env setup),
the adb step (a fake adb script proves `adb forward` runs when a
device is attached; adb absent is tolerated everywhere else), the
TTY pause prompts via an injectable input function, a dead bridge
(exit 1), and the CLI wiring.
"""

import os
import threading
import time

import pytest

from ghost_hands import cli
from ghost_hands.phone_proof import (
    EXIT_ERROR,
    EXIT_NEEDS_SETUP,
    EXIT_NOT_APPROVED,
    EXIT_OK,
    run_phone_proof,
)

from fake_android_bridge import TOKEN, make_bridge


@pytest.fixture
def bridge():
    server, url, state = make_bridge()
    yield url, state
    server.shutdown()


def decide_soon(state, approved=True, delay=0.2):
    """Simulate Ryan's finger: decide the next NEW approval shortly
    after it appears on the 'phone' (fixture hook, not an API)."""
    seen = set(state.approvals)

    def run():
        deadline = time.time() + 15
        while time.time() < deadline:
            fresh = [rid for rid in state.approvals if rid not in seen]
            if fresh:
                time.sleep(delay)
                state.decide_approval(fresh[-1], approved)
                return
            time.sleep(0.02)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def no_adb(name):
    return None


def run_proof(url, lines, **kwargs):
    kwargs.setdefault("assume_yes", True)
    kwargs.setdefault("out", lines.append)
    kwargs.setdefault("which", no_adb)
    kwargs.setdefault("timeout", 30.0)
    kwargs.setdefault("poll_interval", 0.05)
    return run_phone_proof(bridge_url=url, token=TOKEN, **kwargs)


# -- the five steps -------------------------------------------------------------


def test_proof_full_flow_approved(bridge):
    url, state = bridge
    decide_soon(state, approved=True)
    lines = []
    rc = run_proof(url, lines)
    text = "\n".join(lines)
    assert rc == EXIT_OK == 0
    assert "step 1/5 ✓ bridge configured" in text
    assert "step 2/5 adb not on PATH" in text  # tolerated, not fatal
    assert "step 3/5 ✓ bridge answers: MrGhosty 1.7.0" in text
    assert "LOOK AT YOUR PHONE NOW" in text
    assert "step 4/5 ✓ approved on the phone" in text
    assert "PHONE PROOF COMPLETE" in text
    assert "proven on this phone" in text
    # The exact next commands close the proof.
    assert "ghost-hands run --driver android --approver phone" in text
    assert "ghost-hands bus-agent --driver android --approval-channel phone" in text
    # The approval that travelled was the harmless test approval.
    assert state.approval_payloads and state.approval_payloads[0]["test"] is True
    # The token never appears in anything the proof printed.
    assert TOKEN not in text


def test_proof_denied_exits_3(bridge):
    url, state = bridge
    decide_soon(state, approved=False)
    lines = []
    rc = run_proof(url, lines)
    text = "\n".join(lines)
    assert rc == EXIT_NOT_APPROVED == 3
    assert "denied on the phone" in text
    assert "PHONE PROOF COMPLETE" not in text


def test_proof_timeout_is_graceful_exit_3(bridge):
    url, _state = bridge
    lines = []
    started = time.time()
    rc = run_proof(url, lines, timeout=1.5)
    elapsed = time.time() - started
    text = "\n".join(lines)
    assert rc == EXIT_NOT_APPROVED == 3
    assert "no decision arrived (timeout)" in text
    assert "re-run phone-proof" in text  # instructions, not a traceback
    assert "Traceback" not in text
    assert elapsed < 20


def test_proof_unconfigured_exits_2_with_setup(monkeypatch):
    monkeypatch.delenv("GHOST_HANDS_ANDROID_TOKEN", raising=False)
    monkeypatch.delenv("GHOST_HANDS_ANDROID_BRIDGE", raising=False)
    lines = []
    rc = run_phone_proof(
        token=None, out=lines.append, which=no_adb, assume_yes=True
    )
    text = "\n".join(lines)
    assert rc == EXIT_NEEDS_SETUP == 2
    assert "do this, then re-run" in text
    assert "export GHOST_HANDS_ANDROID_TOKEN=<the pairing code>" in text
    assert "export GHOST_HANDS_ANDROID_BRIDGE=http://127.0.0.1:8378" in text
    assert "ghost-hands phone-proof" in text


def test_proof_bridge_down_exits_1(bridge):
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    dead = sock.getsockname()[1]
    sock.close()
    lines = []
    rc = run_phone_proof(
        bridge_url=f"http://127.0.0.1:{dead}",
        token=TOKEN,
        out=lines.append,
        which=no_adb,
        assume_yes=True,
        http_timeout=2.0,
    )
    text = "\n".join(lines)
    assert rc == EXIT_ERROR == 1
    assert "step 3/5 ✗" in text
    assert "Traceback" not in text


# -- the adb step -----------------------------------------------------------------


def test_proof_runs_adb_forward_when_a_device_is_attached(bridge, tmp_path):
    url, state = bridge
    port = url.rsplit(":", 1)[1]
    log = tmp_path / "adb-calls.log"
    script = tmp_path / "adb"
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {log}\n'
        'if [ "$1" = "devices" ]; then\n'
        "  printf 'List of devices attached\\nemulator-5554\\tdevice\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    decide_soon(state, approved=True)
    lines = []
    rc = run_proof(url, lines, which=lambda name: str(script))
    text = "\n".join(lines)
    assert rc == EXIT_OK
    assert f"running: adb forward tcp:{port} tcp:{port}" in text
    calls = log.read_text(encoding="utf-8")
    assert "devices" in calls
    assert f"forward tcp:{port} tcp:{port}" in calls


# -- interactivity ------------------------------------------------------------------


def test_proof_pauses_only_with_an_input_fn(bridge):
    url, state = bridge
    decide_soon(state, approved=True)
    prompts = []
    lines = []
    rc = run_proof(
        url,
        lines,
        assume_yes=False,
        input_fn=lambda prompt: prompts.append(prompt) or "",
    )
    assert rc == EXIT_OK
    assert len(prompts) >= 3  # after steps 1, 2, 3 (and 4)
    # And with --yes the same flow never prompts at all.
    url2_state = state
    decide_soon(url2_state, approved=True)
    prompts2 = []
    rc2 = run_proof(
        url,
        [],
        assume_yes=True,
        input_fn=lambda prompt: prompts2.append(prompt) or "",
    )
    assert rc2 == EXIT_OK
    assert prompts2 == []


# -- the CLI -------------------------------------------------------------------------


def test_cli_phone_proof(monkeypatch, capsys, bridge):
    url, state = bridge
    monkeypatch.setenv("GHOST_HANDS_ANDROID_BRIDGE", url)
    monkeypatch.setenv("GHOST_HANDS_ANDROID_TOKEN", TOKEN)
    decide_soon(state, approved=True)
    rc = cli.main(
        ["phone-proof", "--yes", "--timeout", "30", "--poll-interval", "0.05"]
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "PHONE PROOF COMPLETE" in out
    assert TOKEN not in out


def test_cli_phone_proof_unconfigured(monkeypatch, capsys):
    monkeypatch.delenv("GHOST_HANDS_ANDROID_TOKEN", raising=False)
    monkeypatch.delenv("GHOST_HANDS_ANDROID_BRIDGE", raising=False)
    rc = cli.main(["phone-proof", "--yes"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "export GHOST_HANDS_ANDROID_TOKEN" in out
    assert os.environ.get("GHOST_HANDS_ANDROID_TOKEN") is None
