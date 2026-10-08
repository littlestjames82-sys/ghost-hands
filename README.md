# Ghost Hands

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

> Status: v0.3.0 — **live on GitHub**:
> [github.com/littlestjames82-sys/ghost-hands](https://github.com/littlestjames82-sys/ghost-hands),
> site at [littlestjames82-sys.github.io/ghost-hands](https://littlestjames82-sys.github.io/ghost-hands/).
> The PyPI listing (`ghost-hands`) is being registered and lands at launch.

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
| **Bodies** (`drivers.py`) | `FakeDriver` (in-memory mini-web for tests/demos) and `ChromiumDriver` (real Chromium via our own CDP client: auto-wait, tabs, session save/load, screenshots to disk, downloads/uploads, network capture, dialog policy, viewport emulation, PDF, env-proxy support with a credential-injecting local relay, stale-target detection). |
| **Brains** (`deciders.py`) | `ScriptedDecider` (explicit steps), `RuleDecider` (deterministic offline rules — "go to…", "click…", "type… into…", "search for…"), `OpenAICompatibleDecider` (any OpenAI-compatible endpoint; key from env only, never stored; wire protocol proven against a local stub server). |
| **Runner** (`runner.py`) | The loop, with a step budget, stuck detection (same action 3×, or an unchanged map for 3 steps), and self-healing: a target that moved mid-flight is re-found by descriptor and retried once, heal logged. Honest stop reasons: `done` / `budget` / `denied` / `stuck` / `error`. Silence is never consent: an "ask" with no approver is a denial. |
| **Replay export** (`export.py`) | Graduate a trail into a standalone Ghost Hands script that replays the executed steps deterministically — no model, still governed. Chromium body, or a fake body with the mini-web embedded. |
| **MCP server** (`mcp_server.py`) | Stdlib-only stdio MCP: `hands_perceive`, `hands_act`, `hands_run`, `hands_trail`. Consequential actions are denied on this channel (no approver) with an explanation. |

## CLI

```bash
ghost-hands demo                                   # built-in fake-web demo
ghost-hands bench                                  # 82-case verification suite
ghost-hands bench --live                           # + live cases: example.com, Wikipedia
ghost-hands run --script steps.json --driver fake  # or --driver chromium
ghost-hands run --script steps.json --policy policy.json --approve-all
ghost-hands export trail.jsonl -o replay.py        # graduate a trail into a script
ghost-hands export trail.jsonl --driver fake --pages pages.json -o replay.py
ghost-hands mcp                                    # MCP server on stdio
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
| **Ghost Hands** | **Own CDP stack, zero dependencies — no Playwright, no Selenium under the hood** | Pluggable: scripted, deterministic rules, or any OpenAI-compatible model | **Classify + policy + approval before every action; record-before-execute** | **Full JSONL trail with per-run accounting** | **Yes — trails graduate into Ghost Hands scripts** | Web today; phone (Android via MrGhosty) on the roadmap |

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

What is proven, and what is not — as of v0.3.0 (Oct 8, 2026):

- **Proven:** the full offline suite (170 pytest tests, 82 bench cases);
  the Chromium driver on fixture pages (perceive / type / click / tabs /
  session roundtrip / screenshots / graduated-script execution) and the
  full v0.3 capability set on real Chromium (see the matrix note);
  live runs on example.com and Wikipedia (`ghost-hands bench --live`,
  2/2); the LLM decider's wire protocol against a local stub endpoint.
- **Unproven:** live-model driving quality (bring your own model; how
  well it drives is the model's business, and no benchmark is claimed);
  the Chromium driver against arbitrary third-party websites beyond the
  two live cases above; head-to-head speed vs any other tool (never
  measured, never claimed).

## Safety posture

- **No stealth.** Ghost Hands identifies honestly and never disguises
  automation. Governance is the product; evasion is not a feature.
- **No stored keys.** The OpenAI-compatible decider reads
  `GHOST_HANDS_API_KEY` / `GHOST_HANDS_BASE_URL` / `GHOST_HANDS_MODEL` from
  the environment only.
- **Denied means denied.** A blocked, denied, or unapproved action never
  executes — and the trail shows the verdict and the reason.

## Roadmap

- **Android body** — MrGhosty's accessibility service speaking the same
  action protocol: one hands, phone + web.
- **GhostBus transport** — hands as a bus agent other agents can task.
- **Policy packs** — Seatbelt/GhostGuard policy bundles; approvals routed
  over GhostBus or phone push.

## License

MIT — see `LICENSE`. © 2026 Ghost Developer Studio.
