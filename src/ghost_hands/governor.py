"""The Governor: every action is classified and judged before it runs.

Classes:
- ``readonly``     — perceives or moves without changing anything
                     (navigate, extract, screenshot, scroll, wait, press,
                     hover, pdf, set_viewport, clicking a plain link).
- ``write``        — changes state in a reversible, ordinary way
                     (typing into a field, selecting an option, clicking a
                     plain button, starting a download, setting a file,
                     filling a form, dragging, a raw-coordinate click).
- ``consequential``— form submits (including fill_form with submit=True),
                     and any click/type/select/navigate whose target or URL
                     smells like money, messaging, destruction, or
                     publishing (pay, purchase, checkout, place order,
                     send, delete, publish, transfer, buy now…).

A Policy maps each class to allow / ask / deny and carries a domain
allowlist (empty = all allowed) plus blocked domains. The Runner records
the Governor's verdict to the trail *before* executing — a denied or
unapproved action never runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from .actions import Action
from .eyes import Element

READONLY = "readonly"
WRITE = "write"
CONSEQUENTIAL = "consequential"
CLASSES = (READONLY, WRITE, CONSEQUENTIAL)

ALLOW = "allow"
ASK = "ask"
DENY = "deny"
OUTCOMES = (ALLOW, ASK, DENY)

CONSEQUENTIAL_KEYWORDS = (
    "pay",
    "purchase",
    "buy now",
    "checkout",
    "place order",
    "submit order",
    "confirm payment",
    "transfer",
    "send",
    "delete",
    "remove account",
    "publish",
)


def _hostname(url: Optional[str]) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return (parsed.hostname or "").lower()


def _matches_keyword(haystack: str) -> Optional[str]:
        for kw in CONSEQUENTIAL_KEYWORDS:
            if kw in haystack:
                return kw
        return None


def classify(
    action: Action, element: Optional[Element] = None, page_url: str = ""
) -> str:
    """Classify an action given its target element and current page URL."""
    kind = action.kind
    if kind in (
        "done",
        "extract",
        "screenshot",
        "wait",
        "scroll",
        "press",
        # v0.3 readonly additions: observing or re-framing the page.
        "hover",
        "pdf",
        "set_viewport",
    ):
        return READONLY

    target_bits: list[str] = []
    if element is not None:
        target_bits.append(element.name or "")
        if element.href:
            target_bits.append(element.href)
        if element.label:
            target_bits.append(element.label)
    if action.url:
        target_bits.append(action.url)
    if action.fields:
        target_bits.extend(str(k) for k in action.fields.keys())
    if action.path:
        target_bits.append(action.path)
    haystack = " ".join(target_bits).lower()

    if kind == "navigate":
        kw = _matches_keyword(haystack)
        return CONSEQUENTIAL if kw else READONLY

    if kind in ("click", "double_click"):
        if element is None:
            return WRITE
        kw = _matches_keyword(haystack)
        if kw:
            return CONSEQUENTIAL
        # Form submits are consequential by definition.
        if element.type == "submit" or element.form_action:
            return CONSEQUENTIAL
        if element.tag == "a":
            return READONLY
        return WRITE

    if kind == "right_click":
        # Opening a context menu changes nothing by itself; a menu named
        # for money/destruction still escalates.
        kw = _matches_keyword(haystack)
        return CONSEQUENTIAL if kw else READONLY

    if kind in ("type", "select", "set_file"):
        kw = _matches_keyword(haystack)
        return CONSEQUENTIAL if kw else WRITE

    if kind == "fill_form":
        # Filling is a write; submitting from the same action — or aiming
        # at a consequential-sounding field — makes it consequential.
        if action.submit:
            return CONSEQUENTIAL
        kw = _matches_keyword(haystack)
        return CONSEQUENTIAL if kw else WRITE

    if kind == "download":
        # Starting a download changes local state (a file lands on disk)
        # but is ordinary and reversible: a write.
        return WRITE

    if kind in ("drag", "click_at"):
        kw = _matches_keyword(haystack)
        return CONSEQUENTIAL if kw else WRITE

    return WRITE


@dataclass
class Policy:
    readonly: str = ALLOW
    write: str = ALLOW
    consequential: str = ASK
    allowed_domains: tuple[str, ...] = ()
    blocked_domains: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("readonly", "write", "consequential"):
            val = getattr(self, name)
            if val not in OUTCOMES:
                raise ValueError(f"policy {name} must be one of {OUTCOMES}, got {val!r}")
        self.allowed_domains = tuple(d.lower() for d in self.allowed_domains)
        self.blocked_domains = tuple(d.lower() for d in self.blocked_domains)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Policy":
        return cls(
            readonly=data.get("readonly", ALLOW),
            write=data.get("write", ALLOW),
            consequential=data.get("consequential", ASK),
            allowed_domains=tuple(data.get("allowed_domains", ())),
            blocked_domains=tuple(data.get("blocked_domains", ())),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "readonly": self.readonly,
            "write": self.write,
            "consequential": self.consequential,
            "allowed_domains": list(self.allowed_domains),
            "blocked_domains": list(self.blocked_domains),
        }

    def for_class(self, classification: str) -> str:
        return getattr(self, classification)


@dataclass
class Decision:
    classification: str
    outcome: str  # allow | ask | deny
    reason: str

    @property
    def allowed(self) -> bool:
        return self.outcome == ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.outcome == ASK


class Governor:
    def __init__(self, policy: Optional[Policy] = None) -> None:
        self.policy = policy or Policy()

    def classify(
        self, action: Action, element: Optional[Element] = None, page_url: str = ""
    ) -> str:
        return classify(action, element, page_url)

    def evaluate(
        self, action: Action, element: Optional[Element] = None, page_url: str = ""
    ) -> Decision:
        classification = self.classify(action, element, page_url)
        policy = self.policy

        effective_url = action.url if action.kind == "navigate" else page_url
        host = _hostname(effective_url)
        if host:
            if host in policy.blocked_domains:
                return Decision(
                    classification,
                    DENY,
                    f"domain {host} is blocked by policy",
                )
            if policy.allowed_domains and host not in policy.allowed_domains:
                return Decision(
                    classification,
                    DENY,
                    f"domain {host} is not in the policy allowlist",
                )

        outcome = policy.for_class(classification)
        if outcome == ALLOW:
            reason = f"{classification} actions are allowed by policy"
        elif outcome == ASK:
            reason = f"{classification} actions require approval by policy"
        else:
            reason = f"{classification} actions are denied by policy"
        return Decision(classification, outcome, reason)
