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
from .drivers import Driver
from .deciders import Decider
from .errors import HandsError
from .eyes import ElementMap
from .governor import ASK, Decision, Governor, Policy
from .trail import Trail

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
    ) -> None:
        self.driver = driver
        self.decider = decider
        self.governor = governor or Governor(policy)
        self.approver = approver
        self.trail = trail or Trail(trail_path)
        self.step_budget = step_budget
        self.start_url = start_url
        self._current_step = 0

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
        unchanged = 0

        self._wire_event_sink()
        if self.start_url:
            self.driver.open(self.start_url)

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
                return self._stop(
                    report, step, "stuck", "element map unchanged for 3 consecutive steps"
                )

            # -- decide -------------------------------------------------------
            try:
                action = self.decider.decide(goal, element_map, history)
            except Exception as exc:
                return self._stop(report, step, "error", f"decider failed: {exc}")
            self.trail.record("decide", step, action=action.to_dict())
            history.append(action.to_dict())

            if action.kind == "done":
                return self._stop(report, step, "done", action.summary or "goal complete")

            # stuck: same action at the same target, three times
            signature = action.signature()
            seen_actions[signature] = seen_actions.get(signature, 0) + 1
            if seen_actions[signature] > 3:
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
            try:
                result = self.driver.act(action, element)
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
                    result = self.driver.act(action, element)
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

        return self._stop(report, self.step_budget, "budget", "step budget exhausted")

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
        self.trail.record("stop", step, reason=reason, summary=summary)
        acct = self.trail.accounting()
        report.map_chars = acct["map_chars"]
        report.est_tokens = acct["est_tokens"]
        report.actions_by_class = acct["actions_by_class"]
        if report.steps == 0:
            report.steps = acct["steps"]
        return report
