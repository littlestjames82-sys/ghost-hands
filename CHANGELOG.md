# Changelog

All notable changes to Ghost Hands. Versions follow the releases on
[PyPI](https://pypi.org/project/ghost-hands/) and
[GitHub](https://github.com/littlestjames82-sys/ghost-hands/releases).
Every number below was measured on the build it describes — see the
README's "Honest status" for what remains unproven at each step.

## [Unreleased]

The revalidation release (v1.12 wave):

- **Pre-action revalidation + identity heal (P0-1).** The
  Chromium driver no longer trusts perception-time geometry
  at dispatch: ONE atomic in-page check (`__ghRevalidate`)
  confirms the target node is still connected, visible, not
  occluded, and still the element the decider saw (tag +
  name), with AX eyes revalidating by backend node id. The
  SAME node merely moved proceeds silently with recomputed
  geometry — no model call, no Runner round-trip — and the
  Runner records it as a `heal` trail event with
  `kind="revalidate"` and before/after boxes. A node that
  is gone raises the new distinct `StaleTargetError`.
  Perception now records multi-signal identity per element:
  backendNodeId, role, name, `nth` among same-named
  elements, visible text, stable attributes, and the
  bounding box. Runner healing re-resolves through the new
  containment-checked lookup (`find_heal_candidate`): every
  recorded signal must match, elements inside frames never
  heal, and ambiguity is a refusal, not a guess. The fake
  body learned the same lesson: two same-named elements are
  no longer interchangeable when their ids or hrefs differ.
- **Never-heal rule (P0-1, governance).** An action bearing
  a secret placeholder, or an upload (`set_file`), is never
  re-resolved to a different element — its approval was
  bound to the element identity the approver saw. Identity
  loss on such an action stops the run with the new
  `UnhealableTargetError` and a `kind="refused"` heal event,
  so the action can be re-approved against a fresh
  perception. Same-node geometry recompute is unaffected.
- **Wait-for-quiet (P0-2).** Before perceiving and before
  acting, the Runner asks the Chromium driver to let the
  page settle: no DOM mutation (an injected MutationObserver
  stamp) and no in-flight network request for 500 ms, hard
  cap 5 s. On cap expiry the run proceeds anyway and the
  trail and the decider carry a "possibly mid-transition"
  note. Disable per-run with `Runner(quiet_wait=False)` or
  globally with `GHOST_HANDS_QUIET=0`; a no-op on fake/sim
  drivers.
- **Snapshot-diff change notes (P0-3).** When a page change
  is small, the decider now also gets a line-level difflib
  diff of the rendered element map under the v1.4 summary
  note — a moved or renumbered element reads as an explicit
  -/+ pair instead of silently changing meaning — with the
  summary note and full map as the large-change fallback.
  Heal events carry the same change note for their step.
- **Effect feedback (v1.13 wave).** The rendered element map
  now carries each field's LIVE value: ` value="…"` (whitespace
  collapsed, truncated past 60 chars) for text-like inputs,
  textareas, and selects — a select shows its selected option's
  text — plus a bare ` checked` marker for ticked checkboxes and
  radios. Chromium perception reads the live DOM `value`
  property inside the existing single evaluate; the fake body
  overlays its field store at perceive time. This closes the
  loop the shifting-page-heal eval exposed: the heal landed the
  typing, but on a page that shows no visible change after Save
  the decider saw an identical map, re-verified, and flailed
  into the stuck stop. Now the effect is in the map — and
  because the map signature hashes the render, the stuck
  detector sees the change too. A password input's content is
  NEVER rendered and is redacted at the source in every body
  (HTML parser, fake overlay, the in-page descriptor walk,
  which also no longer names a password field by its value), so
  it cannot reach a model, a descriptor, or a trail through
  perception. `value` and `checked` are effect signals, not
  identity: both stay out of heal matching, so a heal still
  re-resolves a field whose value changed since perception.
  Sensitive values are masked at ingestion under the same
  discipline: a card number (13–19 digits, Luhn-checked), an
  SSN-shaped value, a field whose autocomplete carries a `cc-`
  token or `one-time-code`, a field whose name, id, placeholder,
  or label reads as a card / CVV / routing / IBAN / tax-id /
  OTP / token / API-key / secret field, and a long high-entropy
  token-shaped value enter the element only as
  `[masked · N chars]` — presence and length, never content,
  following the phone approver's redaction standard — so the
  raw string reaches no render, descriptor, or trail through
  perception in any body (HTML parser, fake overlay, Chromium
  DOM and AX perception). Ordinary working data (names,
  emails, addresses, note text) still renders in full, a bare
  short number is never masked by shape alone, and a masked
  value's length change still moves the map signature. An
  explicit `extract` action still reads the live page —
  extraction is an operator-intended read, and masking governs
  perception, not extraction. The policy is one knob,
  GHOST_HANDS_FIELD_VALUES: `masked` (default), `full` (raw
  values; passwords still never), or `off` (no field values
  stored at all, restoring pre-v1.13 perception).
- **Content visibility (v1.14 wave).** Every element map now
  also carries a page-text excerpt: the page's visible text,
  whitespace-collapsed, script/style content excluded, capped
  at 800 chars with a truncation marker, rendered in a
  `--- page text ---` section after the element lines (and in
  the goal-focused render, inside the budget the element lines
  leave). The graded multi-hop-fact task exposed the gap: its
  target page's map was a single "Back to guides" link while
  the answer sat in plain text the map never showed, so the
  model guessed extract modes blind. Collection folds into
  each body's existing perception path — the HTML parser for
  the fake body, the same single describe evaluate for
  Chromium's DOM eyes (`body.innerText`), and the AX tree's
  own StaticText nodes for AX eyes (one DOM text read when the
  tree carries none) — and because the excerpt rides the
  render, a text-only page change moves the map signature.
  The excerpt is page content, not a field value, so the
  v1.13 masking discipline does not apply to it. The stuck
  detector learned the companion lesson: its unchanged-map
  rule now counts steps that taught the run nothing — a step
  whose action result is a non-empty string never before seen
  in the run counts as progress and resets the counter, so
  exploration that pays off (an extract mode returning
  content after two empty modes) no longer races the stuck
  clock, while identical repeats and empty results still
  accumulate exactly as before.

## [1.11.0] — November 12, 2026

The honest-ruler release (v1.11 wave):

- **Eval integrity.** Two graded-suite defects found by
  running a real model (Muse Spark 1.3) and fixed. The
  form-fill checker now credits values entered through
  the `fill_form` action, not only individual `type`
  actions (a correct fill_form run had scored zero). And
  `login-trail-audit` was unpassable by construction:
  its goal printed the fixture password, and the trail
  records the goal, so the leak audit flagged every
  run, including the suite's own stub. The task now
  teaches the shipped secret-placeholder flow: the
  password is typed as `{{secret:PASSWORD}}`, the eval
  provisions the fixture secret itself, and the checker
  requires the real password to appear nowhere in the
  trail at all.


The never-give-up release (v1.10 wave):

- **Heal chains.** The self-healing retry that used to fire
  exactly once per step is now a chain: `heal_attempts`
  (default 2) re-perceives and retries a moved element at
  its fresh number, one `heal` trail event per attempt.
  `heal_attempts=1` reproduces the old behavior exactly;
  0 disables healing.
- **Error-to-loop recovery.** When a step fails terminally
  and `error_recovery_attempts` (default 1) remains, the
  run no longer dies: an `error_recovery` trail event
  records the error, the decider is told what failed and
  given a fresh perception, and the run continues.
  Governance denials and budget stops never recover.
  Runs report `heals` and `error_recoveries` counts.

The integration-surface release (v1.9 wave):

- **`ghost-hands serve`.** A stdlib-only HTTP JSON API: `GET
  /health`, `POST /run` (scripted steps under the same Governor;
  asks denied unless the server is started with `--approve-all`),
  and `POST /verify-trail`. Optional bearer auth via
  `GHOST_HANDS_SERVE_TOKEN`. Runs are serialized; the response
  carries the report plus the trail summary with its chain
  verdict.
- **Completion webhook.** When `GHOST_HANDS_WEBHOOK_URL` is set,
  finished runs (CLI `run`, `batch` roll-up, HTTP `/run`) POST
  their report JSON to it. A webhook failure is noted, never
  fatal — the trail remains the record.

The survive-the-network release (v1.8 wave):

- **Bounded retry with backoff.** Both HTTP deciders (OpenAI-
  compatible and Anthropic) now retry transient failures — 429,
  5xx, network errors — up to `GHOST_HANDS_RETRIES` times
  (default 2) with linear backoff (`GHOST_HANDS_RETRY_BACKOFF`).
  Non-retryable errors still fail immediately. Retries are
  counted in the decider's usage and land on the run report and
  the trail's stop event as `llm_retries`: what the network cost
  is part of the accounting, like tokens.
- **JSON mode (opt-in).** `GHOST_HANDS_JSON_MODE=1` makes the
  OpenAI-compatible decider request a strict JSON object reply
  (`response_format`), cutting parse failures on providers that
  support it. Off by default; some providers reject the parameter.
- **`batch --max-llm-tokens`** applies the per-run token budget
  cap to every script in a runbook.

The proof-connected release (v1.7 wave):

- **Chain-linked resume.** A resumed run's first trail event now
  records the parent trail's last hash (`parent_hash`), so a run
  and its continuation form one verifiable chain instead of two
  unrelated files. `load_resume_context` surfaces the hash; the
  CLI wires it automatically under `run --resume`.
- **Real usage in the eval scorecard.** Task scores carry the
  run's API-reported LLM tokens and call count; suite totals sum
  them, and the scorecard prints an "Actual model usage" line
  whenever a usage-reporting decider ran. Estimates stay labeled
  estimates; the model's own bill is now on the card.
- **Doctor knows the deciders.** A new `deciders` check reports
  which LLM deciders are configured (OpenAI-compatible, Anthropic,
  fallback endpoint, batch mode, MCP selection) from env presence
  only — names, never values.

The more-models, more-runs release (v1.6 wave):

- **Native Anthropic decider.** `AnthropicDecider` speaks the
  Messages API directly (no compatibility shim): same action
  dialect, batch mode, focused perception, and usage accounting
  (input/output tokens mapped onto the same report fields). Over
  MCP, `GHOST_HANDS_DECIDER=anthropic` selects it for goal-driven
  `hands_run`; it composes with the fallback decider like any
  other, so a Claude primary can fail over to an OpenAI-compatible
  endpoint or the reverse.
- **`ghost-hands batch`.** Run several scripts as one runbook:
  fresh body and trail per script, asks denied unless
  `--approve-all` (a batch never prompts), one line per script
  plus a roll-up with the total real LLM tokens. Exit 0 only when
  every script ends `done`; a broken script is reported, not
  fatal to the rest.

The prove-it-first release (v1.5 wave):

- **Verify can prove a negative.** The `verify` action gains
  `absent_text`: assert that text does NOT appear on the page —
  the error message is gone, the spinner disappeared — not only
  that the success signal showed up.
- **`ghost-hands check`.** Preflight a run script without
  executing anything: every step is validated, classified, and
  evaluated under the chosen policy (asks and denies named before
  they can surprise a live run), and every `{{secret:NAME}}`
  placeholder is confirmed present in the environment — names
  only, values never read. Exit 1 on a deny, an invalid step, or
  a missing secret.
- **Resume from a dead run's trail.** `run --resume <trail>`
  starts the new run where the old one stopped (the trail's stop
  event now records the driver's final URL, and the first perceive
  records the goal) and seeds the decider with a note covering
  what the previous run did and how it ended. The old trail stays
  untouched as that attempt's record.

The orientation-and-resilience release (v1.4 wave):

- **Page-change notes.** Between steps the Runner diffs the fresh
  element map against the previous one (navigation, elements
  appeared/gone by identity) and tells the decider what changed,
  in its history — orientation from the delta instead of
  re-deriving it from the full map every step.
- **Fallback decider.** `FallbackDecider(primary, fallback)`
  answers with the second decider when the primary raises (endpoint
  down, unparseable reply), counts failovers, and reports combined
  token usage so accounting and the budget cap stay true. Over MCP,
  `GHOST_HANDS_FALLBACK_BASE_URL` (plus optional key/model vars)
  wires a fallback endpoint into goal-driven `hands_run`.
- **`ghost-hands costs`.** Aggregates the API-reported token spend
  recorded in trail stop events across any number of trail files —
  per-run rows plus totals. Runs whose decider reported no usage
  are counted as unreported, never estimated into the totals.

The finish-the-job release (v1.3 wave):

- **Governed recovery.** A run that goes stuck (unchanged map or a
  repeated action) no longer has to die there: it gets one governed
  second chance by default (`recovery_attempts`) — stuck counters
  reset, any stale batch is dropped, the decider is told in its own
  history what stalled and to change approach, and a `recovery`
  event is written into the hash-chained trail. Exhausted chances
  end in the same honest stuck stop as before; `0` disables it.
- **LLM token budget cap.** `Runner(max_llm_tokens=...)`, the CLI's
  `--max-llm-tokens`, and `hands_run`'s `max_llm_tokens` stop a run
  with reason `budget` once API-reported spend reaches the cap —
  checked between steps, never mid-action, so the trail always
  closes cleanly. (Published failure analysis blames cost caps for
  most benchmark run deaths; here the cap is a governor, with the
  plan cache making the retry cheap.)
- **Goal-driven `hands_run`.** With `GHOST_HANDS_DECIDER=openai`,
  an MCP client passes a bare `goal` and the server's own
  OpenAI-compatible decider drives it under the same Governor,
  approvals, and trail — Ghost Hands as a complete agent over MCP,
  not only a step executor.
- **Publishable eval scorecard.** `ghost-hands eval --scorecard
  FILE.md` renders the graded suites as markdown with each decider
  reported separately and labeled for what it proves: stub =
  harness verification, rules = no-LLM floor, real model only when
  one actually ran. Never blended into a single headline number.

The measured-cost release: fewer model calls, real token numbers,
and success that must be proven when the operator says so.

- **Batched decisions, governed one by one.** An OpenAI-compatible
  decider built with `batch=True` (or `GHOST_HANDS_BATCH=1`) may
  return up to 5 actions from one model call via `decide_batch`.
  The Runner executes them one at a time through the unchanged
  loop — re-perceive, classify, approve, record, execute — so a
  batch is a cost optimization, never a governance bypass. A queued
  action whose target no longer resolves on the fresh page drops
  the rest of the batch and the decider is asked for a single fresh
  step. Batch-derived decisions are recorded with `source: "batch"`.
- **Real token accounting.** When the model API returns a `usage`
  object, the decider accumulates it and the Runner reports actual
  `llm_prompt_tokens` / `llm_completion_tokens` / `llm_calls` on the
  RunReport, in the trail's stop event, and as an "actual LLM
  tokens" card in the HTML report — next to (never replacing) the
  offline chars/4 estimate used when no usage is reported.
- **The require-verify gate.** `Runner(require_verify=True)`, the
  CLI's `--require-verify`, and `hands_run`'s `require_verify`
  argument reject a `done` claim until a verify action has passed
  in the same run; the rejection and its reason are recorded and
  shown to the decider, and three unproven dones end the run as an
  honest error.
- **Fifth MCP tool: `hands_verify_trail`.** Verifies any trail's
  hash chain (or the session's own) and returns the verdict —
  integrity checking is now a first-class tool, not just a CLI.

## [1.1.0] — 2026-11-11

- **Focused perception.** New `ElementMap.render_focused(goal)`:
  when a page's map exceeds the char budget, elements are scored by
  overlap between the goal's words and their name/tag/type/href and
  the goal-relevant ones are kept — still printed in page order under
  their original numbers, with an explicit "focused:" omission note.
  When the full map fits, the output is byte-identical to `render()`.
  The OpenAI-compatible decider now perceives through it, so the
  model stops paying tokens for nav menus while the action target
  sits past the old truncation cut.
- **Integrity everywhere.** The HTML audit report header now carries
  the trail's hash-chain verdict (intact with the chained-event
  count / legacy / CHAIN BROKEN at the first bad event), and the MCP
  `hands_trail` tool returns `trail_integrity` alongside the events.
- **Plan cache over MCP.** `hands_run` honors `GHOST_HANDS_CACHE`:
  proven plans store and replay through the full Governor with zero
  decider calls, same as the CLI's `--cache`.
- **Bench proof for the v1.0 features** (the bench is the project's
  public proof suite): five new cases — hash chain intact/forged,
  placeholder login with the value absent from the trail, verify
  passing on a real result and stopping a false claim, cache replay
  with zero decider calls, and the focused-render budget case.

## [1.0.0] — 2026-11-11

The trust-and-cost release. No new dependencies; the hard rule
stands (own stdlib CDP client, no Playwright/Selenium anywhere).
New regression suite: `tests/test_v1_features.py` (18 tests).

- **Tamper-evident trail.** Every event written by `Trail` now
  carries `prev` + `hash`: a SHA-256 chain over the canonical event
  and the previous event's hash (`"genesis"` for the first).
  Appending to an existing chained trail continues its chain.
  `verify_trail(events)` (exported from the package) and the new
  `ghost-hands verify-trail <trail.jsonl>` CLI (exit 0 intact /
  1 tampered / 2 legacy-or-unreadable) prove a record is untampered;
  editing, deleting, or reordering any event is detected at the
  first broken link, and a pre-1.0 trail is reported as legacy,
  never passed. New trail event type: `cache`.
- **Secret placeholders.** `{{secret:NAME}}` in a `type` text, a
  `select` value, or a `fill_form` field value resolves only at the
  execution boundary in the Runner and the MCP `hands_act` handler,
  from `GHOST_HANDS_SECRET_<NAME>` or a host-supplied in-memory
  `SecretProvider` mapping. Deciders, the Governor, approvers, the
  trail, exports, and the plan cache only ever hold the placeholder.
  Resolved values are scrubbed from driver results before recording.
  A missing secret stops the run with an honest error naming the
  missing name (never the value, which does not exist yet, and never
  a literal typing of the placeholder). The OpenAI-compatible
  decider's system prompt now teaches the placeholder rule.
- **The `verify` action.** New readonly action kind: assert `text`
  appears on the page, a `target` element exists, and/or the current
  URL contains `url` — every condition set must hold. Evaluated by
  the Runner (and MCP `hands_act`) against the live perception;
  drivers never receive it, like `done`. Failure is an `error` stop
  naming the failed condition, with the result recorded `ok: False`.
- **Governed plan cache.** `PlanCache` (`cache.py`): runs that end
  `done` store their executed steps (placeholder form + element
  descriptors) keyed by normalized goal + start URL, in an
  owner-only (0600) JSON file or in memory. A later identical run
  replays cached steps through the full loop — re-perceived,
  re-found by descriptor, re-governed, re-recorded — with no decider
  calls for cached steps (`decide` events carry `source: "cache"`;
  `cache` events record hit / fallback / store / hit-confirmed).
  A cached target that cannot be re-found records a fallback and
  the live decider takes over from that step. Runs ending any way
  other than `done` store nothing. CLI: `run --cache <file>`.
- **MCP server instructions, extended.** The server instructions
  shipped in 0.9.4 (perceive-first workflow, the approval rule, page
  content as untrusted data, session environment limits) now also
  carry the verify-before-done rule, the secret-placeholder rule,
  and the hash-chained trail. Regression test:
  `tests/test_mcp.py::test_initialize_instructions`.
- **Test fix (busagent hosted test).** `test_hosted_workspace_prefix_end_to_end`
  used a 9-character GhostBus admin key; the GhostBus hosted server
  now requires at least 16 characters and exited at boot, so the
  test timed out on health. The test key is now 25 characters.

## [0.9.4] — 2026-11-09 (directory-quality patch)

- **MCP server instructions.** The initialize result now publishes
  server instructions (`SERVER_INSTRUCTIONS` in `mcp_server.py`):
  the perceive-first workflow, the approval rule for consequential
  actions, the rule that page content is untrusted data, and the
  session environment limits. This fills the instructions section
  that Glama's schema page for ghost-hands recorded as empty.
  No tool, classification, or approval behaviour changes. New
  regression test: `tests/test_mcp.py::test_initialize_instructions`.
- **Fuller tool descriptions.** All four MCP tools and every
  parameter carry fuller descriptions written for directory
  tool-definition scoring: what each tool does, when to call it,
  and what it changes. Tool schemas, classification, and approval
  behaviour are unchanged.
- **Test fix (busagent hosted test).** `test_hosted_workspace_prefix_end_to_end`
  used a 9-character GhostBus admin key; the GhostBus hosted server
  now requires at least 16 characters and exited at boot, so the
  test timed out on health. The test key is now 25 characters.
- **Dependency bumps.** GitHub Actions dependencies updated via
  Dependabot (CI, CodeQL, and the publish workflow). The package
  itself remains stdlib-only, with no runtime dependencies.
- **README: per-client MCP setup.** The README gained a
  "Use it in your MCP client" section with copy-paste
  configuration for Claude Desktop, Claude Code, Cursor,
  VS Code, Cline, and Codex. Documentation only.

## [0.9.3] — 2026-11-04 (discoverability patch)

A metadata-only patch over 0.9.2 — no functional changes, no new
dependencies, governed-action semantics unchanged.

- **MCP Registry ownership marker.** The README now carries the
  `mcp-name: io.github.littlestjames82-sys/ghost-hands` line the
  official MCP Registry requires to verify ownership of a PyPI
  package, enabling the Ghost Hands listing there. The registry
  manifest (`server.json`) moves to 0.9.3 alongside.

## [0.9.2] — 2026-10-13 (metadata patch)

A metadata-only patch over 0.9.1 — no functional changes, no new
dependencies, governed-action semantics unchanged.

- **MCP tool annotation hints.** All four tools served by the MCP
  server (`hands_perceive`, `hands_act`, `hands_run`, `hands_trail`)
  now declare the full annotation set — `readOnlyHint`,
  `destructiveHint`, `idempotentHint`, `openWorldHint` — so MCP
  hosts can warn users before invoking a state-changing tool. The
  perceive/trail tools are marked read-only and idempotent; the
  act/run tools are marked destructive and non-idempotent. Added
  after the M8ven Trust Index flagged the missing hints in its
  listing review of ghost-hands. New regression test:
  `tests/test_mcp.py::test_tools_list_annotations`.

## [0.9.1] — 2026-10-11 (hardening patch; built + verified locally)

A security-hardening patch over 0.9.0 — no new features, no new
dependencies, governed-action semantics unchanged (classify → record
→ approve → execute; timeouts and errors still always deny). Full
audit detail, proofs, and cleared non-findings: `HARDENING_091.md`.
New regression suite: `tests/test_hardening_091.py`.

- **Replay-export injection (high).** `export.py` embedded element
  names/hrefs — page-controlled strings — raw into `#` comments in
  the graduated script. A newline in a name ended the comment and
  the rest of the string became executable Python in a script the
  user is meant to run. Descriptor comments now fold every control
  character to a space, so a comment is always one physical line.
- **Governor domain bypass (medium).** Domain allow/block lists
  were checked only against the page an action *started* on, never
  where it *went*: a click on a link to a blocked domain (or a
  submit into one) was allowed from an allowed page. `evaluate()`
  now also checks the target element's href (click/download kinds)
  and form action (click kinds), resolved against the page URL.
  Typing is unchanged — a field's form host is judged at submit.
- **Chromium `file://` navigation (high).** Any scheme could be
  navigated; a governed `navigate` to `file:///…` was readonly and
  allowed, and a following `extract` read the local file's text
  back to the decider. Chromium `open`/`open_tab` now refuse
  anything but http/https (plus `data:`/`about:` for fixtures),
  with browser-style whitespace stripping before the scheme check.
- **MCP server robustness (medium).** Valid-JSON-but-wrong-shape
  messages (a list, a string, non-object `params`/`arguments`,
  `pages: 123`) raised uncaught `AttributeError`/`TypeError` and
  killed the stdio server. They now get proper JSON-RPC errors
  (-32600/-32602), tool arguments are type-validated, single lines
  are capped at 16 MiB, and an unexpected handler error returns a
  generic -32603 instead of a traceback — the loop always survives.
- **BusClient validation + caps (medium).** Any urllib scheme was
  accepted as a bus URL (`file://` turned bus calls into local
  file reads) and a workspace id like `../../admin` rewrote the
  request path out of its workspace. Base URLs must now be
  http(s) with a host; workspace ids must be slugs. Response
  bodies are capped (16 MiB; error bodies 256 KiB).
- **Response caps everywhere (low).** The Android bridge client,
  PhoneApprover, the OpenAI-compatible decider, and doctor's
  probes all read response bodies unbounded; each now has a cap
  sized to its largest legitimate payload, and the approver still
  fails closed (oversize = denial, never consent).
- **Download filename traversal (low).** A page-controlled CDP
  `suggestedFilename` containing `../` was used verbatim to resolve
  the downloaded file, so a "completed download" could name a
  pre-existing file outside the download directory. Filenames are
  reduced to their bare name at ingestion.
- **Credential-file permissions (low).** Trail files (which record
  typed text verbatim) and `save_session` files (cookies +
  localStorage) were created with the process umask — group/world
  readable on typical setups. Both are now created 0600, and a
  pre-existing loose trail/session file is tightened on use.

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
