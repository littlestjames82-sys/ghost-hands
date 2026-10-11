# Ghost Hands 0.9.1 — Hardening Report

Date: 2026-10-11. Scope: audit-first hardening of the 0.9.0 tree, up to
release-ready. No GitHub release was created and nothing was published —
that step is the parent's. Zero new runtime dependencies; MIT license
untouched; governed-action semantics (classify → record → approve →
execute; timeout/error always denies) unchanged.

Baseline (pre-change, this machine): **407 passed, 1 failed** —
`tests/test_busagent.py::test_hosted_workspace_prefix_end_to_end`,
which boots the *external* GhostBus hosted server and asserts its
`/health` answers within 15s; it never got that far. The same failure
reproduced in isolation after all changes, with no ghost-hands code
under test reached (see "Pre-existing failure" below).

Method: read every module in `src/ghost_hands/`, then wrote a proof
script demonstrating each candidate finding against the *unmodified*
0.9.0 code before fixing anything. Only findings with a working
pre-fix proof were fixed. Every fix has a regression test in
`tests/test_hardening_091.py` (35 tests).

## Findings fixed

### F1 — HIGH — Replay-export code injection via descriptor comments
- **Where:** `src/ghost_hands/export.py`, `_descriptor_comment`.
- **Proof (0.9.0):** a trail whose element name was
  `x"\n]\nimport os; os.system("echo PWNED")\nSTEPS_JUNK = [` exported
  into a script where `import os; os.system(...)` appeared as a
  top-level statement. Element names are page-controlled; the
  graduated script is meant to be executed by its owner, so a
  malicious (or compromised) page in a recorded run owned the
  machine that replayed it.
- **Fix:** `_comment_safe()` folds every control character
  (newlines first of all) to a space for all comment fields
  (number, tag, name, type, href, id). A comment is now always one
  physical line. Action data itself was already safe
  (`json.dumps`); the descriptor comments are unchanged otherwise.
- **Tests:** `test_export_descriptor_comment_cannot_inject_code`,
  `test_export_comment_folds_newlines_in_href_and_id`.

### F2 — MEDIUM — Governor domain lists ignored the target's destination
- **Where:** `src/ghost_hands/governor.py`, `Governor.evaluate`.
- **Proof (0.9.0):** with `blocked_domains=("evil.local",)`, clicking
  `<a href="https://evil.local/steal">` from `https://good.local/`
  returned `allow` ("readonly actions are allowed by policy"). Same
  under an allowlist. Only the *starting* page's host was checked,
  so the allow/block lists governed where a run began, not where it
  went — a click or form submit could carry the run to a blocked
  domain in one step.
- **Fix:** `evaluate()` now checks every host the action can reach:
  the effective URL (unchanged), the action URL for
  navigate/download, the element's `href` for click/double_click/
  right_click/download, and the element's `form_action` for
  click/double_click — relative destinations resolved against the
  page URL. Typing is deliberately *not* judged by its form's
  action host (typing navigates nowhere; the host is judged when a
  submit is attempted) — pinned by a test.
- **Tests:** `test_governor_blocked_domain_via_link_click`,
  `test_governor_allowlist_via_link_click`,
  `test_governor_blocked_domain_via_form_action`,
  `test_governor_same_host_and_relative_links_still_allowed`,
  `test_governor_typing_not_judged_by_form_action_host`.

### F3 — HIGH — Chromium navigated to `file://` (local-file disclosure)
- **Where:** `src/ghost_hands/drivers.py`, `ChromiumDriver.open` /
  `open_tab`.
- **Proof (0.9.0):** `ChromiumDriver().open("file:///etc/passwd")`
  did not raise — it launched and navigated. A governed `navigate`
  to `file://…` classifies readonly (no host to check), and a
  following `extract` returns `document.body.innerText` — i.e. the
  local file's text flows back to whatever decider (including a
  remote model endpoint) is driving. Internal schemes
  (`chrome://`, `view-source:`, `javascript:`, `devtools:`) were
  equally open.
- **Fix:** `_check_navigable()` runs before launch in `open()` and
  `open_tab()`: only `http`, `https`, `data` (bench/test fixtures),
  and `about` (about:blank) are navigable; relative URLs resolve
  against the current vetted page. Scheme detection strips ASCII
  whitespace/control characters the way browsers do, so
  `file\t://…` cannot slip past as scheme-less.
- **Tests:** `test_chromium_refuses_non_navigable_schemes`
  (7 parametrized URLs, both `open` and `open_tab`),
  `test_chromium_navigable_schemes_pass_the_check`.

### F4 — MEDIUM — MCP server killed by hostile/wrong-shape messages
- **Where:** `src/ghost_hands/mcp_server.py`, `handle_request`,
  `_serve_loop`, `_perceive`, `_run`.
- **Proof (0.9.0):** `handle_request(session, [1,2,3])` raised
  `AttributeError` (same for a string/number payload); `params` as a
  list raised `AttributeError`; `hands_perceive` with `pages: 123`
  raised `TypeError: 'int' object is not iterable`. None of these
  were caught by the stdio loop, so one bad line from a client
  killed the server.
- **Fix:** non-dict messages return JSON-RPC `-32600`; non-dict
  `params`/`arguments` return `-32602`; `pages` is validated as a
  {str: str} map and `steps` as a list of objects (tool-level
  errors, not crashes); single request lines are capped at 16 MiB
  (`MAX_LINE_BYTES`); the serve loop catches unexpected handler
  exceptions and replies with a generic `-32603` (no exception text
  leaked), then keeps serving.
- **Tests:** `test_mcp_non_dict_message_is_invalid_request`,
  `test_mcp_non_dict_params_and_arguments`,
  `test_mcp_pages_must_be_str_to_str_map`,
  `test_mcp_run_steps_must_be_action_objects`,
  `test_mcp_serve_loop_survives_garbage_and_oversize`.

### F5 — MEDIUM — BusClient accepted any URL scheme; workspace path escape; unbounded replies
- **Where:** `src/ghost_hands/busclient.py`, `__init__`, `_request`.
- **Proof (0.9.0):** `BusClient("file:///etc/passwd")`, `ftp://`,
  and `gopher://` were all accepted (`file://` turns `_request`
  into local file reads via urllib); `workspace="../../admin"`
  produced base URL `…/w/../../admin`, and `workspace="a/b?x=1"`
  injected a query string — the workspace argument could rewrite
  the request path out of its intended prefix. Response and error
  bodies were read with no size limit.
- **Fix:** base URL must be http(s) with a hostname; workspace ids
  must match `^[A-Za-z0-9][A-Za-z0-9_-]*$` (the slug shape every
  real caller uses: `studio`, `hands-test`). Replies are capped at
  16 MiB (`MAX_RESPONSE_BYTES`, `BusError` when exceeded); error
  bodies at 256 KiB. The existing 75s timeout was already present
  and is unchanged; key/token handling was already header-only and
  memory-only (see cleared findings).
- **Tests:** `test_busclient_refuses_non_http_schemes`,
  `test_busclient_refuses_workspace_traversal`,
  `test_busclient_response_size_cap`.

### F6 — LOW — Credential-bearing files created with umask permissions
- **Where:** `src/ghost_hands/trail.py` (`Trail`), `src/ghost_hands/drivers.py` (`ChromiumDriver.save_session`).
- **Proof (0.9.0):** a fresh trail file landed as `0o660` under this
  environment's umask (0644 under a typical one) — group/world
  readable despite recording typed text verbatim in action records
  (the v0.7 trail audit documented passwords appearing there).
  `save_session` files hold cookies + localStorage — credentials
  by SECURITY.md's own wording — and had the same exposure.
- **Fix:** trails are created via `os.open(..., 0o600)`, and a
  pre-existing loose trail file is tightened to 0600 when a `Trail`
  is opened on it. `save_session` writes via `os.open(..., 0o600)`
  and chmods the result 0600 (covers pre-existing loose files).
- **Tests:** `test_trail_file_created_0600`,
  `test_trail_preexisting_loose_file_is_tightened`,
  `test_session_file_created_0600` (local-fixture Chromium test;
  skips without a browser).

### F7 — LOW — Unbounded HTTP response bodies in the remaining clients
- **Where:** `android_driver.py` (`_request`),
  `phone_approver.py` (`_request`), `deciders.py`
  (`OpenAICompatibleDecider.decide`), `doctor.py` (`_http_get_json`).
- **Proof (0.9.0):** every one read `resp.read()` with no cap; a
  hostile/compromised endpoint (or whatever squats on a forwarded
  port) could exhaust memory with one reply.
- **Fix:** capped reads sized to each surface's largest legitimate
  payload: Android bridge 32 MiB (base64 screenshots), decider
  16 MiB, PhoneApprover 1 MiB, doctor probes 1 MiB; error bodies
  256 KiB. Semantics preserved: the approver still fails closed —
  oversize is an error, and an error is a denial, never consent;
  doctor reads over-cap as "not a JSON object" and reports a
  failure row, as it already did for garbage bodies.
- **Tests:** `test_android_driver_response_size_cap`,
  `test_phone_approver_oversize_response_never_approves`,
  `test_decider_response_size_cap`,
  `test_doctor_probe_response_size_cap`.

### F8 — LOW — Download filename traversal via CDP `suggestedFilename`
- **Where:** `src/ghost_hands/drivers.py`, `_on_cdp_event`
  (`Browser.downloadWillBegin`) → `_await_download`.
- **Proof (0.9.0, code + targeted event test):** the page-controlled
  `suggestedFilename` was stored verbatim and later joined onto the
  download directory; a `../../name` filename made the completion
  check resolve (and report as downloaded) a pre-existing file
  outside the download directory. Chrome itself writes safely; the
  lie was in Ghost Hands' own accounting.
- **Fix:** the filename is reduced to its bare name
  (`Path(name).name`) at ingestion.
- **Test:** `test_download_suggested_filename_reduced_to_bare_name`.

## Cleared non-findings (audited, no change)

- **JS evaluation injection (CDP layer).** Every user/page-derived
  value interpolated into a `Runtime.evaluate` expression goes
  through `json.dumps` (select value, fill_form pairs, set_local_
  storage items); typing uses the `Input.insertText` CDP parameter,
  not string-built JS; element numbers interpolated into JS come
  from `ElementMap` (ints by construction). No unsanitized
  interpolation found.
- **Approval semantics.** Runner: ask with no approver is rewritten
  to deny before recording; an approver returning False stops the
  run; approver *exceptions* propagate and the action never runs.
  BusApprover and PhoneApprover: timeout = deny, transport error =
  deny, 401 = deny, bridge has no decision endpoint. Nothing that
  executes a consequential action without classify + record +
  approval was found.
- **Token/secret leakage.** Bus key, agent token, Android pairing
  token, and model API key are header-only, memory-only, and absent
  from trail events, error strings, doctor output, and bus payloads;
  existing tests already pin several of these and passed unchanged.
- **Subprocess surfaces.** All `subprocess` uses are list-form
  (Chrome launch via the fd shim, `adb forward`/`adb devices`,
  doctor's `--version` probe, GhostGuard bridge, bench/demo
  harnesses). No `shell=True`, no string commands — no shell
  injection surface. GhostGuard bridge failure converts to deny.
- **Trail integrity.** The trail is an append-only provenance log
  with record-before-execute ordering; it makes no tamper-evidence
  claim (SECURITY.md is explicit that policy is pattern matching,
  not a proof), so no hash chain was added — that would be a format
  feature, not a fix.
- **BusApprover reply parsing.** Replies must carry the run's unique
  approval id; a bare `DENY`-prefixed message without the id is
  ignored by the id check that runs first; approvals can only come
  from other bus agents. Fail direction is deny.
- **Auth proxy relay.** Binds 127.0.0.1 only, injects credentials
  only upstream, strips client `Proxy-Authorization`; ephemeral by
  design (lives only while a proxied Chromium session runs).
- **Evaluate stub/fixture servers.** Loopback-only local test
  harness, not a shipped runtime surface.
- **`press` classification (residual risk, noted not changed).**
  `press` is classified readonly by design and pinned by tests —
  but in a real browser, Enter inside a form can implicitly submit
  it. The Governor receives no target/focus context for `press`, so
  there is no sound local reclassification; closing this properly
  is a semantics decision (e.g. threading the last-focused element
  into classification) for the maintainer, not a patch.
- **Screenshot/PDF paths (residual risk, noted not changed).**
  `screenshot`/`pdf` are classified readonly by design (pinned by
  tests and bench), yet with an explicit `path` they write a file.
  The path comes from the decider/operator, never from page
  content, and reclassifying would change pinned public semantics;
  flagged here for the maintainer's call.
- **Self-heal re-governance (residual risk, noted not changed).**
  A healed action is re-found by descriptor (tag+role+name) and
  keeps its original verdict; with F2 fixed, the original verdict
  covers the original target's hosts, but a descriptor-identical
  *different* element with a different href could in principle be
  healed onto. Requires a page mutation racing perception plus a
  same-key decoy element; re-governing heals (with re-approval
  plumbing) is a Runner semantics change beyond a patch release.

## Verification

- New hardening tests: **35/35 passed** (`tests/test_hardening_091.py`).
- Affected existing suites (governor, MCP, MCP parity, bus client,
  trail/export, runner, seatbelt, policies, deciders, doctor, phone
  approver, Android driver, report, fake drivers, GhostGuard
  backend): **251/251 passed**.
- Offline bench: **GHOST HANDS BENCH: 95/95 passed**.
- Full suite: **442 passed, 1 failed** in 212s — exactly the
  baseline's shape (407 passed at baseline + 35 new hardening
  tests = 442), and the single failure is the pre-existing
  external-GhostBus one below. Zero regressions.
- Two `tests/test_doctor.py` assertions pinned the literal version
  string `0.9.0`; they were updated to `0.9.1` as part of the bump
  (the bump's own bookkeeping, not a behavior change).

## Pre-existing failure

`tests/test_busagent.py::test_hosted_workspace_prefix_end_to_end`
fails identically before and after this patch: it boots the
GhostBus *hosted* server from `~/workspace/ghostbus` and the server
does not answer `/health` within 15s in this environment right now.
The failure occurs before any ghost-hands code is exercised; the
bus *agent* path against the single-workspace server passes (bench
case `v4 bus` and `v7 bus` both green). Likely entangled with the
concurrent GhostBus hardening work; needs a look from that side,
not from this patch.

## Release artifacts (built locally, not published)

- `dist/ghost_hands-0.9.1-py3-none-any.whl` (176,802 bytes)
- `dist/ghost_hands-0.9.1.tar.gz` (555,990 bytes)
- Clean-venv check: fresh venv + `pip install` of the wheel →
  `import ghost_hands` reports `0.9.1`; `ghost-hands demo` runs the
  full scripted demo and exits 0.

## What remains for the parent to publish

1. (Optional) Re-run the full suite once the GhostBus hosted server
   boots again, to return the tree to a fully green claim.
2. Commit/push the tree (version bump in `pyproject.toml` +
   `src/ghost_hands/__init__.py`, `CHANGELOG.md` 0.9.1 entry, the
   eight source fixes, `tests/test_hardening_091.py`, the two
   version-pin updates in `tests/test_doctor.py`, this report).
3. Create the GitHub release `v0.9.1` — the repo's `publish.yml`
   (release → trusted publishing) builds and uploads to PyPI from
   the release, exactly as 0.9.0 shipped. Verify `pip index` /
   PyPI shows `ghost-hands 0.9.1` afterwards.
