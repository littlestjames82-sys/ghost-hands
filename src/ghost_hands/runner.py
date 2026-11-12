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
records a ``heal`` event in the trail, and retries the action exactly
once at the new number. The healed element has the same descriptor, so
the classification that was approved is the classification that runs.

Honest stop reasons: done / budget / denied / stuck / error. Stuck means
the same (action, target) three times, or an unchanged element map for
three consecutive steps.

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
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .actions import Action
from .cache import PlanCache
from .drivers import Driver
from .deciders import Decider
from .errors import HandsError
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
        history: list[dict] = []
        seen_actions: dict[tuple, int] = {}
        last_signature: Optional[str] = None
        prev_map = None  # v1.4: last step's map, for change notes
        unchanged = 0
        verified_ok = False  # a verify action has passed in this run
        done_rejections = 0  # require_verify gate: dones sent back
        recovery_left = self.recovery_attempts

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
            try:
                element_map = self._perceive_map()
                url = element_map.url or self.driver.current_url()
            except Exception as exc:
                return self._stop(report, step, "error", f"perceive failed: {exc}")
            rendered = element_map.render()
            self.trail.record(
                "perceive",
                step,
                url=url,
                title=element_map.title,
                elements=len(element_map),
                map_chars=len(rendered),
                signature=element_map.signature(),
            )

            # stuck: map unchanged across consecutive steps
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
                    found = (
                        element_map.find_by_descriptor(descriptor)
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
                note = element_map.diff_note(prev_map)
                if note:
                    history.append(
                        {"kind": "system", "note": f"Page change: {note}"}
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
            try:
                result = self.driver.act(exec_action, element)
            except Exception as exc:
                healed = self._attempt_heal(step, action, element, exc)
                if healed is None:
                    self.trail.record(
                        "result", step, ok=False, result=f"error: {exc}"
                    )
                    return self._stop(
                        report, step, "error", f"action failed: {exc}"
                    )
                action, element = healed
                # The retried action is recorded before it runs, too.
                self.trail.record(
                    "execute",
                    step,
                    action=action.to_dict(),
                    classification=decision.classification,
                    element=element.descriptor() if element is not None else None,
                    healed=True,
                    **raw_flag,
                )
                try:
                    heal_exec = action
                    if action_secret_refs(action):
                        heal_exec, more_values, _missing = resolve_action(
                            action, self.secret_provider
                        )
                        for value in more_values:
                            if value not in secret_values:
                                secret_values.append(value)
                    result = self.driver.act(heal_exec, element)
                except Exception as exc2:
                    self.trail.record(
                        "result", step, ok=False, result=f"error: {exc2}"
                    )
                    return self._stop(
                        report,
                        step,
                        "error",
                        f"action failed after heal: {exc2}",
                    )
            if secret_values:
                # A page that echoes a typed secret back must not
                # smuggle it into the trail or the next prompt.
                result = scrub_text(result, secret_values)
            result_fields: dict[str, Any] = {}
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
    ):
        """One self-healing retry: re-perceive, re-find the target by its
        recorded descriptor, and return (action, element) re-pointed at
        the new number — or None when healing does not apply or the
        element is genuinely gone. Always records a ``heal`` event when
        it applies."""
        if action.target is None or element is None:
            return None
        message = str(exc)
        if "target mismatch" not in message and "target missing" not in message:
            return None
        descriptor = element.descriptor()
        found = None
        try:
            fresh_map = self._perceive_map()
            found = fresh_map.find_by_descriptor(descriptor)
        except Exception:
            found = None
        self.trail.record(
            "heal",
            step,
            action=action.to_dict(),
            descriptor=descriptor,
            from_target=action.target,
            to_target=found.number if found is not None else None,
            found=found is not None,
            trigger=message[:200],
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
            usage_fields = {
                "llm_prompt_tokens": report.llm_prompt_tokens,
                "llm_completion_tokens": report.llm_completion_tokens,
                "llm_calls": report.llm_calls,
            }
        self.trail.record("stop", step, reason=reason, summary=summary, **usage_fields)
        acct = self.trail.accounting()
        report.map_chars = acct["map_chars"]
        report.est_tokens = acct["est_tokens"]
        report.actions_by_class = acct["actions_by_class"]
        if report.steps == 0:
            report.steps = acct["steps"]
        return report
