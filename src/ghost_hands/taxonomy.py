"""Failure taxonomy (v1.17, roadmap P3-13): every run ends with a
final status and, on failure, at most one taxonomy label — derived
deterministically at stop time from the run's own recorded signals
(the stop reason, the stop summary, and the trail). No model call,
no new perception, and no influence on the run itself: the
classifier only reads what the run already recorded, so two runs
with identical records always classify identically. The point is
the benchmark report (Online-Mind2Web scope §7/§8): a published
failure breakdown — how many runs died of staleness, how many of
cost, how many never found a target — instead of a bare pass rate.

Final status (exactly one per run)::

    SUCCESS / NO_TARGET / BLOCKED / TIMEOUT / ERROR

Failure label (at most one per run; None on SUCCESS)::

    STALE / MOVED / VANISHED / OCCLUDED / BLOCKED / COST_CAP /
    NO_TARGET / WRONG_ANSWER

The mapping, in precedence order — first match wins, so the label
names the signal closest to the stop (the proximate cause):

1. ``done`` -> (SUCCESS, None).
2. ``denied`` -> (BLOCKED, BLOCKED): the governor or the approver
   refused. The run was stopped BY a decision, not by the page.
3. ``unverified`` -> (ERROR, WRONG_ANSWER): the run claimed
   success and its own evidence checklist judged the claim
   unproven. The residual status is ERROR; the label carries the
   precise meaning.
4. ``budget`` on the token cap (the summary's "LLM token budget
   reached") -> (TIMEOUT, COST_CAP): the status names the limit
   family — the run was cut off by a ceiling — and the label
   names the specific ceiling. This rule outranks NO_TARGET
   below: a budget that fired is the proximate cause even on an
   empty page.
5. The NO_TARGET definition (below) -> (NO_TARGET, NO_TARGET).
   Applies to step-budget, stuck, and error stops.
6. ``budget`` on step exhaustion -> (TIMEOUT, last-signal label
   or None): running out of steps is not itself a failure class,
   so the label reports the last recorded failure signal when one
   names a class.
7. ``error`` on a terminal action failure -> the failure's own
   recorded signals, most specific first:
   - "covered by another element" in the failure text or the
     last heal trigger (the Chromium revalidation's occlusion
     verdict, drivers.py) -> OCCLUDED;
   - the last heal re-resolution searched and found nothing
     (``found=False``) -> VANISHED: positive evidence of absence;
   - a never-heal refusal (heal kind "refused"), or a
     target-shaped failure with no search evidence -> STALE:
     the pointer was stale at dispatch. Documented limit: for
     the refusal, whether the element moved or vanished cannot
     be told from the record — the refusal exists precisely so
     that question is never guessed at (secret/upload actions),
     so STALE here means "stale at dispatch, fate unsearched".
8. ``error`` on a verify failure, or on the require-verify gate's
   triple done claim -> (ERROR, WRONG_ANSWER): the run's own
   truth-check contradicted the claimed state, or success was
   claimed repeatedly with no proof at all.
9. ``stuck`` -> (ERROR, last-signal label or None): the stuck
   detector fired; a bare stall names no class honestly.
10. Anything else (perceive / decider / secret / driver errors)
    -> (ERROR, None).

The last-signal scan (rules 6 and 9, and the tail of 7): walk the
heal and failed-postcondition events in trail order; the LAST one
that names a class decides:

- heal ``reresolve`` with ``found=False`` -> VANISHED
- heal ``refused`` -> STALE
- heal ``revalidate``, or ``reresolve`` with ``found=True`` ->
  MOVED (the element had relocated and the run kept chasing it)
- postcondition failed -> OCCLUDED only when the failure texts
  carry the occlusion verdict; a bare no-observable-change
  failure names no class (a dead button is not proof of
  coverage), so the scan continues past it to earlier signals.

NO_TARGET, pinned (conservative — when in doubt, the label is the
stop-reason default, never NO_TARGET). A run is NO_TARGET when
ALL of these hold:

- the stop reason is ``budget`` (step exhaustion), ``stuck``, or
  ``error``;
- every ``perceive`` event in the trail recorded zero elements:
  no candidate was ever presented. (A run that SAW elements and
  lost them classifies by the loss — VANISHED or STALE — not
  NO_TARGET: rules 7/9 see those trails first only when this
  definition fails, which it does as soon as any perception was
  non-empty.)
- no targeted action ever succeeded: no step pairs an ``execute``
  event carrying a target with an ``ok`` result. Targetless
  successes (extracts, waits) do not count — reading an empty
  page is not engagement with a target;
- and the run actually tried to engage: at least one targeted
  ``execute`` event exists, or the stop was the stuck detector
  (which only fires after steps of trying).

Alignment with errors.py, honestly: ``StaleTargetError`` covers
both the revalidation's "gone/hidden" verdict and its AX
"no longer resolves" verdict. A run that dies on it WITH a failed
re-resolution on the record is VANISHED (the search is the
evidence); a run that dies on it without one — heal attempts off,
or a positional driver's "target missing" — is STALE (the
dispatch-time pointer was wrong; nothing searched). The label
follows the recorded evidence, not the exception's name.
"""

from __future__ import annotations

from typing import Any, Optional

STATUSES = ("SUCCESS", "NO_TARGET", "BLOCKED", "TIMEOUT", "ERROR")

FAILURE_LABELS = (
    "STALE",
    "MOVED",
    "VANISHED",
    "OCCLUDED",
    "BLOCKED",
    "COST_CAP",
    "NO_TARGET",
    "WRONG_ANSWER",
)

# The Chromium revalidation's occlusion verdict (drivers.py): the
# one place an occlusion is positively identified in the record.
_OCCLUSION_PHRASE = "covered by another element"

_TOKEN_CAP_PREFIX = "LLM token budget reached"
_TARGET_SHAPED = ("target missing", "target mismatch", "target unhealable")


def _events_of(events: list[dict], type_: str) -> list[dict]:
    return [ev for ev in events if ev.get("type") == type_]


def _heals(events: list[dict]) -> list[dict]:
    return _events_of(events, "heal")


def _last_reresolve_found(events: list[dict]) -> Optional[bool]:
    """The verdict of the most recent identity re-resolution, or
    None when no re-resolution was ever recorded. (Never-heal
    refusals record kind="refused", not a search, and are excluded:
    they carry no absence evidence.)"""
    for ev in reversed(_heals(events)):
        if ev.get("kind") == "reresolve":
            return bool(ev.get("found"))
    return None


def _failure_texts(summary: str, events: list[dict]) -> str:
    """The texts in which a terminal failure describes itself: the
    stop summary, the last failed result, and the last heal
    trigger — the places an occlusion verdict or a target-shaped
    message can appear."""
    parts = [summary or ""]
    for ev in reversed(_events_of(events, "result")):
        if not ev.get("ok"):
            parts.append(str(ev.get("result") or ""))
            break
    heals = _heals(events)
    if heals:
        parts.append(str(heals[-1].get("trigger") or ""))
    return "\n".join(parts)


def _no_target(events: list[dict], reason: str) -> bool:
    """The pinned NO_TARGET definition (see the module docstring)."""
    perceives = _events_of(events, "perceive")
    if not perceives:
        return False
    if any(int(ev.get("elements") or 0) > 0 for ev in perceives):
        return False
    targeted_steps: set = set()
    ok_steps: set = set()
    for ev in events:
        if ev.get("type") == "execute":
            action = ev.get("action") or {}
            if action.get("target") is not None:
                targeted_steps.add(ev.get("step"))
        elif ev.get("type") == "result" and ev.get("ok"):
            ok_steps.add(ev.get("step"))
    if targeted_steps & ok_steps:
        return False  # a targeted action succeeded: the run engaged
    if not targeted_steps and reason != "stuck":
        return False  # never even tried to engage a target
    return True


def _last_signal_label(events: list[dict], summary: str) -> Optional[str]:
    """The last class-naming failure signal in the trail (module
    docstring): heal verdicts name their class directly; a failed
    postcondition names OCCLUDED only with occlusion evidence and
    otherwise defers to earlier signals."""
    texts = _failure_texts(summary, events)
    for ev in reversed(events):
        if ev.get("type") == "heal":
            kind = ev.get("kind")
            if kind == "reresolve":
                return "MOVED" if ev.get("found") else "VANISHED"
            if kind == "refused":
                return "STALE"
            if kind == "revalidate":
                return "MOVED"
        elif ev.get("type") == "postcondition" and not ev.get("ok"):
            if _OCCLUSION_PHRASE in (str(ev.get("note") or "") + "\n" + texts):
                return "OCCLUDED"
            continue
    return None


def _error_label(summary: str, events: list[dict]) -> Optional[str]:
    """The label for an ``error`` stop (rules 7 and 8)."""
    if summary.startswith("verification failed:") or summary.startswith(
        "done claimed 3 times without a passing verify"
    ):
        return "WRONG_ANSWER"
    if summary.startswith("action failed"):
        texts = _failure_texts(summary, events)
        if _OCCLUSION_PHRASE in texts:
            return "OCCLUDED"
        if _last_reresolve_found(events) is False:
            return "VANISHED"
        heals = _heals(events)
        if (heals and heals[-1].get("kind") == "refused") or any(
            phrase in texts for phrase in _TARGET_SHAPED
        ):
            return "STALE"
        return _last_signal_label(events, summary)
    return None


def classify_run(
    reason: str, summary: str, events: list[dict[str, Any]]
) -> tuple[str, Optional[str]]:
    """Classify a finished run from its recorded signals. Pure:
    reads the stop reason, the summary, and the trail events (the
    stop event itself is not among them — the classification is
    recorded ON it). Returns (status, failure_label)."""
    if reason == "done":
        return "SUCCESS", None
    if reason == "denied":
        return "BLOCKED", "BLOCKED"
    if reason == "unverified":
        return "ERROR", "WRONG_ANSWER"
    if reason == "budget" and (summary or "").startswith(_TOKEN_CAP_PREFIX):
        return "TIMEOUT", "COST_CAP"
    if reason in ("budget", "stuck", "error") and _no_target(events, reason):
        return "NO_TARGET", "NO_TARGET"
    if reason == "budget":
        return "TIMEOUT", _last_signal_label(events, summary)
    if reason == "error":
        return "ERROR", _error_label(summary, events)
    if reason == "stuck":
        return "ERROR", _last_signal_label(events, summary)
    return "ERROR", None
