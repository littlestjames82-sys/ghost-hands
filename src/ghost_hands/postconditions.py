"""Per-action postconditions (v1.16, roadmap P1-8).

Every wave so far taught the run to *see* better: heal (v1.12)
re-finds a moved target, effect feedback (v1.13) renders what a
field now holds, content visibility (v1.14) shows the page's
text, and the validator (v1.15) judges a done claim against a
checklist. What none of them did is check, in code, right after
an action executes, whether the effect the action *exists to
produce* actually happened. Skyvern's motivating failure is a
click that silently no-ops — their cart example — discovered
steps later or never; Stagehand's house rule is the companion
discipline: "never retry act() — retry observe()".

This module is that check. From the action and the pre-action
perception the Runner derives a deterministic **expectation**
(no model is consulted, ever), and after the action executes —
the heal chain fully resolved, so the verdict judges the NET
outcome, never an intermediate stale attempt — the expectation
is compared in code against a fresh perception. The verdict is
a hash-chained ``postcondition`` trail event, and a failure is
fed back immediately: a specific note in the decider's history
("click on [3] 'Add to cart' produced no observable change"),
plus a one-shot blind-retry refusal — if the decider's very
next action repeats the failed (kind, target) signature
exactly, the Runner refuses that one repetition with an
explanatory result instead of executing it. Runner-internal
heals are not decider retries and are never refused; the retry
the decider chooses after re-observing travels the ordinary
govern/approve path, exactly like any other action.

The expectation table (derived per action kind; anything not
listed has NO derivable expectation and is skipped — no fake
checks):

- ``type`` / ``select`` on a field → the field's post-action
  rendered value equals the typed/chosen value (v1.13's
  rendering makes this a code comparison). A masked field's
  value is never compared or recorded: the marker's own
  presence+length disclosure is the check (the marker length
  must equal the typed length). A secret-placeholder action
  degrades to presence-only — a value is there, its content
  and length are never examined. A password field's value is
  never perceivable (v1.13's absolute rule), so typing into
  one carries no checkable expectation and is skipped.
- ``fill_form`` → each filled field re-found on the post page
  holds its value (same value rules, per field). With
  ``submit=True`` the form leaves the page by design, so the
  expectation degrades to the generic change check below.
- ``click`` on a link → the URL becomes the href resolved
  against the pre-action URL (a fragment-only href degrades
  to the change check: an anchor jump need not navigate).
- ``click`` on a checkbox → its rendered state toggles;
  on a radio → it ends checked. (The vocabulary has no
  check/uncheck kinds; the click IS the checking gesture.)
  When the body does not report the control's state, the
  expectation degrades to the change check.
- ``click`` on anything else (and ``fill_form`` submits) →
  something observable happened: the URL or the map
  signature changed. A click after which NOTHING changed is
  precisely the silent no-op this wave exists to catch.
- ``navigate`` → the resulting URL is the target resolved
  against the pre-action URL (one trailing slash of
  difference is the same landing).

Skipped, with reasons: ``extract`` / ``verify`` / ``wait`` /
``screenshot`` / ``pdf`` are observations — a wait's result
already *is* its condition check, and a download's own trail
event + result are its evidence (``download`` skipped for the
same reason). ``press`` has unbounded effects (Enter may
submit, Escape may close) — no single expectation is
derivable. ``scroll`` position is not in the map, and a
scroll at the page edge legitimately changes nothing.
``hover`` / ``double_click`` / ``right_click`` / ``drag``
are transient or compound gestures with no deterministic
rendered end-state. ``click_at`` carries no element identity
to bind an expectation (or a refusal) to. ``set_file``'s
input value is a body-private fake path; the driver's result
and the never-heal discipline are its record. ``set_viewport``
changes driver state, not page state.

Activation is opt-in, following the require-verify (v1.2) and
validator (v1.15) precedent: ``Runner(postconditions=True)``, or
``GHOST_HANDS_POSTCONDITIONS=1``. Default-on was tried first —
the checks are pure code with no model cost — but an existing
test (test_v115_validator's activation-off pin) fixes the exact
trail event sequence of a default run, and the standing rule
for every wave is that a default run's record does not change
unless the wave is switched on. So: off, nothing changes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin

from .actions import Action
from .eyes import Element, ElementMap, field_values_mode
from .secrets import PLACEHOLDER_RE, action_secret_refs

# The v1.13 masked-value marker, parsed for its sanctioned
# disclosure (presence + character count). The format is defined
# in eyes.mask_field_value; the count is all this module reads.
_MASKED_RE = re.compile(r"\[masked · (\d+) chars\]")

_TRUTHY = ("1", "true", "yes", "on")


def postconditions_enabled(flag: Optional[bool] = None) -> bool:
    """The activation surface: an explicit Runner argument wins,
    else GHOST_HANDS_POSTCONDITIONS must carry a truthy value
    (``1``/``true``/``yes``/``on``) — unset or anything else is
    off, the opt-in default."""
    if flag is not None:
        return bool(flag)
    raw = (os.environ.get("GHOST_HANDS_POSTCONDITIONS") or "").strip().lower()
    return raw in _TRUTHY


# ---------------------------------------------------------------------------
# Expectation + outcome shapes
# ---------------------------------------------------------------------------


@dataclass
class Expectation:
    """One action's expected observable effect. Derived BEFORE the
    check runs (from the final, post-heal action and its element),
    compared after execution. ``descriptor`` re-finds the target in
    the post-action map by identity, never by number alone."""

    kind: str  # value | value_present | change | url | state | fields
    describe: str  # 'click on [3] "Add to cart"' — for notes/events
    target: Optional[int] = None
    descriptor: Optional[dict] = None
    expected: Optional[str] = None  # value / url expectations
    secret: bool = False  # the action bears a secret placeholder
    want_checked: Optional[bool] = None  # state expectation
    fields: list = field(default_factory=list)  # fields: (key, value, secret)


@dataclass
class PostconditionOutcome:
    """The code verdict on one expectation: pass/fail plus a short
    evidence note. Notes are masked-safe by construction — for a
    masked or secret-bearing field they carry presence and counts
    only, never content."""

    ok: bool
    note: str


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


def _describe(action: Action, element: Optional[Element]) -> str:
    name = f' "{element.name}"' if element is not None and element.name else ""
    where = f"[{action.target}]" if action.target is not None else ""
    return f"{action.kind} on {where}{name}".replace("  ", " ").strip()


def derive_expectation(
    action: Action, element: Optional[Element], *, pre_url: str
) -> Optional[Expectation]:
    """The expectation an action carries, or None when its kind has
    no derivable one (see the module docstring's table). ``element``
    is the action's target in the pre-action map (the final,
    post-heal element when a heal re-pointed the action)."""
    secret = bool(action_secret_refs(action))
    describe = _describe(action, element)
    if action.kind in ("type", "select"):
        if element is None:
            return None
        if secret:
            return Expectation(
                "value_present",
                describe,
                target=action.target,
                descriptor=element.descriptor(),
                secret=True,
            )
        if (element.type or "").lower() == "password":
            # A password's content is never perceivable (v1.13's
            # absolute rule), so there is nothing code can compare.
            return None
        expected = action.text if action.kind == "type" else action.value
        return Expectation(
            "value",
            describe,
            target=action.target,
            descriptor=element.descriptor(),
            expected=expected or "",
        )
    if action.kind == "fill_form":
        if action.submit:
            # The form leaves the page by design; per-field checks
            # would chase a page that moved on. What must hold is
            # that the submission visibly did something.
            return Expectation("change", describe, target=action.target)
        fields = [
            (
                str(key),
                str(value),
                bool(PLACEHOLDER_RE.search(str(value))),
            )
            for key, value in (action.fields or {}).items()
        ]
        if not fields:
            return None
        return Expectation("fields", describe, target=action.target, fields=fields)
    if action.kind == "click":
        if element is None:
            return None
        descriptor = element.descriptor()
        is_link = element.tag == "a" or element.role == "link"
        if is_link and element.href:
            if element.href.startswith("#"):
                return Expectation(
                    "change", describe, target=action.target, descriptor=descriptor
                )
            return Expectation(
                "url",
                describe,
                target=action.target,
                descriptor=descriptor,
                expected=urljoin(pre_url, element.href),
            )
        itype = (element.type or "").lower()
        if itype in ("checkbox", "radio") or element.role in ("checkbox", "radio"):
            if element.checked is None:
                # The body does not report state; the generic
                # observable-change check is what remains.
                return Expectation(
                    "change", describe, target=action.target, descriptor=descriptor
                )
            return Expectation(
                "state",
                describe,
                target=action.target,
                descriptor=descriptor,
                want_checked=True if itype == "radio" else not element.checked,
            )
        return Expectation(
            "change", describe, target=action.target, descriptor=descriptor
        )
    if action.kind == "navigate":
        if not action.url:
            return None
        return Expectation(
            "url", describe, expected=urljoin(pre_url, action.url)
        )
    # Every other kind: no derivable expectation (module docstring).
    return None


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------


def _short(value: str, limit: int = 40) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _find(post_map: ElementMap, descriptor: Optional[dict]) -> Optional[Element]:
    """Re-find an element in the post-action map by its recorded
    identity (tag + role + name, with id and nth as tiebreaks) —
    the number alone is never trusted, since the action itself
    may have re-rendered the page."""
    if not descriptor:
        return None
    wanted = (
        descriptor.get("tag", ""),
        descriptor.get("role", ""),
        descriptor.get("name", ""),
    )
    matches = [el for el in post_map.elements if el.key() == wanted]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    if descriptor.get("id"):
        exact = [el for el in matches if el.id == descriptor["id"]]
        if len(exact) == 1:
            return exact[0]
    if descriptor.get("nth") is not None:
        exact = [el for el in matches if el.nth == descriptor["nth"]]
        if len(exact) == 1:
            return exact[0]
    return matches[0]


def _find_field(post_map: ElementMap, key: str) -> Optional[Element]:
    """Find a filled field in the post-action map by its fill_form
    key — the same words the drivers match on (label, name,
    placeholder, aria-label, id, element name)."""
    needle = key.strip().lower()
    if not needle:
        return None
    for el in post_map.elements:
        if el.tag not in ("input", "textarea", "select"):
            continue
        candidates = [
            el.label or "",
            el.attr_name or "",
            el.placeholder or "",
            el.attrs.get("aria-label", ""),
            el.id or "",
            el.name or "",
        ]
        if any(
            c and (c.strip().lower() == needle or needle in c.strip().lower())
            for c in candidates
        ):
            return el
    return None


def _same_url(a: str, b: str) -> bool:
    """URL equality with one trailing slash of tolerance — the
    same landing spelled two ways is the same landing."""
    return a == b or a.rstrip("/") == b.rstrip("/")


def _changed(pre_url: str, pre_signature: str, post_map: ElementMap, post_url: str) -> bool:
    return post_url != pre_url or post_map.signature() != pre_signature


def _value_outcome(
    expected: str,
    secret: bool,
    post_el: Optional[Element],
    *,
    pre_url: str,
    pre_signature: str,
    post_map: ElementMap,
    post_url: str,
) -> Optional[PostconditionOutcome]:
    """The value verdict for one field. None means the field's
    post state is not perceivable (a password's content) — the
    check abstains rather than guessing."""
    if post_el is None:
        moved_on = _changed(pre_url, pre_signature, post_map, post_url)
        return PostconditionOutcome(
            moved_on,
            "the field is no longer on the page after the action"
            + (
                " (and the page changed — it moved on)"
                if moved_on
                else " and nothing else changed either"
            ),
        )
    observed = post_el.value
    if observed is None and field_values_mode() == "off":
        # The operator disabled field-value perception (v1.13's
        # knob): the evidence channel this check reads is off, so
        # the check abstains rather than failing every action.
        return None
    if secret:
        if observed:
            return PostconditionOutcome(
                True,
                "a value is present in the field "
                "(content not examined: secret placeholder)",
            )
        if (post_el.type or "").lower() == "password":
            # A password never renders a value, so presence is
            # unjudgeable from perception — abstain.
            return None
        return PostconditionOutcome(
            False, "no value is present in the field after the action"
        )
    if expected == "":
        if observed in (None, ""):
            return PostconditionOutcome(True, "the field is empty, as expected")
        return PostconditionOutcome(
            False,
            f"expected the field to be empty; it holds {_short(observed)!r}",
        )
    if observed is None:
        return PostconditionOutcome(
            False, "the field holds no value after the action"
        )
    marker = _MASKED_RE.fullmatch(observed)
    if marker:
        shown = int(marker.group(1))
        return PostconditionOutcome(
            shown == len(expected),
            f"masked value present ({shown} chars)"
            if shown == len(expected)
            else f"expected a masked value of {len(expected)} chars; "
            f"the field shows one of {shown} chars",
        )
    return PostconditionOutcome(
        observed == expected,
        f"the field now holds {_short(observed)!r}"
        if observed == expected
        else f"expected the field to hold {_short(expected)!r}; "
        f"it holds {_short(observed)!r}",
    )


def check_expectation(
    expectation: Expectation,
    *,
    pre_url: str,
    pre_signature: str,
    post_map: ElementMap,
    post_url: str,
) -> Optional[PostconditionOutcome]:
    """Judge one expectation against the post-action perception.
    None means the check abstains (nothing measurable) — the
    Runner records no event for an abstention."""
    kind = expectation.kind
    if kind == "change":
        if _changed(pre_url, pre_signature, post_map, post_url):
            return PostconditionOutcome(
                True, f"something observable changed after the {expectation.describe}"
            )
        return PostconditionOutcome(
            False,
            f"{expectation.describe} produced no observable change: "
            "the URL and the element map are exactly as they were",
        )
    if kind == "url":
        expected = expectation.expected or ""
        if _same_url(post_url, expected):
            return PostconditionOutcome(True, f"the page is at {post_url}")
        return PostconditionOutcome(
            False,
            f"expected to land on {expected}; the page is at {post_url}",
        )
    if kind == "state":
        post_el = _find(post_map, expectation.descriptor)
        if post_el is None or post_el.checked is None:
            # State became unmeasurable mid-flight; the generic
            # change verdict is the honest fallback.
            changed = _changed(pre_url, pre_signature, post_map, post_url)
            return PostconditionOutcome(
                changed,
                "the control's state is not reported after the action; "
                + (
                    "the page changed"
                    if changed
                    else "and the page did not change either"
                ),
            )
        want = bool(expectation.want_checked)
        return PostconditionOutcome(
            post_el.checked == want,
            f"the control is now {'checked' if post_el.checked else 'unchecked'}"
            if post_el.checked == want
            else f"expected the control to become "
            f"{'checked' if want else 'unchecked'}; it is still "
            f"{'checked' if post_el.checked else 'unchecked'}",
        )
    if kind == "value_present":
        return _value_outcome(
            "",
            True,
            _find(post_map, expectation.descriptor),
            pre_url=pre_url,
            pre_signature=pre_signature,
            post_map=post_map,
            post_url=post_url,
        )
    if kind == "value":
        return _value_outcome(
            expectation.expected or "",
            False,
            _find(post_map, expectation.descriptor),
            pre_url=pre_url,
            pre_signature=pre_signature,
            post_map=post_map,
            post_url=post_url,
        )
    if kind == "fields":
        checked = 0
        failures: list[str] = []
        for key, value, secret in expectation.fields:
            post_el = _find_field(post_map, key)
            if post_el is None:
                continue
            outcome = _value_outcome(
                value,
                secret,
                post_el,
                pre_url=pre_url,
                pre_signature=pre_signature,
                post_map=post_map,
                post_url=post_url,
            )
            if outcome is None:
                continue  # unperceivable field (a password) — abstain
            checked += 1
            if not outcome.ok:
                failures.append(f"field {key!r}: {outcome.note}")
        if failures:
            return PostconditionOutcome(False, "; ".join(failures))
        if checked:
            return PostconditionOutcome(
                True, f"all {checked} filled field(s) hold their values"
            )
        moved_on = _changed(pre_url, pre_signature, post_map, post_url)
        return PostconditionOutcome(
            moved_on,
            "none of the filled fields are on the page anymore"
            + (
                " (and the page changed — it moved on)"
                if moved_on
                else " and nothing else changed either"
            ),
        )
    return None
