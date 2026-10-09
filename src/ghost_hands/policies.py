"""Policy packs: named, loadable bundles of Governor policy.

A pack is data, not code — each pack names the Governor outcome
(allow / ask / deny) for every action class, an approval timeout (how
long an approver channel may take before the answer counts as a deny),
and optional domain-allowlist additions merged on top of the base
policy. Three packs ship with Ghost Hands:

- ``readonly`` — perception only. Writes and consequential actions are
  denied outright; nothing that changes state ever runs.
- ``standard`` — the long-standing default: readonly + write allowed,
  consequential asks a human.
- ``strict`` — writes ask too, and consequential approvals run on a
  shorter leash (120s instead of 600s): a slow "yes" is a "no".

Unknown pack names are a loud error that lists the known packs —
never a silent fallback to a permissive policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .errors import HandsError
from .governor import ALLOW, ASK, DENY, Policy

DEFAULT_APPROVAL_TIMEOUT = 600.0


@dataclass(frozen=True)
class PolicyPack:
    """A resolved pack: the Governor policy plus channel settings."""

    name: str
    description: str
    policy: Policy
    approval_timeout: float = DEFAULT_APPROVAL_TIMEOUT

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "policy": self.policy.to_dict(),
            "approval_timeout": self.approval_timeout,
        }


# Pack definitions, as data. ``allowed_domains`` here are ADDITIONS:
# they merge into (never replace) whatever allowlist the base policy
# already carries.
_PACK_DEFS: dict[str, dict[str, Any]] = {
    "readonly": {
        "description": (
            "Perception only: readonly actions allowed; writes and "
            "consequential actions denied outright."
        ),
        "readonly": ALLOW,
        "write": DENY,
        "consequential": DENY,
        "approval_timeout": 0.0,
        "allowed_domains": (),
    },
    "standard": {
        "description": (
            "The default Ghost Hands posture: readonly and write actions "
            "allowed; consequential actions ask a human (600s to answer)."
        ),
        "readonly": ALLOW,
        "write": ALLOW,
        "consequential": ASK,
        "approval_timeout": DEFAULT_APPROVAL_TIMEOUT,
        "allowed_domains": (),
    },
    "strict": {
        "description": (
            "Writes ask too, and approvals run on a short leash: "
            "consequential actions ask with a 120s timeout."
        ),
        "readonly": ALLOW,
        "write": ASK,
        "consequential": ASK,
        "approval_timeout": 120.0,
        "allowed_domains": (),
    },
}

PACK_NAMES = tuple(_PACK_DEFS)


def load_pack(name: str, base: Optional[Policy] = None) -> PolicyPack:
    """Resolve a pack name to a PolicyPack.

    ``base`` supplies the domains a deployment already configured: the
    pack's class outcomes replace the base policy's, blocked domains
    carry over untouched, and the pack's allowlist additions merge into
    the base allowlist. Raises HandsError on an unknown name — loudly.
    """
    key = (name or "").strip().lower()
    if key not in _PACK_DEFS:
        known = ", ".join(sorted(_PACK_DEFS))
        raise HandsError(
            f"unknown policy pack {name!r}; known packs: {known}"
        )
    spec = _PACK_DEFS[key]
    base = base or Policy()
    allowed = tuple(
        dict.fromkeys(
            tuple(base.allowed_domains) + tuple(spec["allowed_domains"])
        )
    )
    policy = Policy(
        readonly=spec["readonly"],
        write=spec["write"],
        consequential=spec["consequential"],
        allowed_domains=allowed,
        blocked_domains=tuple(base.blocked_domains),
    )
    return PolicyPack(
        name=key,
        description=spec["description"],
        policy=policy,
        approval_timeout=float(spec["approval_timeout"]),
    )


def pack_names() -> list[str]:
    return sorted(_PACK_DEFS)


def describe_packs() -> list[dict[str, Any]]:
    return [load_pack(name).to_dict() for name in pack_names()]
