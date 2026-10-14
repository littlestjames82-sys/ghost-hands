# Ghost Hands Doctor Walkthrough

`ghost-hands doctor` provides one-command diagnostics across the entire runtime stack. It checks local prerequisites, browser availability, security policies, LLM credentials, and optional subsystem bridges (GhostBus and the Android phone bridge).

## Real Output Capture

The following is an authentic terminal capture of `ghost-hands doctor` executed in a standard local environment with no external services or phone paired:

```text
Ghost Hands doctor — ghost-hands 0.9.2
✓ runtime        Python 3.13.15 · ghost-hands 0.9.2
✗ chromium       no Chromium/Chrome binary found
    fix: Install Chrome/Chromium or set GHOST_HANDS_CHROME to the binary.
✓ policy packs   all 4 packs load: readonly, standard, strict, seatbelt
! model env      no model key — stub/rules/eval-stub still work; live model runs need a key
    fix: set GHOST_HANDS_API_KEY (with GHOST_HANDS_BASE_URL) or OPENROUTER_API_KEY to enable live model runs.
! ghostbus       not configured — bus features idle
    fix: pass --bus <url> or set GHOSTBUS_URL to check a GhostBus server.
! phone bridge   not configured — the Android body and phone approvals are idle
    fix: pair a phone: set GHOST_HANDS_ANDROID_TOKEN to the pairing code in MrGhosty's Access checklist (bridge URL defaults to http://127.0.0.1:8378).
! adb            adb not on PATH
    fix: install Android platform-tools and put adb on PATH — needed only to forward the phone bridge over USB.
doctor: 2 pass, 4 warn, 1 fail
```

JSON output is also supported via `ghost-hands doctor --json`:

```json
{
  "tool": "ghost-hands doctor",
  "version": "0.9.2",
  "checks": [
    {
      "name": "runtime",
      "status": "pass",
      "detail": "Python 3.13.15 · ghost-hands 0.9.2",
      "fix": ""
    },
    {
      "name": "chromium",
      "status": "fail",
      "detail": "no Chromium/Chrome binary found",
      "fix": "Install Chrome/Chromium or set GHOST_HANDS_CHROME to the binary."
    },
    {
      "name": "policy packs",
      "status": "pass",
      "detail": "all 4 packs load: readonly, standard, strict, seatbelt",
      "fix": ""
    },
    {
      "name": "model env",
      "status": "warn",
      "detail": "no model key — stub/rules/eval-stub still work; live model runs need a key",
      "fix": "set GHOST_HANDS_API_KEY (with GHOST_HANDS_BASE_URL) or OPENROUTER_API_KEY to enable live model runs."
    },
    {
      "name": "ghostbus",
      "status": "warn",
      "detail": "not configured — bus features idle",
      "fix": "pass --bus <url> or set GHOSTBUS_URL to check a GhostBus server."
    },
    {
      "name": "phone bridge",
      "status": "warn",
      "detail": "not configured — the Android body and phone approvals are idle",
      "fix": "pair a phone: set GHOST_HANDS_ANDROID_TOKEN to the pairing code in MrGhosty's Access checklist (bridge URL defaults to http://127.0.0.1:8378)."
    },
    {
      "name": "adb",
      "status": "warn",
      "detail": "adb not on PATH",
      "fix": "install Android platform-tools and put adb on PATH — needed only to forward the phone bridge over USB."
    }
  ],
  "summary": {
    "pass": 2,
    "warn": 4,
    "fail": 1
  },
  "ok": false
}
```

---

## Detailed Check Breakdown

### 1. `runtime`
- **What it proves:** Verifies the host Python interpreter version and confirms the `ghost-hands` package is imported and operational.
- **Statuses:**
  - `PASS`: Python and package version match expected constraints.
- **Printed fix:** None required when passing.

### 2. `chromium`
- **What it proves:** Locates a usable Chrome or Chromium binary for browser automation drivers (via standard system installation paths or the `GHOST_HANDS_CHROME` environment variable).
- **Statuses:**
  - `PASS`: Detects installed Chrome/Chromium binary and reports its version.
  - `FAIL`: `no Chromium/Chrome binary found`.
- **Printed fix:** `Install Chrome/Chromium or set GHOST_HANDS_CHROME to the binary.`

### 3. `policy packs`
- **What it proves:** Verifies that all built-in security policy packs (`readonly`, `standard`, `strict`, `seatbelt`) parse and load correctly without schema errors or syntax corruption.
- **Statuses:**
  - `PASS`: `all 4 packs load: readonly, standard, strict, seatbelt`.
  - `FAIL`: Reports syntax error or invalid policy rule definition.
- **Printed fix:** Correct any corrupted YAML/JSON policy definitions in the configuration.

### 4. `model env`
- **What it proves:** Checks whether API keys for external model providers (`GHOST_HANDS_API_KEY` with `GHOST_HANDS_BASE_URL` or `OPENROUTER_API_KEY`) are present in the environment.
- **Statuses:**
  - `PASS`: Key is present; displays the target model name while ensuring secret keys are strictly redacted and never printed.
  - `WARN`: `no model key — stub/rules/eval-stub still work; live model runs need a key`. Offline stub modes, rules engines, and eval stubs remain functional without a live model.
- **Printed fix:** `set GHOST_HANDS_API_KEY (with GHOST_HANDS_BASE_URL) or OPENROUTER_API_KEY to enable live model runs.`

### 5. `ghostbus`
- **What it proves:** Tests connectivity to the GhostBus event bus coordinator if configured via `--bus <url>` or `GHOSTBUS_URL`.
- **Statuses:**
  - `PASS`: GhostBus server is reachable and reports workspace mode (e.g. `single-workspace`).
  - `WARN`: `not configured — bus features idle`. Unconfigured bus is non-fatal (exits 0).
  - `FAIL`: Bus URL configured but connection refused or timed out.
- **Printed fix:** `pass --bus <url> or set GHOSTBUS_URL to check a GhostBus server.` (or `start a GhostBus server` if configured but unreachable).

### 6. `phone bridge`
- **What it proves:** Validates communication with the MrGhosty Android phone bridge service over HTTP loopback (`http://127.0.0.1:8378` by default) using pairing token authentication.
- **Statuses:**
  - `PASS`: Bridge service is up, pairing token is accepted, accessibility service is active, and protocol version matches.
  - `WARN`: `not configured — the Android body and phone approvals are idle`.
  - `FAIL`: Unreachable, pairing token mismatch (HTTP 401), accessibility service disabled on the device, or protocol version mismatch.
- **Printed fixes:**
  - If unconfigured: `pair a phone: set GHOST_HANDS_ANDROID_TOKEN to the pairing code in MrGhosty's Access checklist (bridge URL defaults to http://127.0.0.1:8378).`
  - If unreachable: `Is the phone on, MrGhosty accessibility on, and 'adb forward tcp:8378 tcp:8378' running?`
  - If token mismatch: `Pairing token mismatch — re-copy it from MrGhosty's Access checklist into GHOST_HANDS_ANDROID_TOKEN.`

### 7. `adb`
- **What it proves:** Checks if the Android Debug Bridge CLI tool is installed and accessible on `PATH`.
- **Statuses:**
  - `PASS`: `adb` located on system PATH.
  - `WARN`: `adb not on PATH`. Only required if forwarding the phone bridge over a USB connection.
- **Printed fix:** `install Android platform-tools and put adb on PATH — needed only to forward the phone bridge over USB.`

---

## Exit Codes and Automation

- `0`: All checks passed, or only non-blocking warnings were raised (e.g. unconfigured optional subsystems).
- `1`: One or more hard failures occurred (e.g. missing browser binary or unreachable configured bridge).
