"""The Runner: perceive → decide → classify → govern → record → act.

Governance invariants:
- Every action is classified and judged by the Governor *before* it runs.
- The govern + execute events hit the trail *before* the driver acts
  (record-before-execute). A denied or unapproved action never runs.
- An "ask" verdict with no approver is treated as a denial with the reason
  "approval required, no approver" — silence is never consent.

Self-healing (v0.2): if a targeted action fails because its element moved
or changed between perception and execution (the drivers raise a
"target mismatch" / "target missing" error), the Runner re-perceives the
page, re-finds the element by the descriptor recorded at decision time,
records a ``heal`` event in the trail, and retries the action at the new
number. The healed element has the same descriptor, so the classification
that was approved is the classification that runs. v1.10: the retry is a
chain — up to ``heal_attempts`` re-find + retry rounds per step (default
2; 1 is the v0.2 behavior, 0 disables healing) — because a page that
moves once can move again before the retry lands.

Error recovery (v1.10): when a step still fails after its heal chain
(driver raised, target genuinely gone), the run no longer has to die on
the spot. With ``error_recovery_attempts`` chances for the whole run
(default 1, 0 disables), the Runner records an ``error_recovery`` event
with the error text, tells the decider what failed in its own history,
drops any queued batch actions, resets the stuck/heal counters, and
continues — the decider picks a different approach on a fresh perception.
Governance denials, approval refusals, and budget stops never recover:
they are decisions and limits, not accidents.

Revalidation (v1.12, P0): the Chromium driver revalidates its target
atomically at dispatch — the same node merely moved proceeds silently
with fresh geometry and is recorded as a ``heal`` event with
``kind="revalidate"``; only a lost node reaches the heal chain, which
now re-resolves under containment (every recorded identity signal must
match; framed elements never heal). Actions bearing secret placeholders
and uploads never heal at all: identity loss stops the run with
``UnhealableTargetError`` so the action can be re-approved. Before
perceiving and before acting, the Runner also waits for page quiet
(bounded; a capped wait is noted, never fatal) when the driver offers
it and ``quiet_wait`` is on.

Honest stop reasons: done / budget / denied / stuck / error. Stuck means
the same (action, target) three times, or an unchanged element map for
three consecutive steps. v1.14: the unchanged-map rule counts *steps
that taught the run nothing* — a step whose action result is a
non-empty string never before seen in this run is progress (the world
answered with new information; the map simply doesn't show it) and
resets the unchanged counter. Identical repeated results, and empty
results, never reset it, so a genuinely looping run still stops.

v0.3: when the driver offers ``perceive()`` (ChromiumDriver's DOM/AX
eyes), the Runner perceives through it instead of the HTML snapshot —
same loop, richer eyes. When the driver exposes an ``event_sink``, the
Runner wires it into the trail so driver-originated events (dialog /
net / download) land in the record under the current step. Raw
coordinate clicks (``click_at``) are marked ``raw: true`` on their
execute events, and structured extract results are attached to their
result events as parsed ``data``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .actions import Action
from .cache import PlanCache
from .drivers import Driver
from .deciders import Decider
from .errors import HandsError, UnhealableTargetError
from .eyes import ElementMap
from .governor import ASK, Decision, Governor, Policy
from .secrets import SecretProvider, action_secret_refs, resolve_action, scrub_text
from .trail import Trail
from .verify import evaluate_verify

Approver = Callable[[Action], bool]

STOP_REASONS = ("done", "budget", "denied", "stuck", "error")


@dataclass
class RunReport:
    goal: str
    stop_reason: str
    steps: int = 0
    summary: str = ""
    map_chars: int = 0
    est_tokens: int = 0
    actions_by_class: dict = field(default_factory=dict)
    trail_path: Optional[str] = None
    results: list[str] = field(default_factory=list)
    # v1.2: real LLM usage when the decider reports it (the OpenAI-
    # compatible decider accumulates the API's usage object); zeros
    # mean "not reported", and est_tokens stays the offline estimate.
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_calls: int = 0
    # v1.8: transient-failure retries the decider spent (see
    # deciders._post_json); part of the run's true cost story.
    llm_retries: int = 0
    # v1.10: self-heals spent (one per recorded heal event) and
    # error-to-loop recoveries spent, counted off the trail at stop
    # time — the same numbers report.summarize() shows.
    heals: int = 0
    error_recoveries: int = 0

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "stop_reason": self.stop_reason,
            "steps": self.steps,
            "summary": self.summary,
            "map_chars": self.map_chars,
            "est_tokens": self.est_tokens,
            "actions_by_class": self.actions_by_class,
            "trail_path": self.trail_path,
            "results": self.results,
            "llm_prompt_tokens": self.llm_prompt_tokens,
            "llm_completion_tokens": self.llm_completion_tokens,
            "llm_calls": self.llm_calls,
            "llm_retries": self.llm_retries,
            "heals": self.heals,
            "error_recoveries": self.error_recoveries,
        }


class Runner:
    def __init__(
        self,
        driver: Driver,
        decider: Decider,
        governor: Optional[Governor] = None,
        policy: Optional[Policy] = None,
        approver: Optional[Approver] = None,
        trail_path: Optional[str] = None,
        trail: Optional[Trail] = None,
        step_budget: int = 25,
        start_url: Optional[str] = None,
        plan_cache: Optional[PlanCache] = None,
        secrets: Optional[SecretProvider | dict] = None,
        require_verify: bool = False,
        recovery_attempts: int = 1,
        max_llm_tokens: Optional[int] = None,
        initial_history: Optional[list[dict]] = None,
        parent_trail_hash: Optional[str] = None,
        heal_attempts: int = 2,
        error_recovery_attempts: int = 1,
        quiet_wait: bool = True,
    ) -> None:
        self.driver = driver
        self.decider = decider
        self.governor = governor or Governor(policy)
        self.approver = approver
        self.trail = trail or Trail(trail_path)
        self.step_budget = step_budget
        self.start_url = start_url
        self._current_step = 0
        # v1.0: the governed plan cache (learn once, replay governed
        # and free) and secret placeholders (resolved only at the
        # execution boundary; see secrets.py). With no mapping given,
        # secrets resolve from the GHOST_HANDS_SECRET_* environment.
        self.plan_cache = plan_cache
        # v1.2: when True, a `done` claim is rejected (and the decider
        # told why) until some verify action has passed in this run.
        self.require_verify = require_verify
        # v1.3: a stuck run gets this many governed second chances —
        # counters reset, the decider is told what stalled, and the
        # loop continues — before the honest stuck stop. And a hard
        # cap on API-reported LLM tokens, checked between steps so a
        # run never dies mid-action over budget.
        self.recovery_attempts = recovery_attempts
        self.max_llm_tokens = max_llm_tokens
        # v1.5: seed history for a resumed run (see ghost_hands.resume).
        self.initial_history = initial_history
        # v1.7: the parent trail's last hash for a resumed run —
        # recorded on the first perceive event so the two trails
        # chain together (see ghost_hands.resume.load_resume_context).
        self.parent_trail_hash = parent_trail_hash
        # v1.10: heal chains (retries per step; 1 = the v0.2 single
        # retry, 0 = healing off) and error-to-loop recovery (chances
        # per run; 0 = a terminal step failure stops the run, as
        # before). Negative values clamp to 0 rather than surprising.
        self.heal_attempts = max(0, heal_attempts)
        self.error_recovery_attempts = max(0, error_recovery_attempts)
        # v1.12 (P0-2): wait-for-quiet before perceiving and before
        # acting, when the driver offers it (ChromiumDriver). False
        # disables; GHOST_HANDS_QUIET=0 disables regardless. Drivers
        # without the hook (fake/sim) make this a no-op.
        self.quiet_wait = quiet_wait
        # v1.2: queued remainder of a decider batch (see decide_batch).
        self._batch_queue: list[Action] = []
        if isinstance(secrets, SecretProvider):
            self.secret_provider = secrets
        else:
            self.secret_provider = SecretProvider(secrets)

    # -- driver integration --------------------------------------------------
    def _perceive_map(self) -> ElementMap:
        """Perceive through the driver's own eyes when it offers them
        (ChromiumDriver.perceive: DOM/AX), else from an HTML snapshot."""
        perceive = getattr(self.driver, "perceive", None)
        if callable(perceive):
            element_map = perceive()
            if element_map is not None:
                return element_map
        html = self.driver.snapshot_html()
        url = self.driver.current_url()
        return ElementMap.from_html(html, url=url)

    def _quiet_wait(self) -> Optional[str]:
        """v1.12 (P0-2): ask the driver to let the page settle. Returns
        the driver's "possibly mid-transition" note when its quiet cap
        expired (the caller records it and proceeds anyway), else None.
        No-op when disabled here, disabled by GHOST_HANDS_QUIET=0, or
        when the driver has no quiet hook."""
        if not self.quiet_wait:
            return None
        if os.environ.get("GHOST_HANDS_QUIET") == "0":
            return None
        wait = getattr(self.driver, "wait_for_quiet", None)
        if not callable(wait):
            return None
        try:
            quiet = wait()
        except Exception:
            return None
        if quiet:
            return None
        return (
            getattr(self.driver, "last_quiet_note", None)
            or "possibly mid-transition: page did not go quiet in time"
        )

    def _driver_event(self, event_type: str, fields: dict[str, Any]) -> None:
        """The sink wired into drivers: their asynchronous events land
        in this run's trail under the current step."""
        self.trail.record(event_type, self._current_step, **fields)

    def _wire_event_sink(self) -> None:
        if hasattr(self.driver, "event_sink"):
            self.driver.event_sink = self._driver_event  # type: ignore[attr-defined]

    def run(self, goal: str = "") -> RunReport:
        report = RunReport(
            goal=goal,
            stop_reason="budget",
            trail_path=str(self.trail.path) if self.trail.path else None,
        )
        history: list[dict] = [
            dict(h) for h in (self.initial_history or [])
        ]
        seen_actions: dict[tuple, int] = {}
        last_signature: Optional[str] = None
        prev_map = None  # v1.4: last step's map, for change notes
        unchanged = 0
        # v1.14 (content visibility): every step result this run has
        # already produced. The unchanged-map stuck rule below asks
        # "did the last steps teach us anything?" — a map can sit
        # perfectly still while exploration pays off (an extract
        # mode returning the page's table after two empty modes),
        # and counting those steps as "unchanged" stops exactly the
        # runs that are making progress. So: a step whose result is
        # a non-empty string NOT in this set counts as progress and
        # resets the unchanged counter. The set is deliberately NOT
        # cleared by recovery/error-recovery: re-learning a known
        # fact after a nudge is not new progress. Identical repeats
        # and empty results never reset anything — the rule weakens
        # only where new information demonstrably arrived. It is
        # applied to every executed action's result, not just
        # extract: novelty is a property of what the world answered,
        # not of which verb asked, and the repeated-(action,target)
        # rule below still guards true loops on its own terms.
        seen_results: set[str] = set()

        def note_result(text: object) -> None:
            """v1.14: feed one executed step's result into the
            stuck detector's progress signal (see seen_results)."""
            nonlocal unchanged
            if not isinstance(text, str) or not text:
                return
            if text not in seen_results:
                seen_results.add(text)
                unchanged = 0
        verified_ok = False  # a verify action has passed in this run
        done_rejections = 0  # require_verify gate: dones sent back
        recovery_left = self.recovery_attempts
        error_recovery_left = self.error_recovery_attempts

        def try_recover(step_no: int, reason: str) -> bool:
            """v1.3: spend a recovery chance on a stuck run. Resets the
            stuck counters, tells the decider what stalled (in its own
            history), drops any stale batch queue, and records the
            recovery on the trail. False when no chances remain."""
            nonlocal recovery_left, unchanged, last_signature
            if recovery_left <= 0:
                return False
            recovery_left -= 1
            attempt = self.recovery_attempts - recovery_left
            self.trail.record(
                "recovery", step_no, reason=reason, attempt=attempt
            )
            history.append(
                {
                    "kind": "system",
                    "note": f"Recovery {attempt}: the run stalled "
                    f"({reason}). The page has been re-perceived; "
                    "choose a different approach and do not repeat the "
                    "action that stalled.",
                }
            )
            unchanged = 0
            last_signature = None
            seen_actions.clear()
            self._batch_queue = []
            return True

        def try_error_recover(step_no: int, error_text: str) -> bool:
            """v1.10: spend an error-recovery chance on a step that
            failed terminally (heal chain exhausted, driver raised).
            Mirrors the stuck recovery's shape — the failure is on the
            trail, the decider is told what broke in its own history,
            stale batch actions are dropped, and the stuck counters
            reset — but the trigger is an execution error, not a stall.
            False when no chances remain for this run. Governance
            denials and budget stops never reach here."""
            nonlocal error_recovery_left, unchanged, last_signature
            if error_recovery_left <= 0:
                return False
            error_recovery_left -= 1
            attempt = self.error_recovery_attempts - error_recovery_left
            self.trail.record(
                "error_recovery",
                step_no,
                error=error_text[:500],
                attempt=attempt,
            )
            history.append(
                {
                    "kind": "system",
                    "note": f"Error recovery {attempt}: the last action "
                    f"failed with this error: {error_text}. The page "
                    "has been re-perceived; choose a different approach "
                    "and do not repeat the action that failed.",
                }
            )
            unchanged = 0
            last_signature = None
            seen_actions.clear()
            self._batch_queue = []
            return True

        self._wire_event_sink()
        if self.start_url:
            self.driver.open(self.start_url)

        # -- v1.0 plan cache: a proven plan for this goal + start URL
        # replays through this same loop, governed step by step, with
        # no decider calls. ``executed_plan`` collects this run's steps
        # (placeholder form) for storage if the run ends ``done``.
        cache_entry: Optional[dict] = None
        cache_start = self.start_url or ""
        plan_queue: list[dict] = []
        cache_used = False
        plan_completed = False
        executed_plan: list[dict] = []
        if self.plan_cache is not None:
            if not cache_start:
                try:
                    cache_start = self.driver.current_url() or ""
                except Exception:
                    cache_start = ""
            cache_entry = self.plan_cache.lookup(goal, cache_start)
            if cache_entry is not None:
                plan_queue = list(cache_entry.get("steps") or [])

        for step in range(1, self.step_budget + 1):
            self._current_step = step
            # -- perceive ---------------------------------------------------
            # v1.12 (P0-2): let the page settle first; a capped wait is
            # recorded on the perceive event and perception proceeds.
            quiet_note = self._quiet_wait()
            try:
                element_map = self._perceive_map()
                url = element_map.url or self.driver.current_url()
            except Exception as exc:
                return self._stop(report, step, "error", f"perceive failed: {exc}")
            rendered = element_map.render()
            # v1.5: the first perceive also records the run's goal —
            # resume and the costs/report tooling read it from there.
            self.trail.record(
                "perceive",
                step,
                url=url,
                title=element_map.title,
                elements=len(element_map),
                map_chars=len(rendered),
                signature=element_map.signature(),
                **({"quiet_note": quiet_note} if quiet_note else {}),
                **({"goal": goal} if step == 1 and goal else {}),
                **(
                    {"parent_hash": self.parent_trail_hash}
                    if step == 1 and self.parent_trail_hash
                    else {}
                ),
            )

            # stuck: map unchanged across consecutive steps — where
            # "unchanged" counts steps since the last *progress*:
            # a signature change, or (v1.14) a step result the run
            # had never seen before (see note_result).
            sig = element_map.signature()
            if sig == last_signature:
                unchanged += 1
            else:
                unchanged = 0
                last_signature = sig
            if unchanged >= 3:
                if try_recover(step, "element map unchanged for 3 consecutive steps"):
                    continue
                return self._stop(
                    report, step, "stuck", "element map unchanged for 3 consecutive steps"
                )

            # -- decide (or replay a cached step) ----------------------------
            if plan_completed and (verified_ok or not self.require_verify):
                # A proven plan replayed in full: every step re-found
                # its target, was re-governed, and any verify steps in
                # the plan re-passed — that IS the proof, so the run
                # ends with the proving run's summary and the decider
                # is never consulted (a scripted decider would simply
                # restart its script on the final page).
                self.trail.record(
                    "cache",
                    step,
                    phase="complete",
                    key=cache_entry.get("key") if cache_entry else None,
                )
                summary = (
                    (cache_entry or {}).get("summary")
                    or "goal complete (governed plan cache replay)"
                )
                self._cache_finish(
                    goal, cache_start, cache_entry, cache_used, executed_plan
                )
                return self._stop(report, step, "done", summary)
            action: Optional[Action] = None
            from_cache = False
            from_batch = False
            if plan_queue:
                planned = plan_queue[0]
                try:
                    candidate = Action.from_dict(planned.get("action") or {})
                except (ValueError, TypeError):
                    candidate = None  # type: ignore[assignment]
                if candidate is not None and candidate.kind == "done":
                    candidate = None  # a cached plan never claims done
                if candidate is not None and candidate.target is not None:
                    descriptor = planned.get("descriptor")
                    # v1.12 (P0-1): replay re-resolution is containment-
                    # checked too — a cached plan must not re-point onto
                    # a same-named twin of the element it was proven on.
                    # A target that cannot be identity-verified simply
                    # falls back to the live decider below.
                    found = (
                        element_map.find_heal_candidate(descriptor)
                        if descriptor
                        else element_map.get(candidate.target)
                    )
                    if found is None:
                        candidate = None  # type: ignore[assignment]
                    else:
                        candidate = Action.from_dict(
                            {**candidate.to_dict(), "target": found.number}
                        )
                if candidate is None:
                    self.trail.record(
                        "cache",
                        step,
                        phase="fallback",
                        key=cache_entry.get("key") if cache_entry else None,
                        reason="cached step could not be re-found on the "
                        "current page; the live decider takes over",
                    )
                    plan_queue = []
                    cache_entry = None
                    plan_completed = False
                else:
                    plan_queue.pop(0)
                    action = candidate
                    from_cache = True
                    cache_used = True
                    self.trail.record(
                        "cache",
                        step,
                        phase="hit",
                        key=cache_entry.get("key") if cache_entry else None,
                        remaining=len(plan_queue),
                    )
                    if not plan_queue:
                        plan_completed = True
            if action is None and not self._batch_queue:
                # v1.4: tell the decider what changed since the last
                # step (navigation, elements appeared/gone) so it can
                # orient on the delta instead of re-deriving it.
                # v1.12 (P0-3): when the change is small, attach the
                # line-level map diff under the summary — a moved or
                # renumbered element reads as an explicit -/+ pair.
                note = element_map.diff_note(prev_map)
                diff = element_map.map_diff(prev_map)
                if note and diff:
                    history.append(
                        {
                            "kind": "system",
                            "note": f"Page change: {note}\nMap diff:\n{diff}",
                        }
                    )
                elif note:
                    history.append(
                        {"kind": "system", "note": f"Page change: {note}"}
                    )
                elif diff:
                    history.append(
                        {
                            "kind": "system",
                            "note": f"Page change: map diff:\n{diff}",
                        }
                    )
            prev_map = element_map
            if action is None and not self._batch_queue and self.max_llm_tokens:
                # v1.3 token budget: checked between steps (never
                # mid-action), only when the decider reports usage.
                usage = getattr(self.decider, "usage", None)
                if isinstance(usage, dict) and usage.get("calls"):
                    spent = int(usage.get("prompt_tokens") or 0) + int(
                        usage.get("completion_tokens") or 0
                    )
                    if spent >= self.max_llm_tokens:
                        return self._stop(
                            report,
                            step,
                            "budget",
                            f"LLM token budget reached: {spent} tokens "
                            f"spent (cap {self.max_llm_tokens}); the run "
                            "stopped between steps, on the record",
                        )
            force_single = False
            if action is None and self._batch_queue:
                # v1.2 batch remainder: the page has been re-perceived
                # since the batch was planned, so a queued target that
                # no longer resolves drops the rest of the batch and
                # the live decider takes over with a single fresh step
                # — never act on a stale element number, and never
                # re-ask for a batch in the same breath (the model's
                # plan just diverged from the page).
                queued = self._batch_queue[0]
                if queued.target is None or element_map.get(queued.target) is not None:
                    action = self._batch_queue.pop(0)
                    from_batch = True
                else:
                    self._batch_queue = []
                    force_single = True
            if action is None:
                try:
                    batch_fn = getattr(self.decider, "decide_batch", None)
                    if (
                        batch_fn is not None
                        and getattr(self.decider, "batch", False)
                        and not force_single
                    ):
                        actions = batch_fn(goal, element_map, history)
                        if not actions:
                            raise HandsError("decider returned an empty batch")
                        action = actions[0]
                        self._batch_queue = list(actions[1:])
                        from_batch = True
                    else:
                        action = self.decider.decide(goal, element_map, history)
                except Exception as exc:
                    return self._stop(report, step, "error", f"decider failed: {exc}")
            self.trail.record(
                "decide",
                step,
                action=action.to_dict(),
                **({"source": "cache"} if from_cache else {}),
                **({"source": "batch"} if from_batch and not from_cache else {}),
            )
            history.append(action.to_dict())

            if action.kind == "done" and self.require_verify and not verified_ok:
                # v1.2 require-verify gate: success must be proven, not
                # claimed. The done is recorded above; here it is sent
                # back with the reason in the decider's own history.
                done_rejections += 1
                self.trail.record(
                    "result",
                    step,
                    ok=False,
                    result="done rejected: require_verify is on and no "
                    "verify action has passed yet in this run",
                )
                if done_rejections >= 3:
                    return self._stop(
                        report,
                        step,
                        "error",
                        "done claimed 3 times without a passing verify "
                        "(require_verify is on)",
                    )
                history.append(
                    {
                        "kind": "system",
                        "note": "done rejected: no verify action has "
                        "passed yet. Run a verify action that proves the "
                        "result (page text, element, or URL), then done.",
                    }
                )
                continue
            if action.kind == "done":
                self._cache_finish(
                    goal,
                    cache_start,
                    cache_entry,
                    cache_used,
                    executed_plan,
                    summary=action.summary or "",
                )
                return self._stop(report, step, "done", action.summary or "goal complete")

            # stuck: same action at the same target, three times
            signature = action.signature()
            seen_actions[signature] = seen_actions.get(signature, 0) + 1
            if seen_actions[signature] > 3:
                if try_recover(step, f"action repeated 3 times: {action.to_dict()}"):
                    continue
                return self._stop(
                    report, step, "stuck", f"action repeated 3 times: {action.to_dict()}"
                )

            # -- govern (record-before-execute) --------------------------------
            element = element_map.get(action.target)
            decision = self.governor.evaluate(action, element, page_url=url)
            if decision.outcome == ASK and self.approver is None:
                # Silence is never consent.
                decision = Decision(
                    decision.classification,
                    "deny",
                    "approval required, no approver",
                )
            self.trail.record(
                "govern",
                step,
                action=action.to_dict(),
                classification=decision.classification,
                outcome=decision.outcome,
                reason=decision.reason,
            )
            if decision.outcome == "deny":
                return self._stop(report, step, "denied", decision.reason)
            if decision.outcome == ASK and not self.approver(action):  # type: ignore[misc]
                return self._stop(report, step, "denied", "approver declined the action")

            # -- execute (recorded before the driver moves) ---------------------
            raw_flag = {"raw": True} if action.kind == "click_at" else {}
            self.trail.record(
                "execute",
                step,
                action=action.to_dict(),
                classification=decision.classification,
                element=element.descriptor() if element is not None else None,
                **raw_flag,
            )
            if action.kind == "verify":
                # Evaluated here, against the live perception — the
                # driver never sees a verify action (like ``done``).
                ok, message = evaluate_verify(action, element_map, self.driver)
                self.trail.record("result", step, ok=ok, result=message)
                if not ok:
                    return self._stop(report, step, "error", message)
                verified_ok = True
                report.results.append(message)
                note_result(message)  # v1.14: progress signal
                report.steps += 1
                executed_plan.append(
                    {
                        "action": action.to_dict(),
                        "descriptor": element.descriptor()
                        if element is not None
                        else None,
                    }
                )
                continue
            # Secret placeholders resolve here and only here: the
            # recorded action above keeps the placeholder form, the
            # approver saw the placeholder form, and the resolved
            # action exists only as the driver's input.
            exec_action = action
            secret_values: list[str] = []
            if action_secret_refs(action):
                exec_action, secret_values, missing = resolve_action(
                    action, self.secret_provider
                )
                if missing:
                    message = (
                        "secret(s) not available in the environment: "
                        + ", ".join(missing)
                        + " (set GHOST_HANDS_SECRET_<NAME>)"
                    )
                    self.trail.record(
                        "result", step, ok=False, result=f"error: {message}"
                    )
                    return self._stop(report, step, "error", message)
            # v1.10 heal chain: act, and on a target failure re-perceive
            # + retry at the fresh number, up to heal_attempts rounds
            # for this step. The healed action is the same action
            # re-pointed — same descriptor, same class — so the
            # governance decision recorded above carries; approvals
            # are never re-asked. Every retry is recorded before it
            # runs and re-resolves secret placeholders at the same
            # execution boundary as the first attempt.
            # v1.12 (P0-2): settle again before acting — the gap between
            # perception and dispatch is where targets go stale. A
            # capped wait is noted for the decider and on the result
            # event; the action proceeds regardless.
            act_quiet_note = self._quiet_wait()
            if act_quiet_note:
                history.append(
                    {"kind": "system", "note": f"Page change: {act_quiet_note}"}
                )
            # v1.12 (P0-1) NEVER-HEAL: an action bearing a secret
            # placeholder, or an upload, must never be re-resolved to a
            # different element — its approval was bound to the element
            # identity the approver saw. (Same-node geometry recompute
            # inside the driver is unaffected: that is the same node.)
            never_heal = action.kind == "set_file" or bool(
                action_secret_refs(action)
            )
            result = ""
            failure: Optional[Exception] = None
            heals_this_step = 0
            current_exec = exec_action
            while True:
                try:
                    result = self.driver.act(current_exec, element)
                    failure = None
                    # v1.12 (P0-1): a same-node revalidation inside the
                    # driver (the element moved; the driver recomputed
                    # and proceeded) is recorded as a heal event of kind
                    # "revalidate" — no model call was spent on it.
                    rev = getattr(self.driver, "last_revalidation", None)
                    if rev is not None:
                        try:
                            self.driver.last_revalidation = None  # type: ignore[attr-defined]
                        except Exception:
                            pass
                        if rev.get("moved"):
                            self.trail.record(
                                "heal",
                                step,
                                kind="revalidate",
                                action=action.to_dict(),
                                descriptor=element.descriptor()
                                if element is not None
                                else None,
                                from_target=action.target,
                                to_target=action.target,
                                found=True,
                                from_box=rev.get("from_box"),
                                to_box=rev.get("to_box"),
                                attempt=0,
                            )
                    break
                except Exception as exc:
                    failure = exc
                    message = str(exc)
                    if never_heal and (
                        "target mismatch" in message
                        or "target missing" in message
                    ):
                        # Fail loudly and distinctly: no re-resolution,
                        # so the run stops and the action can be
                        # re-approved against a fresh perception.
                        self.trail.record(
                            "heal",
                            step,
                            kind="refused",
                            action=action.to_dict(),
                            descriptor=element.descriptor()
                            if element is not None
                            else None,
                            from_target=action.target,
                            to_target=None,
                            found=False,
                            trigger=message[:200],
                            attempt=heals_this_step + 1,
                        )
                        failure = UnhealableTargetError(
                            "target unhealable: "
                            f"{action.kind} on [{action.target}] carries "
                            "a secret or an upload bound to the element "
                            f"the approver saw; its identity was lost "
                            f"({message}); the run stops so the action "
                            "can be re-approved"
                        )
                        break
                    if heals_this_step >= self.heal_attempts:
                        break
                    healed = self._attempt_heal(
                        step,
                        action,
                        element,
                        exc,
                        attempt=heals_this_step + 1,
                        current_map=element_map,
                    )
                    if healed is None:
                        break
                    heals_this_step += 1
                    action, element = healed
                    # The retried action is recorded before it runs, too.
                    self.trail.record(
                        "execute",
                        step,
                        action=action.to_dict(),
                        classification=decision.classification,
                        element=element.descriptor()
                        if element is not None
                        else None,
                        healed=True,
                        **raw_flag,
                    )
                    current_exec = action
                    if action_secret_refs(action):
                        current_exec, more_values, _missing = resolve_action(
                            action, self.secret_provider
                        )
                        for value in more_values:
                            if value not in secret_values:
                                secret_values.append(value)
            if failure is not None:
                self.trail.record(
                    "result", step, ok=False, result=f"error: {failure}"
                )
                failure_summary = (
                    f"action failed after heal: {failure}"
                    if heals_this_step
                    else f"action failed: {failure}"
                )
                # v1.10: a terminal step failure can spend an
                # error-recovery chance and hand the run back to the
                # decider instead of stopping — the failure stays on
                # the record either way.
                if try_error_recover(step, str(failure)):
                    continue
                return self._stop(report, step, "error", failure_summary)
            if secret_values:
                # A page that echoes a typed secret back must not
                # smuggle it into the trail or the next prompt.
                result = scrub_text(result, secret_values)
            result_fields: dict[str, Any] = {}
            if act_quiet_note:
                result_fields["quiet_note"] = act_quiet_note
            if action.kind == "extract":
                # Structured extracts land in the record as parsed data,
                # not just an opaque string.
                try:
                    parsed = json.loads(result)
                except (ValueError, TypeError):
                    parsed = None
                if isinstance(parsed, (list, dict)):
                    result_fields["data"] = parsed
                    result_fields["mode"] = action.mode or "text"
            self.trail.record("result", step, ok=True, result=result, **result_fields)
            report.results.append(result)
            note_result(result)  # v1.14: progress signal
            report.steps += 1
            executed_plan.append(
                {
                    "action": action.to_dict(),
                    "descriptor": element.descriptor()
                    if element is not None
                    else None,
                }
            )

        return self._stop(report, self.step_budget, "budget", "step budget exhausted")

    def _cache_finish(
        self,
        goal: str,
        cache_start: str,
        cache_entry: Optional[dict],
        cache_used: bool,
        executed_plan: list[dict],
        summary: str = "",
    ) -> None:
        """Settle the plan cache at a ``done`` stop: a replayed plan
        earns a hit; a fresh successful run becomes the plan for its
        goal. Runs that end any other way store nothing."""
        if self.plan_cache is None:
            return
        if cache_used and cache_entry is not None:
            self.plan_cache.note_hit(str(cache_entry.get("key")))
            self.trail.record(
                "cache",
                self._current_step,
                phase="hit-confirmed",
                key=cache_entry.get("key"),
            )
        elif executed_plan:
            key = self.plan_cache.store(
                goal, cache_start, executed_plan, summary=summary
            )
            self.trail.record(
                "cache", self._current_step, phase="store", key=key
            )

    def _attempt_heal(
        self,
        step: int,
        action: Action,
        element,
        exc: Exception,
        attempt: int = 1,
        current_map: Optional[ElementMap] = None,
    ):
        """One self-healing retry: re-perceive, re-find the target by its
        recorded descriptor, and return (action, element) re-pointed at
        the new number — or None when healing does not apply or the
        element is genuinely gone. Always records a ``heal`` event when
        it applies; ``attempt`` is the chain position (v1.10) and lands
        on the event so a multi-heal step reads in order.

        v1.12 (P0-1): the re-find goes through the containment-checked
        lookup (``find_heal_candidate``) — every recorded identity
        signal must match, framed elements never heal, and ambiguity is
        a refusal, not a guess. The heal event (kind "reresolve")
        carries the page-change note against the step's own map when
        one is computable, so a moved target reads as an explicit
        change in the record, not silent renumbering."""
        if action.target is None or element is None:
            return None
        message = str(exc)
        if "target mismatch" not in message and "target missing" not in message:
            return None
        descriptor = element.descriptor()
        found = None
        fresh_map = None
        quiet_note = self._quiet_wait()
        try:
            fresh_map = self._perceive_map()
            found = fresh_map.find_heal_candidate(descriptor)
        except Exception:
            found = None
        change_note = None
        if fresh_map is not None and current_map is not None:
            change_note = fresh_map.map_diff(current_map) or fresh_map.diff_note(
                current_map
            )
        self.trail.record(
            "heal",
            step,
            kind="reresolve",
            action=action.to_dict(),
            descriptor=descriptor,
            from_target=action.target,
            to_target=found.number if found is not None else None,
            found=found is not None,
            trigger=message[:200],
            attempt=attempt,
            **({"change_note": change_note} if change_note else {}),
            **({"quiet_note": quiet_note} if quiet_note else {}),
        )
        if found is None:
            return None
        healed_action = Action.from_dict(
            {**action.to_dict(), "target": found.number}
        )
        return healed_action, found

    def _stop(self, report: RunReport, step: int, reason: str, summary: str) -> RunReport:
        report.stop_reason = reason
        report.summary = summary
        # v1.2: real LLM usage, when the decider reports it, lands on
        # the report and in the trail's stop event (so the HTML report
        # can show actual tokens next to the offline estimate).
        usage = getattr(self.decider, "usage", None)
        usage_fields: dict[str, Any] = {}
        if isinstance(usage, dict) and usage.get("calls"):
            report.llm_prompt_tokens = int(usage.get("prompt_tokens") or 0)
            report.llm_completion_tokens = int(usage.get("completion_tokens") or 0)
            report.llm_calls = int(usage.get("calls") or 0)
            report.llm_retries = int(usage.get("retries") or 0)
            usage_fields = {
                "llm_prompt_tokens": report.llm_prompt_tokens,
                "llm_completion_tokens": report.llm_completion_tokens,
                "llm_calls": report.llm_calls,
                "llm_retries": report.llm_retries,
            }
        # v1.5: the stop event also carries the driver's final URL —
        # the freshest position in the trail, and what resume reads.
        try:
            stop_url = self.driver.current_url() or ""
        except Exception:
            stop_url = ""
        self.trail.record(
            "stop", step, reason=reason, summary=summary, url=stop_url,
            **usage_fields,
        )
        acct = self.trail.accounting()
        report.map_chars = acct["map_chars"]
        report.est_tokens = acct["est_tokens"]
        report.actions_by_class = acct["actions_by_class"]
        # v1.10: heal + error-recovery totals, counted off the trail
        # itself — the same source report.summarize() reads, so the
        # RunReport and the HTML audit can never disagree.
        report.heals = sum(
            1 for ev in self.trail.events if ev.get("type") == "heal"
        )
        report.error_recoveries = sum(
            1
            for ev in self.trail.events
            if ev.get("type") == "error_recovery"
        )
        if report.steps == 0:
            report.steps = acct["steps"]
        return report
