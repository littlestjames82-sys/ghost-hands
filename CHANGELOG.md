# Changelog

All notable changes to Ghost Hands. Versions follow the releases on
[PyPI](https://pypi.org/project/ghost-hands/) and
[GitHub](https://github.com/littlestjames82-sys/ghost-hands/releases).
Every number below was measured on the build it describes — see the
README's "Honest status" for what remains unproven at each step.

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
