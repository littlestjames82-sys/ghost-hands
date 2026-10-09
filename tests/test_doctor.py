"""doctor (v0.9): one-command diagnostics for the whole stack.

Covered: the fake bridge up (PASS rows incl. the MrGhosty version),
down (FAIL + the verbatim forward fix), a bad token (FAIL + the
verbatim pairing fix), accessibility off and a protocol mismatch
(FAILs with their fixes), the real GhostBus booted locally (PASS) and
unconfigured (WARN, exit 0), Chromium present in this sandbox (PASS),
the model env absent (WARN) and present (PASS — with the key VALUE
asserted absent from every output), the --json schema + exit codes,
and the no-hang bound against an endpoint that accepts and never
replies. Secrets (model key, pairing token) are asserted absent from
the human output and the JSON alike.
"""

import json
import socket
import threading
import time

import pytest

from ghost_hands import cli
from ghost_hands.busagent import boot_ghostbus_server, default_ghostbus_dir
from ghost_hands.doctor import (
    BRIDGE_A11Y_FIX,
    BRIDGE_TOKEN_FIX,
    BRIDGE_UNREACHABLE_FIX,
    FAIL,
    PASS,
    WARN,
    doctor_exit_code,
    doctor_json,
    doctor_payload,
    format_doctor,
    run_doctor,
)
from ghost_hands.drivers import find_chrome

from fake_android_bridge import TOKEN, FakeAndroidState, make_bridge

SECRET = "MODELKEY" + "-DO-NOT-LEAK-" + "0123456789"


@pytest.fixture
def bridge():
    server, url, state = make_bridge()
    yield url, state
    server.shutdown()


@pytest.fixture
def bus():
    import shutil as _shutil

    ghostbus_dir = default_ghostbus_dir()
    if _shutil.which("node") is None or not (
        ghostbus_dir / "src" / "http-server.mjs"
    ).exists():
        pytest.skip("node or GhostBus source unavailable — real-bus test cannot run")
    proc, base = boot_ghostbus_server(ghostbus_dir)
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def by_name(checks):
    return {check.name: check for check in checks}


def closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def bridge_env(url, token=TOKEN):
    return {"GHOST_HANDS_ANDROID_BRIDGE": url, "GHOST_HANDS_ANDROID_TOKEN": token}


# -- the phone bridge rows ----------------------------------------------------


def test_doctor_fake_bridge_up(bridge):
    url, _state = bridge
    checks = run_doctor(env=bridge_env(url))
    row = by_name(checks)["phone bridge"]
    assert row.status == PASS
    assert "1.7.0" in row.detail  # the MrGhosty version from /v1/status
    assert "token accepted" in row.detail
    assert "accessibility service connected" in row.detail
    assert "protocol 1" in row.detail
    # Nothing about a working bridge may fail doctor.
    assert doctor_exit_code(checks) == 0


def test_doctor_bridge_down_fix_verbatim():
    env = bridge_env(f"http://127.0.0.1:{closed_port()}")
    checks = run_doctor(env=env, timeout=1.0)
    row = by_name(checks)["phone bridge"]
    assert row.status == FAIL
    assert row.fix == BRIDGE_UNREACHABLE_FIX
    assert row.fix == (
        "Is the phone on, MrGhosty accessibility on, and "
        "`adb forward tcp:8378 tcp:8378` running?"
    )
    assert doctor_exit_code(checks) == 1


def test_doctor_bad_token_fix_verbatim(bridge):
    url, _state = bridge
    checks = run_doctor(env=bridge_env(url, token="not-the-pairing-token"))
    row = by_name(checks)["phone bridge"]
    assert row.status == FAIL
    assert "401" in row.detail
    assert row.fix == BRIDGE_TOKEN_FIX
    assert row.fix == (
        "Pairing token mismatch — re-copy it from MrGhosty's Access "
        "checklist into GHOST_HANDS_ANDROID_TOKEN."
    )


def test_doctor_bridge_unconfigured_warns():
    checks = run_doctor(env={})
    row = by_name(checks)["phone bridge"]
    assert row.status == WARN
    assert "GHOST_HANDS_ANDROID_TOKEN" in row.fix
    assert doctor_exit_code(checks) == 0  # idle is not a failure


def test_doctor_bridge_accessibility_off():
    server, url, _state = make_bridge(FakeAndroidState(service_connected=False))
    try:
        checks = run_doctor(env=bridge_env(url))
    finally:
        server.shutdown()
    row = by_name(checks)["phone bridge"]
    assert row.status == FAIL
    assert "accessibility service is not connected" in row.detail
    assert row.fix == BRIDGE_A11Y_FIX


def test_doctor_bridge_protocol_mismatch():
    server, url, _state = make_bridge(FakeAndroidState(protocol=2))
    try:
        checks = run_doctor(env=bridge_env(url))
    finally:
        server.shutdown()
    row = by_name(checks)["phone bridge"]
    assert row.status == FAIL
    assert "protocol 2" in row.detail


def test_doctor_bridge_token_never_printed(bridge):
    url, _state = bridge
    checks = run_doctor(env=bridge_env(url))
    assert TOKEN not in format_doctor(checks)
    assert TOKEN not in doctor_json(checks)


def test_doctor_nonloopback_bridge_fails_fast():
    env = bridge_env("http://10.255.255.1:8378")
    started = time.time()
    checks = run_doctor(env=env, timeout=0.5)
    elapsed = time.time() - started
    row = by_name(checks)["phone bridge"]
    assert row.status == FAIL
    assert "loopback" in row.detail
    assert elapsed < 10


# -- the no-hang bound ----------------------------------------------------------


@pytest.fixture
def hanging_server():
    """A loopback endpoint that accepts connections and never replies —
    the worst case for a diagnostic that must not hang."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    held = []
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            held.append(conn)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.getsockname()[1]}"
    stop.set()
    for conn in held:
        try:
            conn.close()
        except OSError:
            pass
    srv.close()


def test_doctor_never_hangs_on_a_silent_endpoint(hanging_server):
    env = bridge_env(hanging_server)
    started = time.time()
    checks = run_doctor(env=env, timeout=0.5)
    elapsed = time.time() - started
    row = by_name(checks)["phone bridge"]
    assert row.status == FAIL
    assert row.fix == BRIDGE_UNREACHABLE_FIX
    # The 0.5s probe timeout bounds the whole run; everything else in
    # doctor is local. 15s is a generous ceiling, not the expectation.
    assert elapsed < 15, elapsed


# -- the bus rows ---------------------------------------------------------------


def test_doctor_bus_up(bus):
    checks = run_doctor(bus_url=bus, env={})
    row = by_name(checks)["ghostbus"]
    assert row.status == PASS
    assert "single-workspace" in row.detail
    assert bus in row.detail


def test_doctor_bus_unconfigured_warns_exit_0():
    checks = run_doctor(env={})
    row = by_name(checks)["ghostbus"]
    assert row.status == WARN
    assert row.detail == "not configured — bus features idle"
    assert doctor_exit_code(checks) == 0


def test_doctor_bus_down_fails():
    checks = run_doctor(bus_url=f"http://127.0.0.1:{closed_port()}", env={}, timeout=1.0)
    row = by_name(checks)["ghostbus"]
    assert row.status == FAIL
    assert "start a GhostBus server" in row.fix


# -- runtime / chromium / packs / model ------------------------------------------


def test_doctor_runtime_and_packs_pass():
    rows = by_name(run_doctor(env={}))
    assert rows["runtime"].status == PASS
    assert "ghost-hands 0.9.0" in rows["runtime"].detail
    assert rows["policy packs"].status == PASS
    for name in ("readonly", "standard", "strict", "seatbelt"):
        assert name in rows["policy packs"].detail


@pytest.mark.skipif(find_chrome() is None, reason="no Chromium in this environment")
def test_doctor_chromium_present_in_sandbox():
    row = by_name(run_doctor(env={}))["chromium"]
    assert row.status == PASS
    assert find_chrome() in row.detail
    assert "Chrome" in row.detail or "Chromium" in row.detail  # --version line


def test_doctor_model_env_absent_warns():
    row = by_name(run_doctor(env={}))["model env"]
    assert row.status == WARN
    assert "no model key" in row.detail
    assert "stub/rules/eval-stub still work" in row.detail


def test_doctor_model_env_present_passes_and_hides_the_value():
    env = {
        "GHOST_HANDS_API_KEY": SECRET,
        "GHOST_HANDS_BASE_URL": "https://models.example.test/v1",
        "GHOST_HANDS_MODEL": "some-model",
    }
    checks = run_doctor(env=env)
    row = by_name(checks)["model env"]
    assert row.status == PASS
    assert "key present" in row.detail
    assert "some-model" in row.detail  # model names are not secrets
    assert SECRET not in format_doctor(checks)
    assert SECRET not in doctor_json(checks)


def test_doctor_adb_absent_warns_not_fails():
    row = by_name(run_doctor(env={}, which=lambda name: None))["adb"]
    assert row.status == WARN
    assert "adb not on PATH" in row.detail


# -- output shape: human, JSON, exit codes, the CLI -------------------------------


def test_doctor_json_schema_and_summary(bridge):
    url, _state = bridge
    checks = run_doctor(env=bridge_env(url))
    payload = doctor_payload(checks)
    assert payload["tool"] == "ghost-hands doctor"
    assert payload["version"] == "0.9.0"
    assert payload["ok"] is True
    names = [row["name"] for row in payload["checks"]]
    assert names == [
        "runtime",
        "chromium",
        "policy packs",
        "model env",
        "ghostbus",
        "phone bridge",
        "adb",
    ]
    for row in payload["checks"]:
        assert set(row) == {"name", "status", "detail", "fix"}
        assert row["status"] in (PASS, WARN, FAIL)
    counts = payload["summary"]
    assert counts["pass"] + counts["warn"] + counts["fail"] == len(checks)
    text = format_doctor(checks)
    assert text.splitlines()[-1].startswith("doctor: ")
    assert "✓ runtime" in text and "✓ phone bridge" in text


def _scrub_env(monkeypatch):
    for name in (
        "GHOST_HANDS_ANDROID_BRIDGE",
        "GHOST_HANDS_ANDROID_TOKEN",
        "GHOST_HANDS_BUS_URL",
        "GHOSTBUS_URL",
        "GHOSTBUS_WORKSPACE",
        "GHOSTBUS_KEY",
        "GHOST_HANDS_API_KEY",
        "OPENROUTER_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_cli_doctor_exit_0_when_nothing_configured(monkeypatch, capsys):
    _scrub_env(monkeypatch)
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "doctor:" in out and "0 fail" in out


def test_cli_doctor_json(monkeypatch, capsys, bridge):
    _scrub_env(monkeypatch)
    url, _state = bridge
    monkeypatch.setenv("GHOST_HANDS_ANDROID_BRIDGE", url)
    monkeypatch.setenv("GHOST_HANDS_ANDROID_TOKEN", TOKEN)
    rc = cli.main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    row = {c["name"]: c for c in payload["checks"]}["phone bridge"]
    assert row["status"] == PASS and "1.7.0" in row["detail"]


def test_cli_doctor_exit_1_when_bridge_down(monkeypatch, capsys):
    _scrub_env(monkeypatch)
    rc = cli.main(
        [
            "doctor",
            "--android-bridge",
            f"http://127.0.0.1:{closed_port()}",
            "--android-token",
            "some-token",
            "--timeout",
            "1",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "✗ phone bridge" in out
    assert BRIDGE_UNREACHABLE_FIX in out
