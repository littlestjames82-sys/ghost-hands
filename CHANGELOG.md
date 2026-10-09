# Changelog

All notable changes to Ghost Hands. Versions follow the releases on
[PyPI](https://pypi.org/project/ghost-hands/) and
[GitHub](https://github.com/littlestjames82-sys/ghost-hands/releases).
Every number below was measured on the build it describes — see the
README's "Honest status" for what remains unproven at each step.

## [0.9.0] — 2026-10-08 (built + verified locally; not published)

The field kit wave: one command that diagnoses the whole stack, and
the guided walk that turns a fresh phone install into a proven one.
No MrGhosty changes this wave. Measured on this build: pytest
395/395, offline bench 95/95.

- **`ghost-hands doctor`** (`doctor.py`). Seven read-only checks —
  runtime (Python ≥ 3.10 + the package version), Chromium (via the
  driver's own `find_chrome`, so doctor and the driver can never
  disagree; PASS carries the path + `--version` line), the policy
  packs (readonly / standard / strict / seatbelt all load; a FAIL
  names the broken pack), the model env (key presence only —
  GHOST_HANDS_API_KEY / OPENROUTER_API_KEY — with base URL / model
  shown when set, since those are not secrets), GhostBus (only
  when configured via `--bus` / GHOST_HANDS_BUS_URL / GHOSTBUS_URL:
  `GET /health` with the workspace shape detected — single vs
  hosted `/w/<id>/`; unconfigured is a WARN, "not configured —
  bus features idle", never a failure), the phone bridge (only
  when GHOST_HANDS_ANDROID_BRIDGE / GHOST_HANDS_ANDROID_TOKEN
  configure one: reachability, token acceptance, the MrGhosty
  version from `/v1/status`, accessibility state, and the bridge
  protocol matched against the driver's BRIDGE_PROTOCOL), and adb
  (`adb devices` parsed; absent or device-less is a WARN). Rows
  render ✓/!/✗ with a one-line plain-language fix for every
  non-PASS; the FAIL fixes for a dead bridge ("Is the phone on,
  MrGhosty accessibility on, and `adb forward tcp:8378 tcp:8378`
  running?") and a rejected token ("Pairing token mismatch —
  re-copy it from MrGhosty's Access checklist into
  GHOST_HANDS_ANDROID_TOKEN.") are pinned verbatim by tests.
  Summary line `doctor: N pass, N warn, N fail`; exit 1 iff any
  FAIL. `--json` emits the checks as structured data. Every probe
  is timeout-bounded (3s default) — a test holds an endpoint that
  accepts and never replies and asserts the whole run stays fast.
  Secrets are never printed: tests assert the model key's value
  and the pairing token appear in neither the human output nor
  the JSON.
- **`ghost-hands phone-proof`** (`phone_proof.py`). The guided
  on-device proof: five steps, each printed and then CHECKED.
  (1) Bridge configured — without a pairing token it prints the
  exact env setup and stops, exit 2 (needs-setup). (2) The adb
  forward: with adb + an attached device it runs `adb forward
  tcp:8378 tcp:8378` (announced first; the port follows the
  bridge URL), otherwise it notes the skip and relies on the
  bridge already being reachable. (3) The bridge status, the
  `android-status` equivalent: MrGhosty version, protocol,
  accessibility connected. (4) The harmless test approval (the
  v0.6 `PhoneApprover.test_approval`, reused unchanged) under a
  "LOOK AT YOUR PHONE NOW" prompt — approved continues; denied,
  expired, or timed out ends gracefully with instructions,
  exit 3 (not approved); a transport error is exit 1.
  (5) The summary: what is now proven on this phone, and the
  exact next commands (`run --driver android --approver phone`,
  `bus-agent --driver android --approval-channel phone`). TTY
  pauses between steps go through an injectable input function;
  `--yes` runs straight through — the mode the tests and bench
  use against the fake bridge (approve → exit 0 with the proven
  summary; deny and timeout → exit 3; unconfigured → exit 2; a
  fake `adb` script proves the forward step). The token never
  appears in the output.

## [0.8.0] — 2026-10-08 (built + verified locally; not published)

The receipts + reach wave: the trail becomes a human-readable audit
report, the MCP server reaches every body and the phone approval
channel, and a fourth policy pack adds a prompt-injection tripwire.
No MrGhosty changes this wave. Measured on this build: pytest
365/365, offline bench 93/93.

- **Trail → HTML audit report** (`report.py`, `ghost-hands report
  trail.jsonl [-o out.html]`; default output = the trail path with an
  `.html` suffix). One self-contained HTML file per run — inline CSS,
  no JavaScript, no external assets — with a header (goal when the
  trail records one, body inferred from perception URLs, date range,
  stop reason + summary), stat cards (steps, wall duration,
  perception chars / ~tokens, actions by class, approvals
  requested/granted/denied, heals, downloads/dialogs/net counts), and
  a step timeline: perception summary + collapsible record, the
  decision in plain language, the Governor's verdict (classification
  chip; asks resolved as "ask → approved/denied via {channel}"), the
  result, and special events inline (heals, dialogs, downloads,
  approval pairs with channel + decider, network capture). Safety:
  every trail string is HTML-escaped (XSS-shaped titles/names render
  as text — tested), typed values are truncated at 80 chars, and
  values typed into `type=password` elements (or `fill_form` fields
  with "password" in the key) render as `•••• (N chars)`. Corrupt
  lines are skipped and counted in the footer. Network events are
  recognized in both their shapes — including real Chromium trails,
  where the sunk resource type occupies the event's `type` field.
- **MCP parity** (`mcp_server.py`). `GHOST_HANDS_DRIVER` selects the
  session body — `fake` (default, unchanged), `chromium`, `simphone`,
  `android` — with `GHOST_HANDS_START_URL` opened on the first
  perceive; android uses `GHOST_HANDS_ANDROID_BRIDGE` /
  `GHOST_HANDS_ANDROID_TOKEN` exactly like the bus agent (token never
  in tool output). Bodies with native eyes perceive through them.
  `hands_run` defaults to the session body and accepts all four
  drivers. `GHOST_HANDS_APPROVER=phone` routes asks through
  `PhoneApprover` (session acts and runs); `hands_act` replies name
  the deciding channel ("approved via phone" / "denied: no approver
  (MCP default)"). Unknown env values fail loudly at startup.
- **`seatbelt` policy pack** (`policies.py`, `governor.py`).
  Standard's posture with consequential explicitly ASK on a 300s
  leash, plus `Policy.deny_patterns` — a small, general Governor
  extension: substrings matched case-insensitively (with URL
  separators folded to spaces) against the target element's
  name/label and the page/action URL deny the action outright,
  before class outcomes. The pack's list is the curated,
  documented `INJECTION_PATTERNS` (instruction-shaped phrases:
  "ignore previous instructions", "disregard your instructions",
  "you are now", "system prompt", "click here to claim",
  "verify your password to continue", …); govern reasons name the
  matched pattern ("seatbelt: injection-shaped target"). Benign
  lookalikes are guarded by tests: a button named "Ignore" does not
  trip it. Packs now number four: readonly / standard / strict /
  seatbelt; `Policy.to_dict`/`from_dict` round-trip the new field,
  and packs without patterns behave exactly as before.

## [0.7.0] — 2026-10-08 (built + verified locally; not published)

The proof + depth wave: the one capability never honestly measured —
a real model driving the hands — gets its measuring instrument; the
Android body learns gestures; and the GhostBus file-store caveat
from v0.4 is closed with evidence.

- **Model evaluation harness** (`evaluate.py`, `ghost-hands eval`).
  Eight graded tasks against local fixtures, four on real Chromium:
  search-and-open-result, multi-field form fill + consequential
  submit (the ask must occur; the eval auto-approves), structured
  table extraction of a named cell, multi-hop navigation to a fact,
  a trap page (success = goal done, destructive button never
  executed), a shifting page scored on heal recovery, a login form
  with a trail-leak audit, and an impossible goal scored on honest
  stopping. Per-task scoring: success, steps, wall time, perception
  chars / ~tokens, actions by class, approvals requested, heals;
  suite totals, a plain-language verdict, and `--report PATH` JSON.
  Task failures are data — the harness exits 0 when it ran cleanly.
  Deciders: `--decider stub` (a local OpenAI-compatible stub model
  server with a deterministic policy; every output labeled "stub
  model — harness verification only, not a model-quality
  measurement"), `--decider rules` (RuleDecider baseline; tasks it
  cannot attempt are honest failures), `--decider openai` (the
  existing env-configured decider), `--decider all`.
  Measured on this build: **stub 8/8** (instrument proven end to
  end), **rules 3/8** (trap avoidance, heal recovery, honest stop;
  honest failures on search, form, extraction, multi-hop, login).
  **Real-model run: not completed for this build.** Both
  `GHOST_HANDS_API_KEY` and `OPENROUTER_API_KEY` were confirmed
  present in the environment (presence checks only — values never
  printed), but the key material was not exposed to the build
  sandbox's exec contexts at any eval attempt, so the harness's
  provider resolution found no usable key and skipped the run by
  design; no numbers are claimed. What it would run, exactly:
  the Chromium subset (`search-and-open-result`,
  `form-fill-and-submit`, `extract-table-cell`,
  `login-trail-audit`) against OpenRouter
  (`https://openrouter.ai/api/v1`, model `openai/gpt-4o-mini` —
  `GHOST_HANDS_MODEL` unset) with the key from
  `OPENROUTER_API_KEY`, via `ghost-hands eval --decider openai`.
  One finding from building the login task: credentials typed into
  a GET-method login form land in the post-submit URL, which
  perceive/network trail events record — the shipped fixture uses
  POST (as real login forms do), and the audit pins the result:
  the password appears only inside decide/govern/execute action
  records (the trail's existing verbatim-action behavior), never
  in results, the summary, or any other event. Trail semantics are
  unchanged.
- **Android gestures** (MrGhosty **v1.8.0**, versionCode 9, cert
  and 32 permissions unchanged; APK md5
  `10a88b617c294827a6f6e3bb1f437dc4`, 2,844,022 bytes). The bridge
  implements `double_click` (two dispatchGesture strokes ~120ms
  apart at the target's bounds center), `drag` (both ends
  ref-verified; one ~300ms stroke center-to-center), and `click_at`
  (no target; coordinates validated against the active window's
  root bounds, out-of-bounds refused honestly) — all awaited on a
  bounded gesture-callback latch with honest cancelled/failed
  results. `AndroidDriver` forwards all three (`drag` sends
  `to_ref`/`to_expected`; `click_at` needs no element). The fake
  bridge fixture implements the same contract (gestures recorded;
  slider drag sets its value; click_at hit-tests bounds or misses
  honestly). SimPhoneDriver unchanged — no fake parity.
  JVM suites: Protocol 62/62 (8 new gesture-math checks),
  Approvals 44/44, ActionGrammar, LlamaPrompt 21/21, Briefing
  18/18. `docs/BODY_PROTOCOL.md` §6 updated.
- **GhostBus file-store loop closed.** No GhostBus code change was
  needed from this wave: the shared-`.tmp` rename race was already
  fixed upstream in **GhostBus 0.4.1** (unique temp per save +
  serialized saves) with a permanent regression test in its suite
  since 0.5.0; suites re-verified for this build (**44/44 main +
  6/6 serverless**). The loop is closed from the Ghost Hands side:
  `run_bus_demo(store_dir=...)` + bench case `v7 bus` run the full
  demo flow against a file-backed store, and
  `test_file_store_barrage_no_races` runs a parallel agent+poller
  barrage — zero HTTP 400s, intact store JSON, no orphan `.tmp`
  files. One narrower characteristic was characterized, not fixed
  (it is GhostBus core's documented design): shared stores re-read
  state per operation for cross-process sharing, so a free-for-all
  of many simultaneous writers can still lose updates silently;
  the agent+poller workload Ghost Hands generates is unaffected
  (measured).
- Verified on this build: **pytest 321 passed** (299 at v0.6.0),
  **offline bench 91/91** (88 at v0.6.0; new cases: `v7 android
  gestures`, `v7 bus: file-store …`, `v7 eval harness`), **live
  bench 2/2**. Dist: wheel **140,617 B**, sdist **491,232 B**.
  Zero-dependency stdlib only; grep audit: Playwright/Selenium
  appear only in negative assertions and comparison copy.

## [0.6.0] — 2026-10-08

The phone-decides wave: the last roadmap approver lands. Released
Oct 8, 2026 — this release brings the repo current: 0.4.0 and 0.5.0
(below) were built and verified but never got their own releases.

- **PhoneApprover** (`phone_approver.py`) — consequential asks route
  to Ryan's phone through the Ghost Hands bridge in MrGhosty
  v1.7.0. On an ask it POSTs a redacted approval request and polls
  for the phone's decision; approved → the action runs, and denied /
  expired / timeout / unreachable / 401 all mean it never does.
  Same bridge config as AndroidDriver (loopback-only by default,
  token from constructor args or `GHOST_HANDS_ANDROID_*` env, never
  logged or trailed). Trail approval events carry
  `channel: "phone"`, request + decision phases, and
  `decided_by: "phone"`.
- **Redacted summaries, by construction.** Requests carry kind,
  classification, target descriptor, and current URL/package only;
  `type` shows the target + character count (never the text — it
  may be a password), `fill_form` shows field names (never values).
  The contract tests prove a secret string never appears in the
  payload the bridge receives.
- **Bridge protocol addition** — `POST /v1/approvals` (201 /
  409 duplicate / 400 bad payload) and `GET /v1/approvals/<id>`
  (pending | approved | denied | expired, `decided_by` /
  `decided_at` when settled; 404 unknown). **There is intentionally
  no decision endpoint**: a token holder can create a request but
  can never approve their own — decisions exist only in MrGhosty's
  notification actions and Device-tab card.
- **CLI** — `ghost-hands run --approver phone` works with every
  driver (the phone can approve web runs too); conflicting approver
  flags exit 2. New `ghost-hands phone-approval-test` sends one
  harmless test approval and exits 0 only when the phone approves —
  the one-command on-device proof.
- **Bus agent** — `--approval-channel bus|phone` (env
  `GHOST_HANDS_APPROVAL_CHANNEL`). With `phone`, consequential asks
  route to the phone; the agent posts a bus comment noting the
  phone decision and never requests an approval from the bus.
- **MrGhosty v1.7.0** (companion app) — approval store
  (JVM-tested core: create/pending/decide-only-while-pending,
  lazy expiry, duplicate rejection, JSON persistence), the two
  bridge endpoints, a high-importance "Ghost Hands approvals"
  notification with Approve/Deny actions, and a Device-tab
  approvals card. Same signing cert, same 32 permissions.
- **Verification** — pytest 299/299, offline bench 88/88 (new v6
  phone-approval case: a consequential delete approved on the
  simulated phone mid-run, wire payload asserted free of the typed
  text and the pairing token), live bench 2/2. On-device proof
  still pending Ryan's install of MrGhosty v1.7.0 — no phone was
  attached during this build.

## [0.5.0] — 2026-10-08

The real-hands wave: the Android body stops being a spec and a
simulator and becomes a driver plus a phone-side bridge.

- **AndroidDriver** (`android_driver.py`) — the real Android body.
  Speaks HTTP/JSON to the Ghost Hands bridge in MrGhosty v1.6.0
  (loopback `127.0.0.1:8378`, pairing token as
  `X-Ghost-Hands-Token` from the constructor or
  `GHOST_HANDS_ANDROID_TOKEN`, never logged or trailed). Non-loopback
  bridge hosts are refused unless explicitly allowed. Status is
  validated (protocol 1, app identity, accessibility service
  connected) with actionable errors for the unreachable / 401 /
  not-enabled cases.
- **Bridge protocol** — `GET /v1/status`, `POST /v1/perceive`
  (accessibility-tree elements with opaque child-index `ref`s),
  `POST /v1/act` with `{action, target_ref, expected:{tag,role,name}}`;
  the bridge's 404 "target missing" / 409 "target mismatch" replies
  feed the Runner's self-healing unchanged. Core actions supported
  (navigate app/URL, click, type, global-action press, scroll,
  extract text, real PNG screenshot on API 30+, conditioned waits
  polled locally); web-only actions fail honestly, never faked.
- **Element `body_ref`** — an optional opaque body-native target
  reference, carried through descriptors into the trail.
- **CLI** — `ghost-hands android-status` (bridge/app/version/
  foreground/API level, token never printed) and
  `run --driver android` with `--android-bridge` / `--android-token`;
  bus-agent accepts `--driver android` (env-configured only).
- **MrGhosty v1.6.0** (companion app) — the Ghost Hands bridge inside
  the accessibility service: token generation/rotation in Prefs, a
  "Ghost Hands bridge" Access-checklist row with the pairing code
  (app UI only), JVM-tested protocol mapping (54 checks).
- Verified: 269 pytest (+44: driver units + a fake-bridge end-to-end
  suite incl. heal-after-mutation), bench 87/87 (+1 Android case),
  live bench 2/2. On-device proof is pending Ryan's install of
  MrGhosty v1.6.0 + accessibility grant — no phone was attached
  during this build.

## [0.4.0] — 2026-10-08

The collaboration wave: the hands join the bus, gain a second body,
and learn named policies.

- **GhostBus agent mode** — `ghost-hands bus-agent` registers as the
  agent `ghost-hands` (role: hands) on a GhostBus workspace (stdlib
  `BusClient`; single-workspace relay or hosted `/w/<id>/`, key from
  flag/env only, agent token memory-only). It claims tasks addressed
  to it or tagged `hands` — respecting 15-minute claim leases,
  `blockedBy`, and the bus's needs-approval gate (gated tasks are never
  claimed) — runs them through the unchanged Runner/Governor/Trail,
  posts progress comments, uploads the JSONL trail as a shared bus
  file, and completes with a structured report.
- **Approvals over the bus** — a Governor "ask" during a bus run posts
  `APPROVAL NEEDED: <class> <summary> — reply APPROVE <run-id> or DENY
  <run-id>` as a task comment + a message to the task creator, then
  waits on the bus's `/api/wait` long-poll. Deny/timeout denies. Both
  sides are `approval` trail events (`channel: bus`).
- **Body Protocol** — `docs/BODY_PROTOCOL.md`: the formal driver
  contract (perceive → ElementMap, `act` → outcome, capability flags),
  the shared action vocabulary, conformance rules, and the Android
  mapping table for MrGhosty's accessibility service (implementation
  future work; the contract and the rehearsal body are what's here).
- **SimPhoneDriver** — an in-memory Android-style body: home screen
  with app icons, Notes (type/save/persist, consequential delete-all),
  Settings toggles, Back/Home stack, and real PNG screenshots rendered
  stdlib-only (zlib/struct, 3×5 font). Driven unchanged by the existing
  Runner/Governor/deciders; conformance scenario passes on all three
  bodies (Fake, Chromium, SimPhone).
- **Policy packs** — `ghost_hands.policies`: `readonly` (writes +
  consequential denied outright), `standard` (the previous default),
  `strict` (writes ask too, 120s approval leash) — as data: per-class
  outcomes, approval timeout, allowlist additions. `--policy-pack` on
  `run`/`bus-agent`; MCP honors `GHOST_HANDS_POLICY`; unknown packs
  are a loud error.
- **Trail** — new `approval` event type for external-channel approvals.

Verified on this build: pytest 225 passed; offline bench 86/86; live
bench 2/2 (example.com, Wikipedia — one transient egress failure was
observed and passed on re-run, as in the 0.3 round). Bus mode proven
end to end against the **real** GhostBus node server in both shapes
(single-workspace relay and hosted `/w/<id>/`), including an approval
granted and an approval denied over the bus mid-run. Wheel 104,118 B,
sdist 413,246 B. **Not published** — local build; PyPI/GitHub still
serve 0.3.0 until a release is cut.

One cross-project finding from the bus work: GhostBus's file-backed
store writes through a single shared `.tmp` file whose rename can
collide under heavy concurrent writers (HTTP 400 ENOENT); the bus
demo and tests use the server's `:memory:` store. The fix belongs to
GhostBus.

## [0.3.0] — 2026-10-08

The capabilities wave: Playwright-class breadth on our own CDP stack,
with the governor and the trail on every new action.

- **AX eyes** — a second perception mode from the CDP accessibility tree
  (`Accessibility.getFullAXTree`), alongside the DOM map.
- **Shadow DOM piercing** — DOM perception walks the composed tree, so
  controls inside open shadow roots are visible and clickable.
- **Frames** — same-page iframes are perceived (frame-tagged) and acted on.
- **Dialogs** — JavaScript dialog policy (accept/dismiss), with every
  dialog and the handling decision written to the trail.
- **Downloads** — downloads complete to disk; result reports filename and
  byte size; classified `write`, recorded as a trail event.
- **Uploads** — `set_file` sets file inputs via `DOM.setFileInputFiles`;
  missing paths and directories are refused.
- **Network capture** — requests during a run land in the trail as `net`
  events (capped, truncation noted); `extract` mode `network` returns them.
- **fill_form** — one governed action fills a whole form by field
  descriptor; unresolvable fields are named in the result, and
  `submit: true` stays consequential and approval-gated.
- **Structured extract** — `list` → `[{text, href}]`, `table` → row dicts,
  alongside plain `text`.
- **Richer input** — hover, double-click, right-click, drag, key chords
  (e.g. `Control+a`).
- **PDF export** — `Page.printToPDF` to a file, byte size reported.
- **Device emulation** — desktop/mobile viewport presets; perception
  reflects the emulated layout.
- **Wait-for conditions** — wait for text, an element descriptor, or a URL
  substring, with honest timeout errors on the trail.
- **Raw coordinates** — `click_at(x, y)` for canvas/coordinate pages,
  marked `raw: true` in the trail.

Verified on this build: pytest 170 passed; offline bench 82/82; live
bench 2/2 (example.com, Wikipedia). Wheel 76,665 B, sdist 366,151 B.
Published to PyPI, GitHub, and GitHub Pages on release day.

## [0.2.0] — 2026-10-08

- **Live-web proven** — the Chromium driver drove real sites end to end:
  example.com link-following and a RuleDecider Wikipedia search landing
  on the "Oakdale, Tennessee" article (`ghost-hands bench --live`, 2/2).
- **Self-healing targets** — a stale target is re-found by its recorded
  descriptor, retried exactly once, and the heal is a trail event.
- **Tabs** — `open_tab` / `switch_tab` / `list_tabs` over CDP targets.
- **Session state** — `save_session` / `load_session` for cookies and the
  current origin's localStorage, in a versioned JSON format.
- **Screenshots to disk** — real PNGs via `Page.captureScreenshot`.
- **Proxy support** — `HTTPS_PROXY` honored, with a built-in local relay
  for credentialed proxies and opt-in `ignore_cert_errors`.
- **Decider wire proof** — the OpenAI-compatible decider's protocol and
  the full Runner loop proven against a local stub endpoint. Live-model
  driving quality explicitly still unproven.
- **Graduated scripts, executed** — replay export gained `driver=` /
  `start_url=`; the bench executes a graduated script on real Chromium.

Verified on this build: pytest 127 passed; offline bench 62/62; live
bench 2/2.

## [0.1.0] — 2026-10-08

First build. The governed hands, complete in one zero-dependency,
stdlib-only Python package:

- **Eyes** — numbered element maps parsed from page HTML (Jev-style cheap
  perception, character-budgeted).
- **Hands** — typed, JSON-serializable actions: navigate, click, type,
  press, select, scroll, extract, screenshot, wait, done.
- **Governor** — every action classified (readonly / write /
  consequential) and judged against a policy (per-class allow/ask/deny,
  domain allowlist) *before* it runs; consequential actions stop for
  approval, and an unapproved action never executes.
- **Trail** — JSONL provenance with record-before-execute and per-run
  accounting (steps, perception size).
- **Drivers** — `FakeDriver` (in-memory mini-web) and `ChromiumDriver`,
  our own CDP client over `--remote-debugging-pipe` with our own
  auto-wait. No Playwright, no Selenium.
- **Deciders** — scripted, deterministic rules (offline), and an
  OpenAI-compatible model decider (key from the environment only, never
  stored).
- **Runner** — perceive → decide → govern → record → act, with step
  budgets and stuck detection; it stops and reports instead of wandering.
- **Replay export** — a run's trail graduates into a deterministic,
  model-free Ghost Hands script.
- **MCP server** — stdlib stdio JSON-RPC: `hands_perceive`, `hands_act`,
  `hands_run`, `hands_trail`.
- **CLI** — `ghost-hands demo | bench | run | export | mcp`.

Verified on this build: pytest 101 passed; bench 48/48, including the
live Chrome smoke (perceive → type → click → observe) on Chrome for
Testing 154 through the pipe transport.
