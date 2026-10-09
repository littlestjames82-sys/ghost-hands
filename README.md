# Ghost Hands

[![PyPI](https://img.shields.io/pypi/v/ghost-hands)](https://pypi.org/project/ghost-hands/)
[![Downloads](https://img.shields.io/pypi/dm/ghost-hands)](https://pypi.org/project/ghost-hands/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/littlestjames82-sys/ghost-hands)](https://github.com/littlestjames82-sys/ghost-hands/releases/tag/v0.3.0)
[![CI](https://github.com/littlestjames82-sys/ghost-hands/actions/workflows/ci.yml/badge.svg)](https://github.com/littlestjames82-sys/ghost-hands/actions/workflows/ci.yml)

**Playwright drives. Stagehand thinks. Browser Use wanders. Ghost Hands answers for every move.**

Ghost Hands is a standalone, zero-dependency Python package that gives an
agent *hands* on the web — and a conscience to go with them. Every action an
agent takes is classified before it runs (`readonly` / `write` /
`consequential`), judged against a policy, written to a provenance trail
*before* it executes, and — when it matters — gated on a human approval.
Finished runs graduate into deterministic, model-free Ghost Hands scripts
you can re-run for free.

The browser body is **our own**: a stdlib Chrome DevTools Protocol client
drives Chromium directly over the `--remote-debugging-pipe` transport.
There is no Playwright, no Selenium, and no other automation library under
the hood — the automation layer is 100% ours. (We drive Chromium, the
browser; we did not write a browser engine and don't claim to have.)

From [Ghost Developer Studio](https://github.com/littlestjames82-sys).
MIT licensed.

> Status: v0.6.0 — built and verified locally (approvals by phone:
> consequential asks can be decided on Ryan's phone through the
> MrGhosty v1.7.0 bridge); PyPI and
> GitHub serve v0.3.0 until the next release ships:
> [PyPI `ghost-hands`](https://pypi.org/project/ghost-hands/)
> (`pip install ghost-hands`) ·
> [github.com/littlestjames82-sys/ghost-hands](https://github.com/littlestjames82-sys/ghost-hands) ·
> [littlestjames82-sys.github.io/ghost-hands](https://littlestjames82-sys.github.io/ghost-hands/).

## What's new in 0.6.0

- **Approvals by phone.** A run's "ask" decisions can be routed to
  Ryan's phone: `PhoneApprover` (`phone_approver.py`) posts a
  redacted approval request to the Ghost Hands bridge in MrGhosty
  v1.7.0, which raises a high-importance notification ("Ghost Hands
  needs approval") with **Approve** / **Deny** actions, mirrored by a
  card in MrGhosty's Device tab. The decision returns over the
  bridge; denied, expired, timed-out, and unreachable all mean **no**
  — silence is never consent. Works with every driver
  (`ghost-hands run --approver phone …` — the phone can approve a
  desktop Chromium run too), and in bus-agent mode via
  `--approval-channel phone` (the bus gets a courtesy comment; the
  approval itself is never requested from the bus). Approvals route
  to Ryan's phone through the MrGhosty bridge (notification +
  in-app decision) when the bridge is reachable; it is a local
  bridge channel, not a cloud push service. See
  [docs/APPROVALS.md](docs/APPROVALS.md).
- **Decisions are phone-only, by construction.** The bridge API can
  create and read approval requests but has **no decision
  endpoint** — a token holder can never approve their own request.
  The only deciders are the notification actions and the in-app
  card, on the phone itself.
- **Summaries are redacted before they leave the machine.** A
  request carries the action kind, classification, target
  descriptor, and current URL/package; `type` actions show the
  target and a character count, never the typed text; `fill_form`
  shows field names, never values. The pairing token never appears
  in summaries, trail events, errors, or logs.
- **`ghost-hands phone-approval-test`** — sends one harmless test
  request ("approving this runs nothing") and waits for the phone
  decision: the one-command on-device proof.

## What's new in 0.5.0

- **The real Android body.** `AndroidDriver` (`android_driver.py`)
  drives a real phone through the **Ghost Hands bridge** in the
  MrGhosty Android app (v1.6.0): a loopback-only HTTP server hosted by
  MrGhosty's accessibility service on `127.0.0.1:8378`, authenticated
  by a pairing token (shown in MrGhosty's Device tab; passed via
  `GHOST_HANDS_ANDROID_TOKEN`, never stored). Perception walks the
  live accessibility tree into the same numbered ElementMap (each
  element keeps its bridge `ref` as `body_ref`); actions re-verify
  the target's {tag, role, name} key at the bridge before anything
  moves, and stale targets come back as the same "target missing /
  target mismatch" errors the Runner heals from on the web. The
  unchanged Runner/Governor drive it — governance is identical,
  including the consequential gate. Web-only actions fail honestly.
  Forward the bridge with `adb forward tcp:8378 tcp:8378`, check it
  with `ghost-hands android-status`, then
  `ghost-hands run --driver android …` (or task it over GhostBus with
  `"driver": "android"`). The driver and the bridge are built and the
  full flow is proven against the bridge contract (fake-bridge
  conformance suite + bench case); **on-device proof is pending
  Ryan's install of MrGhosty v1.6.0 + the accessibility grant**.
- **Element `body_ref`.** Elements now carry an optional opaque
  body-native target reference in their descriptor, so trails record
  exactly which node a phone action touched.

## What's new in 0.4.0

- **GhostBus agent mode.** `ghost-hands bus-agent` puts the hands on
  [GhostBus](https://github.com/littlestjames82-sys/ghostbus) as the
  agent `ghost-hands` (role: hands). It claims tasks addressed to it or
  tagged `hands` — respecting claim leases, `blockedBy`, and the bus's
  own needs-approval gate (gated tasks are never claimed) — runs them
  through the same Runner/Governor/Trail, posts progress comments,
  uploads the JSONL trail as a shared bus file, and completes with a
  structured report. Works against the single-workspace relay and
  hosted workspaces (`--workspace`, `/w/<id>/`), key from flag/env only,
  never stored. `ghost-hands bus-demo` proves the whole loop against
  the **real** GhostBus node server — including a consequential submit
  approved by a second client over the bus, mid-run.
- **Approvals over the bus.** When the Governor says "ask" during a bus
  run, the approver posts `APPROVAL NEEDED: <class> <summary> — reply
  APPROVE <run-id> or DENY <run-id>` as a task comment and a message to
  the task's creator, then waits (long-poll) up to the approval
  timeout. Deny or timeout = denied, on the record: both the request
  and the decision land in the trail as `approval` events.
- **The Body Protocol.** [docs/BODY_PROTOCOL.md](docs/BODY_PROTOCOL.md)
  formalizes the driver contract — perceive → numbered ElementMap,
  `act(action)` → outcome, optional capability flags — and the shared
  action vocabulary, so new bodies can be built against a written spec.
  A conformance scenario (same abstract script on every body) proves
  it: the destructive step is denied with no approver and lands with
  one, and the trail records identical shapes, on FakeDriver,
  ChromiumDriver, and the new phone body alike.
- **SimPhoneDriver.** A simulated Android-style body (in-memory):
  home screen with app icons, a Notes app (type, save, notes persist;
  a "Delete all notes" button that classifies consequential), a
  Settings app with toggles, a Back/Home navigation stack, and real
  PNG screenshots rendered with stdlib only. The existing
  Runner/Governor/deciders drive it unchanged — it is the rehearsal
  body for the real Android body — which has since landed (v0.5.0,
  above): the same Runner/Governor drive SimPhoneDriver in tests and
  a real phone through AndroidDriver.
- **Policy packs.** Named, loadable governor bundles
  (`ghost_hands.policies`): `readonly` (writes + consequential denied
  outright), `standard` (the default posture), `strict` (writes ask
  too; approvals on a 120s leash). `--policy-pack` on `run` and
  `bus-agent`; the MCP server honors `GHOST_HANDS_POLICY`. Unknown
  pack names are a loud error, never a silent fallback.

## What's new in 0.3.0

- **Two kinds of eyes.** DOM eyes now walk the composed tree — open
  shadow roots pierced, same-page iframes included and tagged — so web
  components and framed controls are numbered like everything else.
  Or switch to **AX eyes** (`eyes="ax"`): the map is built from the CDP
  Accessibility tree (role + name + states) and actions resolve back
  through `backendNodeId`.
- **Forms in one move.** `fill_form` fills a whole form from
  `{label: value}` pairs, resolving each field by label, name,
  placeholder, or aria-label, and reports per-field results — failures
  named, never silent. With `submit: true` it is classified
  consequential and takes the normal approval path.
- **Files both ways.** `download` clicks through and waits for the file
  to complete on disk (filename + byte size reported, completion on the
  trail); `set_file` uploads a local file into a file input
  (`DOM.setFileInputFiles`), refusing missing paths and directories.
- **Network on the record.** The Network domain is captured during
  runs (method, URL, status, type) into `net` trail events, capped at
  200 per run with truncation noted; `extract` mode `network` returns
  the captured list.
- **Structured extract.** `extract` modes: `text` (default), `list`
  (links → `[{text, href}]`), `table` (→ list of row dicts), `network`.
- **Dialogs handled, on the record.** `confirm`/`alert`/`prompt` are
  answered per the driver policy (`dialog_policy="dismiss"|"accept"`,
  default dismiss) and every dialog + decision lands on the trail.
- **Richer input.** `hover`, `double_click`, `right_click`, `drag`
  (target → target), key chords in `press` (`"Control+a"`), and
  `click_at(x, y)` — the raw-coordinate fallback, classified `write`
  and marked `raw: true` on the trail.
- **PDF + viewport.** `pdf` prints the page to a real PDF file
  (`Page.printToPDF`); `set_viewport` switches named presets
  (desktop 1280×800, mobile 390×844 + touch) and perception reflects
  the emulated layout, media queries included.
- **Wait-for conditions.** `wait_for` waits for text to appear, an
  element matching a descriptor, or a URL substring — with a timeout
  that fails honestly, on the record.
- All new actions work through the CLI runner and MCP `hands_act`
  unchanged, and every one of them is still classified by the governor
  and written to the trail. (HTML-snapshot eyes do not pierce shadow
  roots or frames — that is what the live-element map is for.)

## What's new in 0.2.0

- **Live-web proven.** The Chromium driver has now driven real sites end
  to end: example.com (followed the IANA link to the Example Domains
  page) and a real Wikipedia search — the deterministic RuleDecider typed
  "Oakdale, Tennessee", submitted, and landed on the Oakdale article.
  Reproduce with `ghost-hands bench --live` (2 cases, verified Oct 8,
  2026 from the studio sandbox).
- **Self-healing targets.** If the page mutates between perception and
  action, the stale target is detected, re-found by its recorded
  descriptor, retried exactly once, and the heal is written to the trail
  as its own event.
- **Tabs.** `open_tab` / `switch_tab` / `list_tabs` — each tab is its own
  CDP target; perception and actions apply to the active tab.
- **Session state.** `save_session` / `load_session`: cookies
  (Network.getAllCookies / setCookie) plus the current origin's
  localStorage, in a small versioned JSON format.
- **Screenshots to disk.** The screenshot action now writes a real PNG
  (`Page.captureScreenshot`) to the action's `path`.
- **Proxy support.** The driver honors `HTTPS_PROXY` / `https_proxy`
  (opt out with `proxy=""`). Authenticated proxies work via a built-in
  local relay that injects the credentials Chrome itself can't accept;
  `ignore_cert_errors=True` is available for TLS-intercepting proxies.
- **Decider wire proof.** The OpenAI-compatible decider's protocol —
  request shape, env gating, reply parsing, the full Runner loop — is
  covered by tests and a bench case against a local stub
  `/chat/completions` server. Live-model driving quality remains
  unproven (see "Honest status").
- **Graduated scripts, executed.** Replay export now takes `driver=`
  (chromium or fake-with-embedded-pages) and `start_url=`; the bench
  actually *executes* a graduated script against fixture pages on real
  Chromium and checks its completion marker.

## Quickstart

```bash
pip install ghost-hands        # (once published; for now: pip install . from a checkout)
ghost-hands demo               # scripted run over a built-in fake mini-web — no browser needed
ghost-hands bench              # the verification suite
```

Drive a real page:

```python
from ghost_hands import ChromiumDriver, Runner, ScriptedDecider

driver = ChromiumDriver()  # finds Chromium/Chrome; $GHOST_HANDS_CHROME overrides
runner = Runner(driver, ScriptedDecider([
    {"kind": "navigate", "url": "https://example.com"},
    {"kind": "extract"},
    {"kind": "done", "summary": "looked at the page"},
]))
report = runner.run("look at example.com")
print(report.stop_reason, report.summary)
driver.close()
```

## How it works

| Layer | What it does |
|---|---|
| **Eyes** (`eyes.py`) | Numbered **ElementMap** — `[3] <button> "Sign in"` — compact text with a character budget. No screenshots, no vision model. Two sources: an HTML snapshot parse (offline body), or the live browser — a composed-tree DOM walk (shadow roots + iframes) or the CDP Accessibility tree. |
| **Hands** (`actions.py`) | Typed, JSON-serializable actions: navigate, click, type, press (with key chords), select, scroll, hover, double/right click, drag, click_at (raw coordinates), fill_form, set_file, download, extract (text/list/table/network), screenshot (to a real PNG file), pdf, set_viewport, wait / wait_for, tabs, session save/load, done. Targets are element numbers, never raw selectors. |
| **Governor** (`governor.py`) | Classifies every action *before* execution and applies a policy: allow / ask / deny per class, a domain allowlist, blocked domains. Form submits and anything smelling like pay / send / delete / publish / transfer is **consequential**. |
| **Trail** (`trail.py`) | JSONL provenance: perceive → decide → govern → execute → result → stop (plus `heal`, `dialog`, `net`, and `download` events), with per-run accounting (steps, perception chars, ~tokens at chars/4, actions by class). Govern + execute are recorded **before** the action runs. |
| **Bodies** (`drivers.py`, `simphone.py`, `android_driver.py`) | `FakeDriver` (in-memory mini-web for tests/demos), `ChromiumDriver` (real Chromium via our own CDP client: auto-wait, tabs, session save/load, screenshots to disk, downloads/uploads, network capture, dialog policy, viewport emulation, PDF, env-proxy support with a credential-injecting local relay, stale-target detection), `SimPhoneDriver` (simulated Android-style phone: apps, notes, toggles, Back/Home, rendered PNG screenshots), and `AndroidDriver` (a real phone via the MrGhosty app's Ghost Hands bridge: loopback HTTP/JSON, pairing-token auth, accessibility-tree perception with `body_ref` targets, key-verified actions, real screenshots on API 30+, loopback-only by construction). The contract every body signs is [docs/BODY_PROTOCOL.md](docs/BODY_PROTOCOL.md). |
| **Brains** (`deciders.py`) | `ScriptedDecider` (explicit steps), `RuleDecider` (deterministic offline rules — "go to…", "click…", "type… into…", "search for…"), `OpenAICompatibleDecider` (any OpenAI-compatible endpoint; key from env only, never stored; wire protocol proven against a local stub server). |
| **Runner** (`runner.py`) | The loop, with a step budget, stuck detection (same action 3×, or an unchanged map for 3 steps), and self-healing: a target that moved mid-flight is re-found by descriptor and retried once, heal logged. Honest stop reasons: `done` / `budget` / `denied` / `stuck` / `error`. Silence is never consent: an "ask" with no approver is a denial. |
| **Replay export** (`export.py`) | Graduate a trail into a standalone Ghost Hands script that replays the executed steps deterministically — no model, still governed. Chromium body, or a fake body with the mini-web embedded. |
| **Policy packs** (`policies.py`) | Named governor bundles as data: `readonly` (perception only — writes and consequential denied outright), `standard` (the default), `strict` (writes ask too, 120s approval leash). Each pack sets per-class allow/ask/deny, an approval timeout, and allowlist additions. |
| **Bus mode** (`busclient.py`, `busagent.py`) | A stdlib GhostBus REST client (single-workspace relay or hosted `/w/<id>/`; key + agent token from flag/env, memory only) and the agent loop on top: register as `ghost-hands`, claim addressed/tagged tasks (leases, `blockedBy`, and the bus's needs-approval gate respected), run them governed, route approvals back over the bus, upload the trail as a shared file, complete with a structured report. |
| **MCP server** (`mcp_server.py`) | Stdlib-only stdio MCP: `hands_perceive`, `hands_act`, `hands_run`, `hands_trail`. Consequential actions are denied on this channel (no approver) with an explanation. Honors the `GHOST_HANDS_POLICY` env var (a policy pack name). |

## CLI

```bash
ghost-hands demo                                   # built-in fake-web demo
ghost-hands bench                                  # 87-case verification suite
ghost-hands bench --live                           # + live cases: example.com, Wikipedia
ghost-hands run --script steps.json --driver fake  # or --driver chromium
adb forward tcp:8378 tcp:8378                      # forward the phone's Ghost Hands bridge (USB)
ghost-hands android-status                         # is MrGhosty's bridge answering? (token via env)
ghost-hands run --script steps.json --driver android   # drive the real phone, governed
ghost-hands run --script steps.json --policy policy.json --approve-all
ghost-hands run --script steps.json --policy-pack strict   # or readonly
ghost-hands export trail.jsonl -o replay.py        # graduate a trail into a script
ghost-hands export trail.jsonl --driver fake --pages pages.json -o replay.py
ghost-hands mcp                                    # MCP server on stdio
ghost-hands bus-agent --once                       # work one GhostBus task, then exit
ghost-hands bus-agent --workspace studio           # hosted bus, /w/studio/ (key via GHOSTBUS_KEY)
ghost-hands bus-demo                               # prove bus mode vs the real GhostBus server
```

See `examples/steps.json` and `examples/policy.json` for the file formats.

## How it compares

| | Driver stack | Intent layer | Governance / approval gate | Provenance trail | Run → script replay | Bodies |
|---|---|---|---|---|---|---|
| **Playwright** | Own protocol drivers for Chromium/Firefox/WebKit | None — deterministic scripts | None | Trace viewer for tests | Codegen records scripts | Web |
| **Playwright MCP** | Playwright via MCP (23+ raw tools) | The calling model, unmediated | None | None built in | No | Web |
| **Stagehand** | Playwright + AI primitives (act/observe/extract/agent) | Per-step LLM calls | None | Session logs | Action caching | Web (cloud browsers upsell) |
| **Browser Use** | Own browser agent loop | Full autonomous LLM loop | None | Run history | No | Web |
| **Jev** | Numbered control list, cheap decision pass | Single cheap pass per step | None (we built Agent Seatbelt for it) | Minimal | No | Web |
| **Ghost Hands** | **Own CDP stack, zero dependencies — no Playwright, no Selenium under the hood** | Pluggable: scripted, deterministic rules, or any OpenAI-compatible model | **Classify + policy + approval before every action; record-before-execute** | **Full JSONL trail with per-run accounting** | **Yes — trails graduate into Ghost Hands scripts** | Web (Chromium) + a real Android body (MrGhosty bridge) + a simulated phone body, under a written Body Protocol |

### Capabilities, feature by feature

Surveyed from each project's public docs (Oct 2026); "—" means not a
built-in of the tool as documented. Every Ghost Hands row is proven by
a named bench case on real Chromium unless marked (fake) — the bench
says which body each case ran on.

| Capability | Playwright | Stagehand | Browser Use | Ghost Hands |
|---|---|---|---|---|
| Numbered element map for a model | Via MCP / snapshots | observe | Yes (own format) | **Yes — DOM or Accessibility-tree eyes** |
| Shadow DOM controls | Yes | Yes | Partial | **Yes — open roots pierced (live map)** |
| Iframe controls | Yes | Yes | Partial | **Yes — same-page frames, tagged** |
| JS dialogs (confirm/alert) | Yes | Yes | Yes | **Yes — policy-driven, decision on the trail** |
| Downloads | Yes | Yes | Yes | **Yes — completion + size on the trail** |
| File upload | Yes | Yes | Yes | **Yes — `set_file`** |
| Network capture | Yes (HAR/request APIs) | Via Playwright | Limited | **Yes — `net` trail events + extract mode** |
| Fill a whole form in one step | No (per-field API) | act per field | Agent loop | **Yes — `fill_form`, per-field results** |
| Structured extract (list/table) | Manual locators | extract (LLM schema) | Agent loop | **Yes — deterministic, no model call** |
| Hover / double / right click / drag | Yes | Via act | Yes | **Yes** |
| Key chords (Control+a) | Yes | Via act | Yes | **Yes** |
| Raw coordinate clicks | Yes | — | Yes | **Yes — classified `write`, marked raw** |
| PDF export | Yes (Chromium) | Via Playwright | — | **Yes** |
| Device/viewport emulation | Yes | Via Playwright | Viewport opts | **Yes — named presets, perception follows** |
| Wait for text/element/URL | Yes | Via act | Agent loop | **Yes — honest timeout on the record** |
| **Every action classified + approval-gated** | **No** | **No** | **No** | **Yes — the whole point** |
| **Run graduates into a replayable script** | Codegen (records new) | Action cache | No | **Yes — from the trail** |

## Honest status

What is proven, and what is not — as of v0.6.0 (Oct 8, 2026):

- **Proven:** the full offline suite (299 pytest tests, 88 bench cases);
  the Chromium driver on fixture pages (perceive / type / click / tabs /
  session roundtrip / screenshots / graduated-script execution) and the
  full v0.3 capability set on real Chromium (see the matrix note);
  live runs on example.com and Wikipedia (`ghost-hands bench --live`,
  2/2); the LLM decider's wire protocol against a local stub endpoint;
  **bus agent mode against the real GhostBus node server** — both the
  single-workspace relay and a hosted `/w/<id>/` workspace — including
  an approval granted and an approval denied over the bus mid-run;
  the Body Protocol conformance scenario on all three in-process
  bodies; the sim phone's notes flow end to end; **the Android body
  against the bridge contract** — AndroidDriver's full governed flow
  (including heal-after-mutation and the consequential delete gate)
  against a fake bridge speaking the exact wire protocol of the real
  one, plus the MrGhosty v1.6.0 APK built and signature-verified with
  the bridge compiled in; **approvals by phone against the same
  contract** — PhoneApprover's full request/poll flow (approved /
  denied / expired / timeout / unreachable / 401), payload redaction
  proven on the wire, a consequential action approved and denied by
  simulated phone decisions mid-run on both the Android and web
  bodies, the bus agent's phone channel against the real GhostBus
  server, and the MrGhosty v1.7.0 APK built and signature-verified
  with the approval store, receiver, and bridge endpoints compiled
  in (approval-store logic JVM-tested, 44 checks).
- **Unproven:** live-model driving quality (bring your own model; how
  well it drives is the model's business, and no benchmark is claimed);
  the Chromium driver against arbitrary third-party websites beyond the
  two live cases above; head-to-head speed vs any other tool (never
  measured, never claimed); bus mode against a *deployed* hosted
  GhostBus (proven against the real server booted locally, in both
  shapes); **the Android body and the phone-approval flow on
  physical hardware — driver + MrGhosty bridge + approval UX are
  built, but no phone was attached during the v0.5/v0.6 builds;
  on-device proof waits on Ryan's install of MrGhosty v1.7.0 and his
  accessibility grant (then: `adb forward tcp:8378 tcp:8378` and
  `ghost-hands phone-approval-test`).** One real finding
  from the bus work: GhostBus's file-backed store writes through a
  single shared `.tmp` file, and heavy concurrent writers can collide
  on its rename (HTTP 400) — the bus demo uses the server's `:memory:`
  store for that reason; a file-store fix belongs to GhostBus.

## Safety posture

- **No stealth.** Ghost Hands identifies honestly and never disguises
  automation. Governance is the product; evasion is not a feature.
- **No stored keys.** The OpenAI-compatible decider reads
  `GHOST_HANDS_API_KEY` / `GHOST_HANDS_BASE_URL` / `GHOST_HANDS_MODEL` from
  the environment only.
- **Denied means denied.** A blocked, denied, or unapproved action never
  executes — and the trail shows the verdict and the reason.

## Roadmap

- [x] **GhostBus transport** — hands as a bus agent other agents can
  task, approvals routed over the bus (v0.4.0).
- [x] **Policy packs** — named governor bundles: readonly / standard /
  strict (v0.4.0). Seatbelt/GhostGuard-flavored packs can follow the
  same data format.
- [x] **Body Protocol** — the written driver contract + a simulated
  phone body proving it (v0.4.0).
- [x] **Android body** — driver + MrGhosty bridge BUILT (v0.5.0):
  MrGhosty's accessibility service implements the Body Protocol
  behind a loopback, pairing-token bridge, and `AndroidDriver` speaks
  to it; on-device proof pending Ryan's install + accessibility
  grant. One hands, phone + web.
- [x] **Approvals by phone push** — DONE (v0.6.0), with one precise
  caveat about the word "push": approvals route to Ryan's phone
  through the MrGhosty bridge (notification + in-app decision) when
  the bridge is reachable; it is a local bridge channel, not a
  cloud push service. The bridge API has no decision endpoint —
  decisions happen only on the phone itself.

## Repository map

**Links:** [PyPI](https://pypi.org/project/ghost-hands/) ·
[Site](https://littlestjames82-sys.github.io/ghost-hands/) ·
[Release v0.3.0](https://github.com/littlestjames82-sys/ghost-hands/releases/tag/v0.3.0) ·
[CHANGELOG.md](CHANGELOG.md) · [LAUNCH.md](LAUNCH.md)

- `src/ghost_hands/` — the package, one module per layer: `eyes`
  (perception), `actions`, `governor`, `policies` (policy packs),
  `trail`, `drivers` (FakeDriver + our CDP ChromiumDriver), `simphone`
  (the simulated phone body), `android_driver` (the real Android body
  via the MrGhosty bridge), `phone_approver` (approvals decided on
  Ryan's phone), `deciders`, `runner`, `export`,
  `busclient` + `busagent` (GhostBus mode), `mcp_server`, `cli`,
  `bench`.
- `docs/BODY_PROTOCOL.md` — the body contract, capability flags,
  conformance rules, and the Android mapping table.
- `docs/APPROVALS.md` — the three approval channels (prompt, bus,
  phone), the no-API-decision rule, and the summary redaction rule.
- `tests/` — the pytest suite (299 tests at v0.6.0).
- Bench — `ghost-hands bench` (offline, 88 cases at v0.6.0) and
  `ghost-hands bench --live` (opt-in real-web cases); bench code lives
  in `src/ghost_hands/bench.py`.
- `examples/` — sample steps and policy files for `ghost-hands run`.
- `site/` — the project site source, deployed to GitHub Pages by
  `.github/workflows/pages.yml`.
- `LAUNCH.md` — the launch runbook and copy pack, as written at launch.
- `PLAN.md` — the field survey and design notes the build started from.

## License

MIT — see `LICENSE`. © 2026 Ghost Developer Studio.
