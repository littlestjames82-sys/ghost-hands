# Ghost Hands — standalone agent hands (Ghost Developer Studio)

Ryan's directive, Oct 8, 2026: find Playwright and tools like it, ship Ghost Hands as a
standalone tool like them — but better. Ghost Hands is also Ryan's name for MrGhosty's
hands layer (confirmed Oct 8); this package is the standalone, public form of those hands.

## The field (surveyed Oct 8, 2026)
- **Playwright** (Microsoft, Apache-2.0): standard scripted browser driver — Chromium/
  Firefox/WebKit, auto-wait, trace viewer, codegen, test runner. Deterministic; no intent
  layer, no governance. **Playwright MCP** (@playwright/mcp): 23+ raw browser tools over
  accessibility snapshots — a thin wrapper; the agent gets hands with no judgment.
- **Puppeteer**: Chrome/CDP only, scripted. **Selenium/WebDriver**: universal, older,
  slower. **Cypress**: test-focused, JS-only, parallel is paid.
- **Stagehand** (Browserbase, MIT, ~24k stars): Playwright + AI primitives
  act/observe/extract/agent, per-step model choice, action caching. Funnels to paid
  Browserbase cloud browsers; LLM cost per action.
- **Browser Use** (MIT, ~112k stars): full autonomous agent loop; least deterministic,
  hardest to debug. **Jev** (browser-use team, in our shop): page as numbered control
  list, one cheap decision pass per step; MVP, no approval gate (we built Seatbelt for it).
- **Computer Use** (vision/screenshots): universal, expensive, fails silently.
- **affirmitv/ghosthands** (MIT, 56 stars): USB-HID hardware hands + vision eyes,
  pitched as "undetectable". Different animal: hardware + stealth. Ours is software +
  accountability. Name note: PyPI `ghost-hands` and `ghosthands` both unregistered
  (checked Oct 8, 2026 — 404); our GitHub repo will be littlestjames82-sys/ghost-hands.

## The gap (our wedge)
No tool in the field governs the hands: any of them will submit, pay, send, or delete
the moment a model says so; none keep a provenance trail you can audit or replay;
agent runs can't graduate into free deterministic scripts; and each tool is one body
(web OR desktop OR phone).

## Ghost Hands v0.1 — design
Python, zero-dependency core, hatchling, MIT. Dist name `ghost-hands`, import
`ghost_hands`. House pattern (Seatbelt/GhostBus): build + full tests + bench,
parent-verified, publish gated on Ryan's word.

Core modules:
1. **ElementMap (eyes)** — parse an HTML snapshot (stdlib html.parser) or a
   driver-supplied accessibility/DOM snapshot into a numbered list of interactive
   elements (role, name/text, type, href, value), rendered as a compact text map with
   a character budget — Jev-style cheap perception, no screenshots needed.
2. **Actions (hands)** — typed, JSON-serializable: navigate, click, type, press,
   select, scroll, extract, screenshot, wait, done. Targets are element numbers from
   the current map (or URLs for navigate).
3. **Governor (conscience)** — every action classified before execution:
   readonly / write / consequential. Policy: allow / ask / deny per class, domain
   allowlist, consequential keywords (pay, submit order, send, delete, publish…).
   Record-before-execute: an action is written to the trail before it runs; a denied
   or unapproved action never runs. Approval via callback (CLI prompt / programmatic).
4. **Trail (memory)** — JSONL provenance: perception summary, decision, classification,
   policy outcome, action, result, timestamps, per-run step + token accounting.
5. **Drivers (bodies)** — Driver protocol. `FakeDriver`: in-memory mini-web of HTML
   pages (tests/bench/demo). `ChromiumDriver`: OUR OWN CDP client (Ryan, Oct 8: "I
   don't want to harness Playwright, I want to make our own") — stdlib-only Chrome
   DevTools Protocol over --remote-debugging-pipe (websocket fallback), launching a
   Chromium binary directly; perception via Runtime.evaluate → our ElementMap;
   actions via Input/DOM domains; our own auto-wait. NO Playwright, NO Selenium,
   no third-party automation layer anywhere. We drive the Chromium browser through
   our own protocol client — the automation stack is 100% ours; we did not write a
   browser engine and don't claim to.
6. **Deciders (brains)** — Decider protocol. `ScriptedDecider` (explicit steps /
   replay), `RuleDecider` (deterministic offline matching on element names — no LLM),
   `OpenAICompatibleDecider` (any OpenAI-compatible endpoint; key from env only,
   never stored; single decision pass per step from the numbered map).
7. **Runner (loop)** — perceive → decide → govern → record → act, with step budget,
   no-progress/stuck detection (same target+action repeating or map unchanged → stop
   and report), honest stop reasons: done / budget / denied / stuck / error.
8. **Replay export** — trail → standalone Ghost Hands script (our own ChromiumDriver
   + ScriptedDecider): graduate an agent run into a deterministic, model-free script
   that runs on our own stack.
9. **MCP server** — stdlib-only stdio JSON-RPC MCP server exposing `hands_perceive`,
   `hands_act`, `hands_run`, `hands_trail` — Playwright MCP's surface, governed.
10. **CLI** — `ghost-hands demo`, `ghost-hands bench`, `ghost-hands run --script
    steps.json [--driver fake|chromium]`, `ghost-hands export trail.jsonl -o out.py`,
    `ghost-hands mcp`.

## Bench (target)
≥40 cases, all passing, parent re-run: element-map parsing, action classifier,
governor allow/ask/deny + domain policy, end-to-end FakeDriver tasks (search flow,
login form fill, todo add, checkout blocked at approval), stuck detection, token
accounting, replay-export correctness (exported script is valid Python and encodes
the same steps), MCP protocol smoke (initialize/tools/list/tools/call).

## v0.2 — delivered (Oct 8, 2026)
- [x] Live-site proof: `ghost-hands bench --live` — example.com link-follow to
  IANA, and a RuleDecider Wikipedia search ("Oakdale, Tennessee") landing on
  the article. Sandbox proxy handled in-driver (env proxy honored; a local
  credential-injecting relay for authenticated proxies, since Chrome rejects
  credentials in --proxy-server; opt-in --ignore-certificate-errors).
- [x] Decider wire proof: full Runner loop against a local stub
  /chat/completions server (request shape, env gating, parsing). Live-model
  quality still unproven — stated as such.
- [x] Self-healing targets: descriptor re-find + one retry, `heal` trail events.
- [x] Session state: save/load cookies + localStorage (versioned JSON).
- [x] Tabs: open/switch/list via CDP targets.
- [x] Screenshots saved as real PNG files.
- [x] Exported-script execution proof: the bench executes a graduated script
  on real Chromium; export gained driver/start_url/pages parameters.
- [x] Launch package staged locally (LAUNCH.md + site/) — nothing published.
- Verified: pytest 127 passed · bench 62/62 offline · bench --live 2/2.

## v0.3 — delivered (Oct 8, 2026): the capabilities round
Ryan: "More capabilities." All fourteen, each proven by pytest + a named
bench case on real Chromium (local fixtures) unless noted:
- [x] AX eyes: `eyes="ax"` builds the map from Accessibility.getFullAXTree
  (role/name/states), actions resolve via backendNodeId (box model / focus);
  bench compares AX vs DOM key controls and lands a type + click.
- [x] Shadow DOM: the live DOM walk pierces open shadow roots recursively;
  fixture web component's button perceived + clicked (HTML-snapshot eyes
  stay blind to it — stated in the README).
- [x] Frames: same-page iframes (srcdoc proven) walked with frame tags;
  type-in-frame read back, in-frame click observed from the parent.
- [x] Dialogs: Page.javascriptDialogOpening handled per `dialog_policy`
  (dismiss default / accept); dialog + decision recorded as `dialog`
  trail events via the driver's event sink.
- [x] Downloads: Browser.setDownloadBehavior to the run download dir;
  completion awaited and reported (filename + bytes); classified `write`;
  `download` trail event.
- [x] Uploads: `set_file` via DOM.setFileInputFiles; missing paths and
  directories refused.
- [x] Network capture: Network domain on during runs → `net` trail events
  (cap 200/run, truncation noted); `extract` mode `network`.
- [x] Form intelligence: `fill_form` (label/name/placeholder/aria-label
  resolution, per-field results, failures named); `submit: true` →
  consequential through the normal approval path.
- [x] Structured extract: `list` / `table` / `text` modes with exact
  parsed structures asserted.
- [x] Richer input: hover, double_click, right_click, drag (mouse-event
  path), key chords in press (Control+a proven by selection + replace).
- [x] PDF: Page.printToPDF to file; %PDF magic + size asserted.
- [x] Emulation: set_viewport presets (desktop 1280×800, mobile 390×844 +
  touch); media-query perception flips proven. (Pages without a viewport
  meta tag get Chromium's legacy 980px layout under emulation — noted.)
- [x] Wait-for: text / element-descriptor / URL-substring conditions with
  honest timeout errors on the record.
- [x] Raw coordinates: click_at classified `write`, trail marks `raw: true`.
- [x] MCP `hands_act` carries the new kinds unchanged (fill_form smoke);
  CLI run scripts accept all new kinds.
- Verified: pytest 170 passed · bench 82/82 offline · bench --live 2/2
  (one transient egress failure mid-round, re-run green — see LAUNCH.md) ·
  clean-venv install + demo + MCP smoke on 0.3.0 · dist wheel 76,665 B /
  sdist 366,151 B · zero Playwright/Selenium code (grep: only negative
  assertions and comparison copy).

## Roadmap beyond v0.3
- Android body: MrGhosty's accessibility service speaks the same action protocol —
  one hands, phone + web. **BUILT in v0.5.0** (AndroidDriver + the Ghost
  Hands bridge in MrGhosty v1.6.0, loopback + pairing token; proven
  against the bridge contract via a fake bridge; on-device proof
  pending Ryan's install + accessibility grant).
- GhostBus transport: hands as a bus agent other agents can task. (Done, v0.4.0.)
- Seatbelt/GhostGuard policy packs; approval over GhostBus/phone push.
  (Policy packs + bus approvals done, v0.4.0; **phone approver BUILT in
  v0.6.0** — PhoneApprover + the approvals endpoints in the MrGhosty
  v1.7.0 bridge; decisions exist only on the phone's own UI, the API
  has no decision endpoint; on-device proof pending Ryan's install.)
- Live-model driving evaluation (bring a key; measure a named model honestly).

## Rules
- No stealth/undetectability features. The hands identify honestly; governance is
  the product.
- No keys stored; OpenAI-compatible decider reads env only.
- Nothing published (GitHub/PyPI) without Ryan's explicit go.
