# Ghost Hands — Launch Package

> **STATUS: DRAFT — STAGED LOCALLY ONLY.**
> Nothing in this file has been published, posted, pushed, or deployed.
> Every step below runs only on Ryan's explicit go. All copy is DRAFT.
> Numbers cited are the verified v0.3.0 numbers (see "Honest-claims
> checklist" at the bottom) — do not inflate them anywhere.

---

## Positioning

**Ghost Hands is the governed agent-hands layer for the web: every action
an agent takes is classified, judged against policy, recorded before it
runs, and — when it matters — gated on a human approval. And the browser
stack underneath is our own: a zero-dependency CDP client driving Chromium
directly. No Playwright. No Selenium. Nobody else's automation stack.**

One-liner (primary): *Playwright drives. Stagehand thinks. Browser Use
wanders. Ghost Hands answers for every move.*

### Tagline options (pick one at launch)
1. "Agent hands with a conscience — classified, approved, and on the record."
2. "Your agent's hands, on a leash you hold: policy gates, approval prompts, and a replayable trail."
3. "We didn't wrap Playwright. We built the hands — governor included."

### The wedge (why this exists)
- Every tool in the field will click "Pay", "Send", or "Delete" the moment
  a model tells it to. Ghost Hands classifies first (`readonly` / `write` /
  `consequential`), applies a policy, and stops for a human on the
  consequential ones. No approver present = denied. Silence is never consent.
- Every run writes a provenance trail *before* actions execute — audit it,
  diff it, replay it.
- Finished runs graduate into deterministic, model-free scripts (Chromium
  or fake body): explore with a model once, re-run forever for free.
- Perception is cheap on purpose: numbered element maps, ~174 tokens for
  the demo page — not screenshot firehoses.
- Self-healing targets: if the page mutates between seeing and acting, the
  hands re-find the element by what it *is*, retry once, and log the heal.
- Local-first: your Chromium, your keys (env only, never stored), zero
  runtime dependencies, no cloud-browser upsell.

---

## Show HN (DRAFT)

**Title:** Show HN: Ghost Hands – governed agent hands for the web (own CDP stack, zero deps)

**Body:**

> Hi HN — I run a one-person studio in Tennessee and kept running into the
> same problem with browser agents: every tool I tried (Playwright MCP,
> Stagehand, Browser Use) hands an LLM raw browser power with no judgment
> attached. Ask for the wrong thing, or let a page prompt-inject the model,
> and it will cheerfully click submit/pay/delete.
>
> Ghost Hands is the layer I wanted instead. It's a zero-dependency Python
> package that gives an agent hands on the web — and a governor:
>
> - Every action is classified before it runs: readonly / write /
>   consequential. Consequential actions (form submits, anything that
>   smells like pay/send/delete/publish) hit a policy: allow, ask a human,
>   or deny. With no approver attached, "ask" means deny.
> - Record-before-execute: the decision and the action hit a JSONL
>   provenance trail before the driver moves, so a run can be audited or
>   replayed even if it crashes mid-step.
> - Trails graduate into deterministic, model-free scripts — explore with
>   a model once, then re-run the same steps with no model in the loop.
> - The browser body is our own stdlib CDP client over
>   --remote-debugging-pipe. No Playwright, no Selenium — the maintainer of
>   our stack is us. (We drive Chromium, the browser; we didn't write a
>   browser engine.)
> - Perception is a numbered element map (Jev-style): the demo run reads a
>   page in ~174 estimated tokens. Stuck detection stops and reports
>   instead of wandering.
>
> v0.2.0 adds tabs, session save/load (cookies + localStorage),
> self-healing targets (page mutates between perception and action → the
> element is re-found by descriptor, retried once, heal logged), real
> screenshots to disk, and an OpenAI-compatible decider whose wire protocol
> is proven against a local stub. v0.3.0 adds a second pair of eyes
> (Accessibility-tree maps alongside the shadow-piercing DOM walk),
> one-action form filling, downloads/uploads, network capture on the
> trail, structured extract, dialog policy, PDF, viewport presets,
> richer input (hover/drag/chords/raw coordinates), and wait-for
> conditions — every one classified and trailed like the rest.
> It's verified by a 170-test pytest suite and an 82-case bench,
> including live runs against example.com and a real Wikipedia search
> driven end-to-end by the deterministic decider.
>
> The honest limits: the LLM decider's protocol is proven against a stub;
> how well any given live model drives it is that model's business.
> Chromium only, for now — the same action protocol is designed to gain an
> Android body from our phone agent (MrGhosty) later.
>
> MIT. I'd love feedback on the governance model in particular — is
> classify-and-approve the right default, or should read/write/consequential
> be split differently?

---

## r/Python post (DRAFT)

**Title:** Ghost Hands 0.2 — zero-dependency, governed browser automation for agents (own CDP client, approval gates, replayable trails)

**Body:**

> I built Ghost Hands because every agent-browser tool I tried would
> execute whatever the model said — including "submit", "pay", and
> "delete". Ghost Hands puts a governor between the brain and the hands:
>
> - **Classify → policy → record → act.** Every action is readonly, write,
>   or consequential. Consequential actions need policy approval (or a
>   human's). Everything is written to a JSONL trail *before* it executes.
> - **Own CDP stack.** The Chromium driver is a stdlib Chrome DevTools
>   Protocol client over `--remote-debugging-pipe` — no Playwright, no
>   Selenium, `dependencies = []` in pyproject. Driving a real tab,
>   session save/load (cookies + localStorage), and screenshots included.
> - **Cheap eyes.** Numbered element maps parsed from HTML — the bundled
>   demo reads a page in ~174 estimated tokens.
> - **Runs graduate.** Any trail exports into a deterministic Ghost Hands
>   script (no model) — the bench actually executes a graduated script on
>   real Chromium to prove it.
> - **Self-healing targets** (new in 0.2): if the page mutates between
>   perception and action, the target is re-found by descriptor and
>   retried once; the heal is a trail event.
> - **Deciders are pluggable:** scripted, deterministic rules (offline),
>   or any OpenAI-compatible endpoint (key from env only; wire protocol
>   tested against a local stub server).
> - Plus a stdlib MCP server (`hands_perceive` / `hands_act` / `hands_run`
>   / `hands_trail`) — Playwright MCP's surface, with the governor attached.
>
> Verified locally: 170 pytest tests, 82 bench cases, plus live cases
> (example.com link-follow; a real Wikipedia search for "Oakdale,
> Tennessee" driven end-to-end by the rules decider). MIT licensed.
> Feedback on the classification model especially welcome.

## r/opensource post (DRAFT)

**Title:** Ghost Hands — open-source agent hands that ask before they pay (zero-dep Python, own CDP stack)

**Body:**

> Open-sourcing the browser-hands layer from our studio's agent stack.
> The idea: agents shouldn't get raw browser power. Ghost Hands classifies
> every action (readonly / write / consequential), enforces a policy with
> human approval for the consequential ones, records everything to a
> provenance trail before it runs, and can graduate a finished run into a
> deterministic script that needs no model. The browser automation layer
> is our own — a zero-dependency stdlib CDP client — because we didn't
> want to ship someone else's stack as our product. MIT. Links + a
> 82-case bench and 170 tests in the repo; live example (real Wikipedia
> search, governed) in the README.

---

## X / TikTok short copy (DRAFT)

**X (post):**
> Playwright drives. Stagehand thinks. Browser Use wanders.
> Ghost Hands answers for every move.
>
> Governed agent hands for the web: every action classified, consequential
> ones gated on YOUR approval, everything recorded before it runs — on our
> own zero-dependency CDP stack. No Playwright under the hood. 👻🖐️
> #buildinpublic #python #aiagents

**X (follow-up):**
> The demo reads a whole page in ~174 tokens.
> A checkout click with no approver attached? Denied — and the trail shows
> exactly why. That's the whole product: hands you can audit.

**TikTok caption:**
> I built my AI agent a pair of hands — then I built it a conscience.
> Ghost Hands: it classifies every click before it clicks, asks me before
> anything that costs money, and writes down everything it did. Oh — and
> we didn't wrap Playwright. We wrote our own browser stack. 👻
> #buildinpublic #devtools #aiagent #python #ghostdevstudio

---

## Build in Public episode outline (DRAFT)

**Working title:** "We Built Our Own Playwright — With a Conscience"
**Format:** vertical, studio standard; show artifacts, not gaps.

1. **Cold open (the problem):** on camera — an ungoverned agent (any
   competitor) one prompt away from clicking "Place order". Freeze frame:
   "Would you let your agent do that unsupervised?"
2. **The bench moment:** run `ghost-hands bench` live on screen — 82/82,
   call out the money case: "checkout blocked without approval."
3. **Show the trail:** open a trail file; walk one step: perceive →
   decide → govern → execute → result. "Receipts for every move."
4. **Show the hands are ours:** the CDP pipe client, zero dependencies —
   print the import audit. "No Playwright. No Selenium. Ours."
5. **Live proof:** the Wikipedia run — RuleDecider types "Oakdale,
   Tennessee", the Search click classifies *consequential*, approval
   granted, lands on the Oakdale article. Real browser, real site.
6. **The heal:** mutation demo — banner appears mid-run, target shifts,
   `heal` event in the trail, action still lands.
7. **Graduation:** export a trail → run the graduated script with no
   model. "Explore once with AI. Replay forever free."
8. **Close:** what's next (Android body — the same hands on a phone),
   launch ask: star/feedback; no mention of anything unfinished as a lack
   — frame purely as what's coming.

---

## Product Hunt draft (DRAFT)

**Name:** Ghost Hands
**Tagline:** Governed agent hands for the web — approval gates, provenance trails, zero dependencies
**Description:**
> Ghost Hands gives AI agents hands on the web — with a governor attached.
> Every action is classified (readonly / write / consequential), judged
> against a policy, written to a replayable provenance trail before it
> executes, and gated on human approval when it matters. Finished runs
> graduate into deterministic scripts that re-run with no model. Under the
> hood is our own zero-dependency CDP client driving Chromium — no
> Playwright, no Selenium — plus cheap numbered element maps (~174 tokens
> for the demo page), tabs, session save/load, self-healing targets, and
> an MCP server. MIT, from Ghost Developer Studio.
**Makers:** Ryan Cotten (Ghost Developer Studio)
**Topics:** Developer Tools, Artificial Intelligence, Open Source, Automation
**First comment (draft):** the Show HN body, lightly trimmed.

---

## Publish steps (run ONLY on Ryan's explicit go, in order)

1. **Final local verification:** `pytest -q` (expect 170 passed),
   `ghost-hands bench` (expect 82/82), `ghost-hands bench --live`
   (expect 2/2), `python -m build` (0.3.0 wheel + sdist), clean-venv
   install + `ghost-hands demo` + MCP smoke. Record the numbers; update
   README/this file if they moved.
2. **Create the GitHub repo** `littlestjames82-sys/ghost-hands` (public,
   MIT) via the GitHub API with the stored connector (same route used for
   agent-seatbelt / ghostbus): push the tree at `~/workspace/ghost-hands/`
   (src layout, tests, examples, README, LICENSE, PLAN.md, LAUNCH.md,
   site/) as the initial commit.
3. **Tag `v0.3.0`** on that commit and cut a GitHub release with the notes
   from the README changelog; attach the built wheel + sdist from `dist/`.
4. **PyPI trusted publishing** (mirror the ghost-seatbelt flow exactly):
   a. On PyPI: "Publishing → Add a new pending publisher" — project name
      `ghost-hands`, owner `littlestjames82-sys`, repository `ghost-hands`,
      workflow `release.yaml`, environment `pypi`.
      (Name availability re-verify immediately before: `ghost-hands`
      returned 404 on Oct 8, 2026.)
   b. Commit `.github/workflows/release.yaml` (build + `pypa/gh-action-pypi-publish`
      with OIDC, environment `pypi`) — same shape as agent-seatbelt's.
   c. Trigger the workflow from the v0.3.0 release; verify
      `pip install ghost-hands` in a clean venv + `ghost-hands demo`.
   d. After the first successful publish, delete the "pending" nature by
      confirming the project page lists 0.3.0 files (wheel + sdist).
5. **Enable GitHub Pages** for the repo: the landing page lives at
   `site/` in the repo root, so publish that directory — either add the
   standard static-pages workflow (`actions/upload-pages-artifact` with
   `path: site`, Pages source = GitHub Actions) or serve a `gh-pages`
   branch containing the contents of `site/`. Verify the logo path
   (`assets/ghost-developer-studio-logo.png`, relative to `site/index.html`)
   resolves on the live URL before announcing it.
6. **Announce** (each is its own approval): Show HN (weekday 8–9am ET),
   r/Python + r/opensource posts, X/TikTok copy, Product Hunt submission,
   Build in Public episode. Re-check every number in the copy against the
   honest-claims checklist below on the day.

---

## Honest-claims checklist (verified Oct 8, 2026 — re-verify on launch day)

- [x] pytest: **170 passed** (v0.3.0 tree).
- [x] Bench (offline): **82/82 passed**, including live-Chrome fixture
      cases (perceive/type/click, session roundtrip, tabs, screenshot,
      graduated-script execution) and the full v0.3 capability set on
      real Chromium (AX eyes, shadow DOM, frames, dialogs, downloads,
      uploads, network capture, fill_form, structured extract, hover,
      drag, chords, PDF, viewport, wait_for, click_at).
- [x] Bench (live): **2/2 passed** — example.com (title "Example Domain",
      IANA link followed to https://www.iana.org/help/example-domains,
      landed title "Example Domains") and Wikipedia (RuleDecider searched
      "Oakdale, Tennessee", 3 executed steps, landed on
      https://en.wikipedia.org/wiki/Oakdale,_Tennessee, title
      "Oakdale, Tennessee - Wikipedia"). Note: one transient failure was
      observed during the v0.3 round (the sandbox egress proxy closing
      connections across several hosts, `net::ERR_CONNECTION_CLOSED`);
      the re-run passed 2/2 and adjacent probes loaded Wikipedia with
      the same code — environmental, not a regression.
- [x] Dist sizes (0.3.0): wheel **76,665 B**, sdist **366,151 B** (the
      sdist ships the full test suite + bench fixtures).
- [x] Demo perception: **696 map chars ≈ 174 tokens (chars/4)** for the
      bundled demo run; demo completes in 8 steps.
- [x] Zero runtime dependencies: pyproject `dependencies = []`; import
      audit = stdlib only.
- [x] No Playwright / Selenium code: grep shows only negative assertions
      and comparison copy (tests assert exports do NOT contain them).
- [x] example.com link label: as served Oct 2026 it reads **"Learn more"**
      (href https://iana.org/help/example-domains) — do not claim a
      "More information" label in copy.
- [ ] LLM decider: protocol proven vs a **local stub** only. Never claim
      live-model performance, success rates, or benchmarks.
- [ ] ChromiumDriver vs arbitrary third-party sites: proven on
      example.com + Wikipedia only. No broad compatibility claims.
- [ ] No speed claims vs Playwright/Stagehand/Browser Use — we have not
      benchmarked head-to-head; the comparison table describes features,
      not performance.
- [ ] PyPI/GitHub: NOT live until step 2–4 above are done. All install
      commands in copy are future tense until then.
