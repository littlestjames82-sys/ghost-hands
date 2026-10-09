"""doctor — one-command diagnostics for the whole Ghost Hands stack
(v0.9).

``ghost-hands doctor`` answers "is everything wired up?" in one pass:
the Python runtime, a Chromium binary, the policy packs, the model
environment, a GhostBus server (when configured), the MrGhosty phone
bridge (when configured), and adb. Every check is read-only — doctor
never mutates anything, and it never prints a secret: the model key
and the pairing token are reported as *present*, never as values.

Each check returns PASS / WARN / FAIL plus, when it is not a PASS, a
one-line plain-language fix. FAIL means "this part of the stack will
not work until you act"; WARN means "this part is idle or optional
right now". The exit code is 1 iff any check FAILs. ``--json``
emits the same checks as structured data for tooling.

Every network probe carries a short timeout (3s by default), so
doctor finishes fast and never hangs on a dead endpoint.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from .android_driver import BRIDGE_PROTOCOL, DEFAULT_BRIDGE_URL, _is_loopback_host
from .drivers import find_chrome
from .policies import load_pack

PASS = "pass"
WARN = "warn"
FAIL = "fail"

_SYMBOLS = {PASS: "✓", WARN: "!", FAIL: "✗"}

#: The packs doctor loads to prove the pack registry is intact.
PACKS_CHECKED = ("readonly", "standard", "strict", "seatbelt")

#: Verbatim fixes — tests and the README quote these.
CHROME_FIX = "Install Chrome/Chromium or set GHOST_HANDS_CHROME to the binary."
BRIDGE_UNREACHABLE_FIX = (
    "Is the phone on, MrGhosty accessibility on, and "
    "`adb forward tcp:8378 tcp:8378` running?"
)
BRIDGE_TOKEN_FIX = (
    "Pairing token mismatch — re-copy it from MrGhosty's Access "
    "checklist into GHOST_HANDS_ANDROID_TOKEN."
)
BRIDGE_A11Y_FIX = (
    "Enable MrGhosty under Settings → Accessibility on the phone, "
    "then re-run doctor."
)


@dataclass
class Check:
    """One diagnostic row: a name, a verdict, what was found, and —
    when the verdict is not PASS — the one-line fix."""

    name: str
    status: str  # PASS | WARN | FAIL
    detail: str
    fix: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "fix": self.fix,
        }


def _version() -> str:
    from . import __version__

    return __version__


# ---------------------------------------------------------------------------
# individual checks
# ---------------------------------------------------------------------------


def _check_runtime() -> Check:
    detail = f"Python {platform.python_version()} · ghost-hands {_version()}"
    if sys.version_info >= (3, 10):
        return Check("runtime", PASS, detail)
    return Check(
        "runtime",
        FAIL,
        detail + " — too old",
        "Ghost Hands needs Python 3.10+ — run it with a newer Python.",
    )


def _check_chromium() -> Check:
    # find_chrome is the ChromiumDriver's own resolver (drivers.py) —
    # doctor and the driver can never disagree about the binary.
    path = find_chrome()
    if not path:
        return Check("chromium", FAIL, "no Chromium/Chrome binary found", CHROME_FIX)
    try:
        proc = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=5
        )
        version_line = (proc.stdout or "").strip().splitlines()
        version = version_line[0] if version_line else ""
    except Exception as exc:  # binary exists but will not even report
        return Check(
            "chromium",
            WARN,
            f"{path} (version probe failed: {exc})",
            "the binary was found but `--version` failed — check it "
            "runs, or set GHOST_HANDS_CHROME to a working binary.",
        )
    return Check("chromium", PASS, f"{path} — {version}" if version else path)


def _check_packs() -> Check:
    broken = []
    for name in PACKS_CHECKED:
        try:
            load_pack(name)
        except Exception as exc:  # a broken pack must be named, loudly
            broken.append(f"{name} ({exc})")
    if broken:
        return Check(
            "policy packs",
            FAIL,
            "broken pack(s): " + "; ".join(broken),
            "reinstall ghost-hands — a built-in policy pack failed to load.",
        )
    return Check(
        "policy packs",
        PASS,
        "all 4 packs load: " + ", ".join(PACKS_CHECKED),
    )


def _check_model(env: dict) -> Check:
    present = [
        name
        for name in ("GHOST_HANDS_API_KEY", "OPENROUTER_API_KEY")
        if (env.get(name) or "").strip()
    ]
    extras = []
    if (env.get("GHOST_HANDS_BASE_URL") or "").strip():
        extras.append(f"base URL {env['GHOST_HANDS_BASE_URL']}")
    if (env.get("GHOST_HANDS_MODEL") or "").strip():
        extras.append(f"model {env['GHOST_HANDS_MODEL']}")
    if present:
        detail = (
            "key present ("
            + ", ".join(present)
            + " — value never shown)"
        )
        if extras:
            detail += "; " + "; ".join(extras)
        return Check("model env", PASS, detail)
    return Check(
        "model env",
        WARN,
        "no model key — stub/rules/eval-stub still work; live model "
        "runs need a key",
        "set GHOST_HANDS_API_KEY (with GHOST_HANDS_BASE_URL) or "
        "OPENROUTER_API_KEY to enable live model runs.",
    )


def _http_get_json(
    url: str, timeout: float, headers: Optional[dict] = None
) -> tuple[Optional[int], Optional[dict], str]:
    """One GET expecting JSON. Returns (http_status, payload, error):
    status is None on a transport failure (error says why); payload
    is None when the body was not a JSON object. Nothing here raises,
    and nothing here can carry a secret into its result — headers go
    out, never back."""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read() if exc.fp is not None else b""
        except Exception:
            raw = b""
    except (urllib.error.URLError, OSError) as exc:
        cause = getattr(exc, "reason", exc)
        return None, None, str(cause)
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else None
    except Exception:
        payload = None
    return status, payload if isinstance(payload, dict) else None, ""


def _check_bus(
    bus_url: Optional[str], env: dict, timeout: float
) -> Check:
    configured = (
        bus_url
        or (env.get("GHOST_HANDS_BUS_URL") or "").strip()
        or (env.get("GHOSTBUS_URL") or "").strip()
    )
    if not configured:
        return Check(
            "ghostbus",
            WARN,
            "not configured — bus features idle",
            "pass --bus <url> or set GHOSTBUS_URL to check a GhostBus server.",
        )
    base = configured.rstrip("/")
    workspace = (env.get("GHOSTBUS_WORKSPACE") or "").strip()
    if workspace and "/w/" not in urlparse(base).path:
        base = f"{base}/w/{workspace}"
    headers = {}
    key = (env.get("GHOSTBUS_KEY") or "").strip()
    if key:
        headers["x-bus-key"] = key  # /health is open; the key never returns
    status, payload, error = _http_get_json(base + "/health", timeout, headers)
    fail_fix = (
        "start a GhostBus server, or check the bus URL "
        "(--bus / GHOSTBUS_URL) — bus features stay idle until one answers."
    )
    if status is None:
        return Check(
            "ghostbus", FAIL, f"no answer from {base}/health ({error})", fail_fix
        )
    if status != 200:
        return Check(
            "ghostbus", FAIL, f"{base}/health answered HTTP {status}", fail_fix
        )
    server = str((payload or {}).get("server") or "")
    if payload is None or payload.get("ok") is not True or "ghostbus" not in server:
        return Check(
            "ghostbus",
            FAIL,
            f"{base}/health answered but did not identify as a healthy GhostBus",
            "check that the URL points at a GhostBus server, not another service.",
        )
    path = urlparse(base).path
    if "/w/" in path:
        shape = f"hosted workspace ({path.rstrip('/').rsplit('/', 1)[-1]})"
    elif server == "ghostbus-hosted":
        shape = "hosted relay"
    else:
        shape = "single-workspace relay"
    version = payload.get("version")
    detail = f"GhostBus {version} at {base} — {shape}" if version else (
        f"GhostBus at {base} — {shape}"
    )
    return Check("ghostbus", PASS, detail)


def fetch_bridge_status(
    bridge_url: str, token: Optional[str], timeout: float
) -> tuple[Optional[int], Optional[dict], str]:
    """GET /v1/status on the MrGhosty bridge, token header attached.
    Same return contract as _http_get_json. The token goes out as a
    header only — it never appears in the result."""
    headers = {}
    if token:
        headers["X-Ghost-Hands-Token"] = token
    return _http_get_json(bridge_url.rstrip("/") + "/v1/status", timeout, headers)


def _check_bridge(
    bridge_url: Optional[str],
    bridge_token: Optional[str],
    env: dict,
    timeout: float,
) -> Check:
    env_url = (env.get("GHOST_HANDS_ANDROID_BRIDGE") or "").strip()
    env_token = (env.get("GHOST_HANDS_ANDROID_TOKEN") or "").strip()
    configured = bool(bridge_url or env_url or bridge_token or env_token)
    if not configured:
        return Check(
            "phone bridge",
            WARN,
            "not configured — the Android body and phone approvals are idle",
            "pair a phone: set GHOST_HANDS_ANDROID_TOKEN to the pairing "
            "code in MrGhosty's Access checklist (bridge URL defaults "
            f"to {DEFAULT_BRIDGE_URL}).",
        )
    url = (bridge_url or env_url or DEFAULT_BRIDGE_URL).rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return Check(
            "phone bridge",
            FAIL,
            f"bridge URL {url!r} is not a valid http(s) URL",
            f"use the adb-forwarded loopback URL: {DEFAULT_BRIDGE_URL}.",
        )
    if not _is_loopback_host(parsed.hostname):
        return Check(
            "phone bridge",
            FAIL,
            f"bridge host {parsed.hostname!r} is not loopback",
            "the MrGhosty bridge is loopback-only — forward it with "
            f"`adb forward tcp:8378 tcp:8378` and use {DEFAULT_BRIDGE_URL}.",
        )
    token = bridge_token or env_token
    if not token:
        return Check(
            "phone bridge",
            FAIL,
            f"bridge URL set ({url}) but no pairing token",
            "set GHOST_HANDS_ANDROID_TOKEN to the pairing code shown "
            "in MrGhosty (Device tab → Ghost Hands bridge).",
        )
    status, payload, error = fetch_bridge_status(url, token, timeout)
    if status is None:
        return Check(
            "phone bridge",
            FAIL,
            f"bridge at {url} did not answer ({error})",
            BRIDGE_UNREACHABLE_FIX,
        )
    if status == 401:
        return Check(
            "phone bridge",
            FAIL,
            "bridge answered but rejected the pairing token (HTTP 401)",
            BRIDGE_TOKEN_FIX,
        )
    if status != 200 or payload is None:
        return Check(
            "phone bridge",
            FAIL,
            f"bridge at {url} answered HTTP {status} without a status payload",
            "check that MrGhosty — not something else — is on that "
            "port, then re-run doctor.",
        )
    if payload.get("ok") is not True:
        return Check(
            "phone bridge",
            FAIL,
            "bridge answered but did not report a healthy status (ok != true)",
            "open MrGhosty on the phone, check its Access checklist, "
            "then re-run doctor.",
        )
    protocol = payload.get("protocol")
    try:
        protocol_major = int(protocol)  # the bridge protocol is one int
    except (TypeError, ValueError):
        protocol_major = None
    if protocol_major != BRIDGE_PROTOCOL:
        return Check(
            "phone bridge",
            FAIL,
            f"bridge speaks protocol {protocol!r}; Ghost Hands "
            f"expects {BRIDGE_PROTOCOL}",
            "update MrGhosty or ghost-hands so the bridge protocol matches.",
        )
    app = payload.get("app")
    if app != "MrGhosty":
        return Check(
            "phone bridge",
            FAIL,
            f"bridge answered as app {app!r}, not 'MrGhosty'",
            "something else is on that port — forward MrGhosty's "
            "bridge with `adb forward tcp:8378 tcp:8378`.",
        )
    version = payload.get("version") or "?"
    if payload.get("service_connected") is not True:
        return Check(
            "phone bridge",
            FAIL,
            f"MrGhosty {version} bridge answers and the token is "
            "accepted, but the accessibility service is not connected",
            BRIDGE_A11Y_FIX,
        )
    return Check(
        "phone bridge",
        PASS,
        f"MrGhosty {version} at {url} — token accepted, "
        f"accessibility service connected, bridge protocol "
        f"{BRIDGE_PROTOCOL}",
    )


def adb_attached_devices(adb_path: str) -> list[str]:
    """Serials `adb devices` reports in the *device* state (the only
    adb call doctor/phone-proof ever make besides `forward` — it is
    read-only). Returns [] on any failure."""
    try:
        proc = subprocess.run(
            [adb_path, "devices"], capture_output=True, text=True, timeout=5
        )
    except Exception:
        return []
    serials = []
    for line in (proc.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def _check_adb(which: Callable) -> Check:
    adb = which("adb")
    if not adb:
        return Check(
            "adb",
            WARN,
            "adb not on PATH",
            "install Android platform-tools and put adb on PATH — "
            "needed only to forward the phone bridge over USB.",
        )
    try:
        proc = subprocess.run(
            [adb, "devices"], capture_output=True, text=True, timeout=5
        )
        output = proc.stdout or ""
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "").strip() or f"exit {proc.returncode}")
    except Exception as exc:
        return Check(
            "adb",
            WARN,
            f"adb present at {adb} but `adb devices` failed: {exc}",
            "check the adb install; doctor only reads `adb devices`.",
        )
    devices = []
    unauthorized = 0
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            if parts[1] == "device":
                devices.append(parts[0])
            elif parts[1] == "unauthorized":
                unauthorized += 1
    if devices:
        return Check("adb", PASS, f"{len(devices)} device(s) attached via adb")
    if unauthorized:
        return Check(
            "adb",
            WARN,
            "a device is attached but not authorized for USB debugging",
            "unlock the phone and accept the USB debugging prompt, "
            "then re-run doctor.",
        )
    return Check(
        "adb",
        WARN,
        "no device attached — phone proof needs USB debugging + the "
        "phone plugged in",
        "plug the phone in with USB debugging enabled (Settings → "
        "Developer options → USB debugging).",
    )


# ---------------------------------------------------------------------------
# the doctor run
# ---------------------------------------------------------------------------


def run_doctor(
    *,
    bus_url: Optional[str] = None,
    bridge_url: Optional[str] = None,
    bridge_token: Optional[str] = None,
    timeout: float = 3.0,
    env: Optional[dict] = None,
    which: Callable = shutil.which,
) -> list[Check]:
    """Run every check and return the rows. ``env`` replaces
    os.environ for configuration reads (tests pass hermetic dicts);
    ``timeout`` bounds each network probe. Read-only throughout."""
    env = dict(os.environ) if env is None else env
    timeout = max(0.1, float(timeout))
    return [
        _check_runtime(),
        _check_chromium(),
        _check_packs(),
        _check_model(env),
        _check_bus(bus_url, env, timeout),
        _check_bridge(bridge_url, bridge_token, env, timeout),
        _check_adb(which),
    ]


def summarize(checks: list[Check]) -> dict:
    counts = {PASS: 0, WARN: 0, FAIL: 0}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    return counts


def doctor_exit_code(checks: list[Check]) -> int:
    """1 iff any check FAILed — warnings never fail doctor."""
    return 1 if any(c.status == FAIL for c in checks) else 0


def format_doctor(checks: list[Check]) -> str:
    lines = [f"Ghost Hands doctor — ghost-hands {_version()}"]
    for check in checks:
        lines.append(
            f"{_SYMBOLS[check.status]} {check.name:<14} {check.detail}"
        )
        if check.status != PASS and check.fix:
            lines.append(f"    fix: {check.fix}")
    counts = summarize(checks)
    lines.append(
        f"doctor: {counts[PASS]} pass, {counts[WARN]} warn, "
        f"{counts[FAIL]} fail"
    )
    return "\n".join(lines)


def doctor_payload(checks: list[Check]) -> dict:
    counts = summarize(checks)
    return {
        "tool": "ghost-hands doctor",
        "version": _version(),
        "checks": [check.to_dict() for check in checks],
        "summary": {
            "pass": counts[PASS],
            "warn": counts[WARN],
            "fail": counts[FAIL],
        },
        "ok": counts[FAIL] == 0,
    }


def doctor_json(checks: list[Check]) -> str:
    return json.dumps(doctor_payload(checks), indent=2)
