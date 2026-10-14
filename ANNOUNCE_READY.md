# Ghost Hands: Announce Pack (REFRESHED to v0.9.2 live state)
Refreshed Oct 14, 2026. This pack supersedes the previous ANNOUNCE_READY.md (which was pinned to v0.6.0) and the draft numbers in LAUNCH.md. The in-repo copy of this refreshed pack is ANNOUNCE_READY.md itself. Every number below was re-verified on Oct 14, 2026, as noted. Nothing here is posted. Each channel is its own approval from Ryan, per draft-first. No em dashes in any copy prepared for Ryan to post.

## Verified numbers (Oct 14, 2026)
- Version: 0.9.2, released Oct 13, 2026. PyPI and GitHub release live.
- Install: `pip install ghost-hands`
- PyPI: https://pypi.org/project/ghost-hands/
- GitHub: https://github.com/littlestjames82-sys/ghost-hands
- Site: https://littlestjames82-sys.github.io/ghost-hands/
- Tests: 443 passing, re-run in full Oct 14 (443 passed in about 4 minutes). The single failure is the known pre-existing one, tests/test_busagent.py::test_hosted_workspace_prefix_end_to_end, a hosted GhostBus health-check wait that fails the same way on the unmodified baseline in this sandbox.
- Bench: 95/95 offline cases, plus 2/2 live cases, re-run Oct 14.
- Demo: `ghost-hands demo` completes in 8 steps; the whole demo run perceives 696 map characters total, about 174 tokens at the chars/4 accounting.
- Eval harness: 8/8 graded tasks under the stub decider, 21 steps, about 521 perception tokens total, 3 approvals requested, 1 heal. Harness proof only, never present this as a model benchmark.
- PyPI downloads: 118 in the last day, 567 in the last week (PyPI stats, Oct 14). Re-check on posting day before using in copy.
- Independent check: M8ven Trust Index, grade B 89/100, Verified Publisher, Live Monitored, with a code subscore of 100/100 and no concerning findings (Oct 13 scan). The grade is capped at B while the project is new and adoption builds. Say exactly that if it comes up; do not call it an A.

## What is new since the old drafts (0.6.0 and earlier)
- MCP tool annotations on all 4 tools (readOnly / destructive / idempotent / openWorld hints), shipped in 0.9.2. This was the one quality item M8ven flagged; it is now fixed and scanned.
- Security hardening release 0.9.1: replay-export injection fix, governor domain-bypass fix, Chromium file:// navigation refused, MCP server robustness, response caps, credential-file permissions tightened to 0600. Full audit in HARDENING_091.md in the repo.
- `ghost-hands doctor`: one command that diagnoses the whole stack (runtime, Chromium, policy packs, model env, bus, phone bridge) with plain-language fixes.
- `ghost-hands phone-proof`: the guided on-device proof walk.
- HTML audit reports: any trail renders into a single self-contained HTML report.
- The seatbelt policy pack: standard posture plus deny patterns for injection-shaped target text ("ignore previous instructions" and friends), with tests guarding benign lookalikes.
- A GhostGuard governor backend option, and MCP driver parity (fake, chromium, simphone, android bodies selectable by env).
- Still true and still the wedge: own stdlib CDP stack, zero dependencies, no Playwright, no Selenium, classify then record then approve then act, trails that graduate into deterministic model-free scripts.

## Honest limits (keep in every long-form post)
- Phone proof on Ryan's actual phone has not run yet. The Android body and bridge are built and tested against fakes; do not claim an on-device proof.
- Live-model driving quality is unproven and unbenchmarked. The OpenAI-compatible decider protocol is proven against a local stub; the eval numbers prove the harness, not a model.
- Chromium only. No speed claims against Playwright, Stagehand, or Browser Use; features only.
- Passwords typed during a run appear in the trail's action records by design (it is an audit log). The HTML report masks password fields. Do not describe the trail as redacted.

## Sequencing (from the adoption plan, Oct 14)
1. Fri Oct 16: r/Python first (copy below). Ryan posts himself as Strong_Primary1494 and stays for the first 2 hours of comments.
2. Week of Oct 19: Show HN on a weekday morning, 8 to 9am ET, not on a Reddit day. Product Hunt the same day for a combined push.
3. After HN: DEV build-in-public write-up, and the governor-denial clip to TikTok, YouTube, and the Facebook Page (clip is a separate Phase 0 work item, not in this pack).
4. r/opensource can run the same week as r/Python, at least a day apart.
Do not post the Show HN and a Reddit launch post on the same morning; comment duty collides.

---

## r/Python post (REFRESHED, for Fri Oct 16)

**Title:** Ghost Hands 0.9.2: zero-dependency, governed browser automation for agents (own CDP client, approval gates, replayable trails)

**Body:**
> I built Ghost Hands because every agent-browser tool I tried would execute whatever the model said, including "submit", "pay", and "delete". Ghost Hands puts a governor between the brain and the hands:
>
> - **Classify, record, approve, act.** Every action is readonly, write, or consequential. Consequential actions need a policy approval or a human's. Everything is written to a JSONL provenance trail before it executes, so a run can be audited or replayed even if it crashes mid-step.
> - **Own CDP stack.** The Chromium driver is a stdlib Chrome DevTools Protocol client over the remote debugging pipe. No Playwright, no Selenium, and `dependencies = []` in pyproject.
> - **Cheap eyes.** Numbered element maps instead of screenshots. The bundled demo perceives 696 map characters across its whole run, about 174 tokens.
> - **Runs graduate.** Any trail exports into a deterministic script that re-runs with no model in the loop. The bench executes a graduated script on real Chromium to prove it.
> - **Self-healing targets.** If the page mutates between seeing and acting, the target is re-found by what it is, retried once, and the heal is logged as a trail event.
> - **An MCP server with the governor attached.** Four tools (perceive, act, run, trail), and since 0.9.2 every tool declares the full annotation hints (readOnly, destructive, idempotent, openWorld), so hosts can warn before a state-changing call.
> - **Plus:** named policy packs (readonly, standard, strict, and a seatbelt pack that denies injection-shaped targets), an HTML audit report for any trail, a `doctor` command that diagnoses the whole stack, and an optional Android body.
>
> Verified numbers: 443 tests passing, bench 95/95 offline plus 2/2 live, eval harness 8/8 under the stub decider. An independent scanner (M8ven Trust Index) currently grades it B 89 with a code subscore of 100 and no concerning findings; the grade itself is capped while the project is new.
>
> Honest limits: the phone proof on my own device has not run yet, live-model driving is unbenchmarked (the decider protocol is proven against a local stub), and it is Chromium only.
>
> PyPI: https://pypi.org/project/ghost-hands/
> Repo: https://github.com/littlestjames82-sys/ghost-hands
>
> Feedback on the classification model especially welcome. Is readonly / write / consequential the right split?

## Show HN (REFRESHED, week of Oct 19, 8 to 9am ET)

**Title:** Show HN: Ghost Hands – governed agent hands for the web (own CDP stack, zero deps)

**Body:**
> Hi HN. I run a one-person studio in Tennessee and kept running into the same problem with browser agents: every tool I tried hands an LLM raw browser power with no judgment attached. Ask for the wrong thing, or let a page prompt-inject the model, and it will cheerfully click submit, pay, or delete.
>
> Ghost Hands is the layer I wanted instead. It is a zero-dependency Python package that gives an agent hands on the web, and a governor:
>
> - Every action is classified before it runs: readonly, write, or consequential. Consequential actions hit a policy: allow, ask a human, or deny. With no approver attached, ask means deny. Silence is never consent.
> - Record before execute: the decision and the action hit a JSONL provenance trail before the driver moves.
> - Trails graduate into deterministic, model-free scripts. Explore with a model once, then re-run the same steps with no model in the loop.
> - The browser body is our own stdlib CDP client over the remote debugging pipe. No Playwright, no Selenium. (We drive Chromium, the browser; we did not write a browser engine and do not claim to have.)
> - Perception is a numbered element map: the demo run perceives 696 map characters in total (about 174 tokens), not screenshots.
> - It also serves as an MCP server (4 tools, full annotation hints since 0.9.2), has named policy packs including one that denies injection-shaped targets, and can render any trail as a self-contained HTML audit report.
>
> State of it: v0.9.2 on PyPI (`pip install ghost-hands`), 443 tests passing, a 95-case offline bench (95/95) plus live cases, and a security hardening pass in 0.9.1 that came out of an external listing review. That review (M8ven) now shows code 100/100 with the overall grade capped at B while the project is new, which feels fair.
>
> The honest limits: how well a live model drives it is that model's business (the decider protocol is proven against a stub; my eval harness numbers prove the harness, not a model), the guided phone proof has not run on my own phone yet, and it is Chromium only for now.
>
> MIT. Feedback on the governance model in particular: is classify-and-approve the right default, or should the classes be split differently?
>
> Repo: https://github.com/littlestjames82-sys/ghost-hands

## r/opensource post (REFRESHED)

**Title:** Ghost Hands: open-source agent hands that ask before they pay (zero-dep Python, own CDP stack)

**Body:**
> Open-sourcing the browser-hands layer from our studio's agent stack. The idea: agents should not get raw browser power. Ghost Hands classifies every action (readonly / write / consequential), enforces a policy with human approval for the consequential ones, records everything to a provenance trail before it runs, and can graduate a finished run into a deterministic script that needs no model.
>
> The automation layer is our own, a zero-dependency stdlib CDP client, because we did not want to ship someone else's stack as our product. It also runs as an MCP server, renders audit reports from trails, and ships policy packs including one that denies prompt-injection-shaped targets.
>
> v0.9.2, MIT, 443 tests, bench 95/95. Repo: https://github.com/littlestjames82-sys/ghost-hands · PyPI: https://pypi.org/project/ghost-hands/

## X post (REFRESHED)

> Playwright drives. Stagehand thinks. Browser Use wanders. Ghost Hands answers for every move.
>
> Governed agent hands for the web: every action classified, consequential ones gated on your approval, everything recorded before it runs. Our own zero-dependency CDP stack, no Playwright under the hood.
>
> v0.9.2 is live: pip install ghost-hands
> https://pypi.org/project/ghost-hands/
> #buildinpublic #python #aiagents

**Follow-up:**
> An independent scanner just graded the code 100/100 (overall B while the project is new). The demo perceives a whole run from 696 map characters, about 174 tokens, and a checkout click with no approver attached gets denied, with the reason in the trail. That denial is the product.

## TikTok caption (REFRESHED)

> I built my AI agent a pair of hands, then I built it a conscience. Ghost Hands classifies every click before it clicks, asks me before anything that costs money, and writes down everything it did. We did not wrap Playwright, we wrote our own browser stack, zero dependencies. v0.9.2 live on PyPI: pip install ghost-hands #buildinpublic #devtools #aiagent #python #ghostdevstudio

## Product Hunt (REFRESHED, same day as Show HN)

**Name:** Ghost Hands
**Tagline:** Governed agent hands for the web: approval gates, provenance trails, zero dependencies
**Description:**
> Ghost Hands gives AI agents hands on the web, with a governor attached. Every action is classified (readonly / write / consequential), judged against a policy, written to a replayable provenance trail before it executes, and gated on human approval when it matters. Finished runs graduate into deterministic scripts that re-run with no model. Under the hood is our own zero-dependency CDP client driving Chromium, no Playwright, no Selenium, plus cheap numbered element maps, an MCP server with full tool annotations, policy packs, HTML audit reports, and an optional Android body. MIT, from Ghost Developer Studio.
**Makers:** Ryan Cotten (Ghost Developer Studio)
**Topics:** Developer Tools, Artificial Intelligence, Open Source, Automation
**First comment:** the Show HN body above, lightly trimmed, with the honest limits kept in.

## DEV post outline (REFRESHED, after HN)
Working title: "I gave an AI agent hands, then I gave it a governor"
1. The problem in one screenshot: an ungoverned agent one prompt from a purchase.
2. The classify / record / approve / act loop, with a real trail excerpt.
3. Why we built our own CDP client (and the honest line: we drive Chromium, we did not write a browser engine).
4. The hardening story: an external scanner flagged missing MCP annotations and a review round found real bugs (replay-export injection, domain bypass); what got fixed in 0.9.1 and 0.9.2.
5. Numbers box: 443 tests, bench 95/95 plus 2/2 live, eval 8/8 under the stub, downloads so far.
6. Honest limits and what is next: phone proof, first outside contributors, good-first-issues link.

## Posting-day checklist (re-verify before any post goes out)
- [ ] Re-run pytest, bench, demo, eval; update every number in the chosen copy if anything moved.
- [ ] Re-check PyPI downloads and the M8ven grade that morning.
- [ ] Confirm the GitHub README status block fix (Issue 1 in the issues draft) has landed, so visitors do not land on v0.9.0 copy.
- [ ] Ryan approves the exact final text of the specific post. Approval for one channel is not approval for another.
