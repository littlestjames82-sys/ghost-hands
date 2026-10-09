# Ghost Hands v0.6.0 — finish + verify progress

Started: 2026-10-08 (parent task: finish v0.6.0 — PhoneApprover, the
Governor's third approval channel; do NOT publish, local files only).

## Assessment (2026-10-08)

Already in tree and complete on inspection:
- `src/ghost_hands/phone_approver.py` — full implementation: redacted
  `build_summary` (type = char count only, fill_form = field names
  only, 280-char cap), `PhoneApprover` (loopback-only refusal,
  token via `X-Ghost-Hands-Token`, never logged/trailed), poll cycle
  (approved only → True; denied/expired/timeout/404/401/unreachable →
  False), trail `approval` events channel `phone`, `test_approval()`,
  `ObservingDecider` wrapper.
- `docs/APPROVALS.md` — complete: three-channel table, phone-channel
  protocol, no-API-decision rule, redaction rule, on-device proof
  steps (honestly marked as requiring MrGhosty install).
- `tests/fake_android_bridge.py` — approval endpoints added:
  `POST /v1/approvals` (validation, 400/409), `GET /v1/approvals/<id>`
  with lazy expiry, NO decision endpoint; decisions only via fixture
  hooks `decide_approval` / `expire_approval`; raw payloads retained
  for redaction assertions.
- CLI wiring: `run --approver phone` (conflicts with `--approve-all`
  → usage error), `bus-agent --approval-channel bus|phone` + env
  `GHOST_HANDS_APPROVAL_CHANNEL`, `phone-approval-test` subcommand.
- `busagent.py` — phone channel routes asks to PhoneApprover, bus
  gets an outcome comment only.
- `bench.py` — a PhoneApprover bench case (~line 2205).
- `pyproject.toml` version = 0.6.0; `__init__.py` exports PhoneApprover.

## Left to do

- [x] Run full pytest suite — 299 passed
- [x] Run offline bench (88/88) + live bench (2/2)
- [x] Import audit: stdlib-only proof
- [x] Build dist wheel + sdist for 0.6.0
- [x] CHANGELOG.md [0.6.0] entry — was already written by the prior
      builder; every ghost-hands number in it re-verified by this run
      (299 / 88 / 2/2 all reproduce exactly), so it stands as-is
- [x] README "Honest status" → v0.6.0 — already updated by the prior
      builder; verified consistent with measured numbers, including
      the plain on-device-pending statement

## Verification results (2026-10-08, this run)

- pytest: **299 passed** in 235s (28 tests in the two phone-approver
  files; +30 over the v0.5 baseline of 269 across the suite).
- Offline bench: **88/88** — includes "v6 phone approvals:
  consequential delete approved on the phone mid-run; redacted
  payload".
- Live bench: **2/2** (example.com + Wikipedia via existing relay).
- Import audit: AST scan of `src/ghost_hands/*.py` — **zero
  third-party imports**; pyproject `dependencies = []`.
- Dist built: `dist/ghost_hands-0.6.0-py3-none-any.whl` (123,802 B),
  `dist/ghost_hands-0.6.0.tar.gz` (454,640 B). Wheel contains
  `phone_approver.py`, METADATA version 0.6.0, and imports cleanly
  from a fresh target dir (`from ghost_hands import PhoneApprover`).
- Phone side (read-only check, untouched): MrGhosty v1.7.0 is in the
  ghostbot tree — AndroidManifest versionName 1.7.0,
  GhostHandsBridge implements POST /v1/approvals +
  GET /v1/approvals/<id> with an explicit no-decision-route comment;
  GhostHandsApprovals/Hub/Receiver + JVM test present.
- NOT published: no push, no release, no PyPI upload, no commits —
  per Ryan's gate.

## Honest gap (unchanged, stated in CHANGELOG + README)

On-device proof still pending: no phone was attached during the
build. It needs Ryan to install MrGhosty v1.7.0, grant accessibility,
`adb forward tcp:8378 tcp:8378`, then `ghost-hands
phone-approval-test` (exits 0 only on a phone approval).

STATUS: COMPLETE (local build + verification only).
