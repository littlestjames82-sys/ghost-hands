# Ghost Hands — Announce Pack (READY, updated to live state)

> Prepared 2026-10-08 (evening, ET) after the launch verified live.
> This file supersedes the status/copy staleness in LAUNCH.md (that file
> still says "STAGED LOCALLY ONLY / NOT live" and its r/Python draft is
> titled "0.2"). Numbers below are the same verified v0.3.0 numbers from
> LAUNCH.md's honest-claims checklist — nothing inflated.
>
> **Nothing here is posted. Each channel is its own approval from Ryan,
> per draft-first.**
>
> **v0.6.0 note (Oct 8, 2026, later):** v0.6.0 is now PUBLISHED —
> PyPI serves 0.6.0 (via the GitHub release workflow), and the
> release brings the repo current through the 0.4.0 wave (GhostBus
> agent mode, Body Protocol + sim phone, policy packs) and the
> 0.5.0 wave (real Android body) plus 0.6.0's PhoneApprover
> (approvals decided on Ryan's phone). Verified counts are now
> 299 tests / 88 bench cases. The drafts below still describe the
> 0.3.0 feature set and have NOT been updated or posted; a fresh
> announcement pass is needed before any of them go out.

## Live links (verified Oct 8, 2026, parent-checked)

- PyPI: https://pypi.org/project/ghost-hands/ — `pip install ghost-hands`
- GitHub: https://github.com/littlestjames82-sys/ghost-hands
- Site: https://littlestjames82-sys.github.io/ghost-hands/
- Clean-venv `pip install ghost-hands` from PyPI imports 0.3.0, demo green.

## Sequencing — read first (HN account risk)

- Agent Seatbelt's Show HN is already scheduled **Tue Oct 13, 8–9am ET**
  from the `ghostdevstudio` HN account, after a deliberate warm-up week,
  because HN restricts Show HN from new accounts.
- **Do NOT post the Ghost Hands Show HN before Oct 13.** A second Show HN
  from the same young account days earlier risks the restriction hitting
  the Seatbelt launch Ryan already committed to.
- Safe order:
  1. **Now / Fri Oct 9:** X post + TikTok caption (below), r/Python,
     r/opensource — none of these touch the HN account.
  2. **Product Hunt:** any day Ryan picks; independent of HN.
  3. **Ghost Hands Show HN:** Wed Oct 14 or Thu Oct 15, 8–9am ET
     (after Seatbelt's Oct 13 post), same account, now warmer.

---

## X post (READY)

> Playwright drives. Stagehand thinks. Browser Use wanders.
> Ghost Hands answers for every move.
>
> Governed agent hands for the web: every action classified, consequential
> ones gated on YOUR approval, everything recorded before it runs — on our
> own zero-dependency CDP stack. No Playwright under the hood. 👻🖐️
>
> Live now: pip install ghost-hands
> https://pypi.org/project/ghost-hands/
> #buildinpublic #python #aiagents

**Follow-up:**
> The demo reads a whole page in ~174 tokens.
> A checkout click with no approver attached? Denied — and the trail shows
> exactly why. That's the whole product: hands you can audit.
> https://github.com/littlestjames82-sys/ghost-hands

## TikTok caption (READY)

> I built my AI agent a pair of hands — then I built it a conscience.
> Ghost Hands: it classifies every click before it clicks, asks me before
> anything that costs money, and writes down everything it did. Oh — and
> we didn't wrap Playwright. We wrote our own browser stack. 👻
> Live on PyPI: pip install ghost-hands
> #buildinpublic #devtools #aiagent #python #ghostdevstudio

(A Build in Public episode outline is already drafted in LAUNCH.md —
"We Built Our Own Playwright — With a Conscience" — 8 beats, all
artifact-first per the studio editorial rule.)

## r/Python post (READY — title corrected to 0.3)

**Title:** Ghost Hands 0.3 — zero-dependency, governed browser automation for agents (own CDP client, approval gates, replayable trails)

**Body:** (as drafted in LAUNCH.md, with live links added)

> I built Ghost Hands because every agent-browser tool I tried would
> execute whatever the model said — including "submit", "pay", and
> "delete". Ghost Hands puts a governor between the brain and the hands:
>
> - **Classify → policy → record → act.** Every action is readonly, write,
>   or consequential. Consequential actions need policy approval (or a
>   human's). Everything is written to a JSONL trail *before* it executes.
> - **Own CDP stack.** The Chromium driver is a stdlib Chrome DevTools
>   Protocol client over `--remote-debugging-pipe` — no Playwright, no
>   Selenium, `dependencies = []` in pyproject.
> - **Cheap eyes.** Numbered element maps parsed from HTML — the bundled
>   demo reads a page in ~174 estimated tokens.
> - **Runs graduate.** Any trail exports into a deterministic Ghost Hands
>   script (no model) — the bench actually executes a graduated script on
>   real Chromium to prove it.
> - **Self-healing targets:** if the page mutates between perception and
>   action, the target is re-found by descriptor and retried once; the
>   heal is a trail event.
> - **Deciders are pluggable:** scripted rules (offline), or any
>   OpenAI-compatible endpoint (key from env only; wire protocol tested
>   against a local stub server).
> - Plus a stdlib MCP server — Playwright MCP's surface, with the
>   governor attached.
>
> Verified: 170 pytest tests, 82 bench cases, plus live cases
> (example.com link-follow; a real Wikipedia search for "Oakdale,
> Tennessee" driven end-to-end by the rules decider). MIT licensed.
>
> PyPI: https://pypi.org/project/ghost-hands/
> Repo: https://github.com/littlestjames82-sys/ghost-hands
>
> Feedback on the classification model especially welcome.

## r/opensource post (READY)

**Title:** Ghost Hands — open-source agent hands that ask before they pay (zero-dep Python, own CDP stack)

**Body:** as drafted in LAUNCH.md, ending with the live links above.

## Product Hunt (READY)

Name / tagline / description / first comment: as drafted in LAUNCH.md
(Show HN body, lightly trimmed, as first comment). Add the live PyPI and
GitHub links above. Topics: Developer Tools, Artificial Intelligence,
Open Source, Automation.

## Show HN (READY copy — HOLD until Oct 14–15, see sequencing note)

Title and body exactly as drafted in LAUNCH.md — the copy already covers
v0.2.0 and v0.3.0 features and the honest limits (live-model driving
unproven, Chromium only). Add at the top of the body:

> Live on PyPI: `pip install ghost-hands` —
> https://pypi.org/project/ghost-hands/ · Repo:
> https://github.com/littlestjames82-sys/ghost-hands

## Honest-claims guardrails (carried from LAUNCH.md)

- Never claim live-model driving performance — protocol proven vs a
  local stub only.
- Chromium compatibility proven on example.com + Wikipedia only.
- No speed claims vs Playwright/Stagehand/Browser Use — features only.
- example.com link label is "Learn more" as served Oct 2026.
