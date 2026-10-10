"""The GhostGuard governor backend: Ghost Hands actions, decided by
GhostGuard.

Opt-in, following the Jev pattern (Bridge Spec v1 —
``ghostguard/docs/bridge-spec-v1.md``). The native :class:`Governor`
stays the default everywhere; this backend is selected only when a
caller builds it directly or sets ``GHOST_HANDS_GOVERNOR_BACKEND=
ghostguard`` and asks for :func:`governor_from_env`.

How it works, per action:

1. The action is classified with the native classifier — Ghost Hands
   keeps its own classes (readonly / write / consequential) for the
   trail, and maps them onto GhostGuard permission classes:
   readonly → ``READ``, write → ``WRITE_LOCAL``, consequential →
   ``NETWORK_WRITE`` — with any non-readonly action whose target or
   URL smells like spending (buy / pay / purchase / checkout / …)
   upgraded to ``SPEND``, so the locked no-spend rule sees it.
2. A gateway intent is built — kind ``ghosthands.<kind>``, the exact
   action dict as payload plus the target's label and the page host
   — and sent to the GhostGuard gate bridge: the same Node script
   Jev spawns (``ghostguard-gate.mjs`` around GhostGuard's shipped
   ``dist/`` build).
3. The bridge evaluates the deployment policy, writes the gateway
   record to the GhostGuard audit (when one is configured) through
   GhostGuard's own recorder, and only then replies —
   record-before-reply, across the process boundary.

Verdict mapping back onto Ghost Hands outcomes: ``allow`` and
``pre_approved`` → allow; ``ask`` → ask (the Runner's approver flow
takes it from there, exactly as with the native governor);
``hand_off`` → deny with the routing target named in the reason —
Ghost Hands has no second approver scope to route to, so a handed-off
action does not execute here; ``deny`` → deny.

Fail-closed, two layers deep: :meth:`GhostGuardGovernor.gate` raises
:class:`GhostGuardBridgeError` when the bridge cannot be reached,
refuses the record, or replies unreadably; :meth:`evaluate` converts
that into a deny Decision, so a broken bridge can never let an
action through the Runner. Silence — a missing bridge, a missing
policy, a dead Node — is a denial, never consent.

Environment (all optional, constructor arguments win):

- ``GHOST_HANDS_GOVERNOR_BACKEND`` — ``native`` (default) |
  ``ghostguard``; read by :func:`governor_from_env`. Unknown values
  are a loud error, never a silent fallback.
- ``GHOSTGUARD_DIR`` — the GhostGuard checkout; default: the
  ``ghostguard`` directory beside this repo in the studio workspace.
- ``GHOST_HANDS_GHOSTGUARD_BRIDGE`` — bridge script override;
  default ``<GHOSTGUARD_DIR>/integrations/jev/ghostguard-gate.mjs``
  (the reference gate from Bridge Spec v1).
- ``GHOST_HANDS_GHOSTGUARD_POLICY`` — path to the base policy JSON.
  Default: the shared conformance policy
  (``<GHOSTGUARD_DIR>/conformance/fixtures.json``, its ``policy``
  document) — one policy for the whole stack, so this backend and
  the TypeScript conformance runner decide from the same document.
- ``GHOST_HANDS_GHOSTGUARD_AUDIT`` — JSONL audit path the bridge
  appends each record to before replying; unset = the bridge returns
  the record without persisting it (the Ghost Hands trail still
  records the govern event, as always).
- ``GHOST_HANDS_GHOSTGUARD_NODE`` — the Node binary; default
  ``node``.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from .actions import Action
from .errors import HandsError
from .eyes import Element
from .governor import ALLOW, ASK, DENY, Decision, Governor, classify

#: Substrings that upgrade a consequential action to GhostGuard's
#: SPEND class: the action is not just state-changing, it commits
#: money. Matched case-insensitively against the target element's
#: name/label/href and the action/page URL — the same haystack the
#: native classifier reads for its consequential keywords.
SPEND_MARKERS = (
    "buy",
    "pay",
    "purchase",
    "checkout",
    "place order",
    "submit order",
    "confirm payment",
    "transfer funds",
)

_PERMISSION_FOR_CLASS = {
    "readonly": "READ",
    "write": "WRITE_LOCAL",
    "consequential": "NETWORK_WRITE",
}

#: GhostGuard verdict → Ghost Hands outcome. hand_off denies here:
#: the record names a different approver/scope, and this host has
#: none to route to — the action does not execute.
_OUTCOME_FOR_VERDICT = {
    "allow": ALLOW,
    "pre_approved": ALLOW,
    "ask": ASK,
    "hand_off": DENY,
    "deny": DENY,
}


class GhostGuardBridgeError(HandsError):
    """The GhostGuard gate could not produce a record.

    Raised by :meth:`GhostGuardGovernor.gate`; caught inside
    :meth:`GhostGuardGovernor.evaluate`, where it becomes a deny —
    no record, no action.
    """


def ghostguard_dir() -> Path:
    """The GhostGuard checkout: ``GHOSTGUARD_DIR``, else the
    ``ghostguard`` directory beside this repo in the workspace
    (this file lives at ``<workspace>/ghost-hands/src/ghost_hands/``).
    """
    override = os.environ.get("GHOSTGUARD_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "ghostguard"


def default_bridge_path() -> Path:
    """The reference gate: ``GHOST_HANDS_GHOSTGUARD_BRIDGE``, else
    the Jev bridge script inside the GhostGuard checkout — Bridge
    Spec v1's reference implementation, shared by every host."""
    override = os.environ.get("GHOST_HANDS_GHOSTGUARD_BRIDGE")
    if override:
        return Path(override)
    return ghostguard_dir() / "integrations" / "jev" / "ghostguard-gate.mjs"


def default_policy_doc() -> dict[str, Any]:
    """The default base policy: the shared conformance policy from
    ``ghostguard/conformance/fixtures.json`` (its ``policy`` field),
    unless ``GHOST_HANDS_GHOSTGUARD_POLICY`` names a policy file
    (a bare policy document, or a fixtures-shaped file with one).
    A missing or unreadable policy file is a loud error — this
    backend never invents a permissive policy to fall back on.
    """
    override = os.environ.get("GHOST_HANDS_GHOSTGUARD_POLICY")
    path = Path(override) if override else ghostguard_dir() / "conformance" / "fixtures.json"
    try:
        data = json.loads(path.read_text(encoding="utf8"))
    except (OSError, ValueError) as exc:
        raise GhostGuardBridgeError(
            f"GhostGuard policy could not be loaded from {path} ({exc}); "
            "set GHOST_HANDS_GHOSTGUARD_POLICY to a policy document"
        ) from None
    if isinstance(data, dict) and isinstance(data.get("rules"), list):
        return data
    if isinstance(data, dict) and isinstance(data.get("policy"), dict):
        return data["policy"]
    raise GhostGuardBridgeError(
        f"GhostGuard policy at {path} is not a policy document "
        "({ rules: [...] }) or a fixtures file carrying one"
    )


def permission_class_for(
    classification: str, action: Action, element: Optional[Element], page_url: str = ""
) -> str:
    """Map a native classification onto a GhostGuard permission
    class, upgrading any non-readonly action that smells like
    spending to ``SPEND`` — a plain "Buy …" button is a native
    ``write``, but at the gateway it is a spend, and the locked
    no-spend rule keys on the class."""
    permission = _PERMISSION_FOR_CLASS[classification]
    if classification != "readonly":
        bits: list[str] = []
        if element is not None:
            bits.extend([element.name or "", element.label or "", element.href or ""])
        bits.extend([action.url or "", page_url or ""])
        haystack = " ".join(bits).lower()
        if any(marker in haystack for marker in SPEND_MARKERS):
            return "SPEND"
    return permission


class GhostGuardGovernor:
    """A Governor-shaped backend that delegates each decision to
    GhostGuard over the gate bridge. Duck-type compatible with the
    native :class:`Governor` where the Runner uses it: ``evaluate``
    and ``classify`` with the same signatures, returning the native
    :class:`Decision`."""

    backend_name = "ghostguard"

    def __init__(
        self,
        policy: Optional[dict[str, Any]] = None,
        policy_overlay: Optional[dict[str, Any]] = None,
        bridge_path: Optional[Path | str] = None,
        audit_path: Optional[str] = None,
        actor: str = "ghost-hands",
        initiator: str = "person",
        project_id: str = "",
        node: Optional[str] = None,
        timeout: float = 15.0,
    ) -> None:
        self.policy = policy if policy is not None else default_policy_doc()
        self.policy_overlay = policy_overlay
        self.bridge_path = (
            Path(bridge_path) if bridge_path is not None else default_bridge_path()
        )
        self.audit_path = (
            audit_path
            if audit_path is not None
            else (os.environ.get("GHOST_HANDS_GHOSTGUARD_AUDIT") or None)
        )
        self.actor = actor
        self.initiator = initiator
        self.project_id = project_id
        self.node = node or os.environ.get("GHOST_HANDS_GHOSTGUARD_NODE") or "node"
        self.timeout = timeout
        self._counter = 0
        #: The last gateway record the bridge returned (or None).
        #: Hosts that want the canonical record — rule id, routing —
        #: beyond the Decision's reason can read it here.
        self.last_record: Optional[dict[str, Any]] = None

    # -- the bridge ----------------------------------------------------

    def gate(
        self,
        intent: dict[str, Any],
        approved: bool = False,
        policy_overlay: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Send one gateway intent to the bridge and return the
        gateway record it replies with — the Bridge Spec v1 client.
        Raises GhostGuardBridgeError on any failure: bridge missing,
        non-zero exit, or an unreadable reply. A record exists only
        when this returns."""
        request = {
            "policy": self.policy,
            "policy_overlay": (
                policy_overlay if policy_overlay is not None else self.policy_overlay
            ),
            "audit_path": self.audit_path,
            "approved": approved is True,
            "intent": intent,
        }
        try:
            proc = subprocess.run(
                [self.node, str(self.bridge_path)],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GhostGuardBridgeError(
                f"GhostGuard bridge could not be reached ({exc}); no action executed"
            ) from None
        if proc.returncode != 0:
            lines = proc.stderr.strip().splitlines()
            detail = lines[-1] if lines else f"exit {proc.returncode}"
            raise GhostGuardBridgeError(
                f"GhostGuard bridge refused the record ({detail}); no action executed"
            )
        try:
            record = json.loads(proc.stdout)
        except ValueError:
            raise GhostGuardBridgeError(
                "GhostGuard bridge returned an unreadable record; no action executed"
            ) from None
        if not isinstance(record, dict) or "verdict" not in record:
            raise GhostGuardBridgeError(
                "GhostGuard bridge returned an unreadable record; no action executed"
            )
        self.last_record = record
        return record

    # -- the Governor shape ---------------------------------------------

    def classify(
        self, action: Action, element: Optional[Element] = None, page_url: str = ""
    ) -> str:
        return classify(action, element, page_url)

    def build_intent(
        self, action: Action, element: Optional[Element] = None, page_url: str = ""
    ) -> dict[str, Any]:
        """The gateway intent for one Ghost Hands action: the exact
        action dict as payload, plus the target label and page host
        policy rules read (``payload.label`` / ``payload.host``)."""
        classification = self.classify(action, element, page_url)
        permission = permission_class_for(classification, action, element, page_url)
        host = urlparse(action.url or page_url or "").hostname or ""
        label = ""
        if element is not None:
            label = element.name or element.label or ""
        payload: dict[str, Any] = {
            **action.to_dict(),
            "label": label,
            "host": host.lower(),
        }
        self._counter += 1
        return {
            "action_id": f"gh-{self._counter}",
            "kind": f"ghosthands.{action.kind}",
            "permission_class": permission,
            "actor": self.actor,
            "initiator": self.initiator,
            "project_id": self.project_id,
            "action_payload": payload,
            "target_ref": None,
            "snapshot_id": None,
            "target": (
                element.descriptor() if element is not None else None
            ),
            "dry_run": False,
        }

    def evaluate(
        self, action: Action, element: Optional[Element] = None, page_url: str = ""
    ) -> Decision:
        classification = self.classify(action, element, page_url)
        intent = self.build_intent(action, element, page_url)
        try:
            record = self.gate(intent)
        except GhostGuardBridgeError as exc:
            # Fail closed: a bridge that cannot produce a record
            # decides nothing — the action is denied, and the Ghost
            # Hands trail records this govern event as usual.
            return Decision(classification, DENY, f"ghostguard: {exc}")
        verdict = str(record.get("verdict"))
        outcome = _OUTCOME_FOR_VERDICT.get(verdict, DENY)
        rule = record.get("rule_id") or "(fail-closed default)"
        if verdict == "pre_approved":
            reason = f"ghostguard: pre_approved by policy rule \"{rule}\" (standing approval)"
        elif verdict == "hand_off":
            reason = (
                f"ghostguard: handed off to \"{record.get('handoff_to')}\" "
                f"by policy rule \"{rule}\" — not executed by Ghost Hands"
            )
        elif verdict == "ask":
            reason = f"ghostguard: policy rule \"{rule}\" requires human approval (ask)"
        elif verdict == "allow":
            reason = f"ghostguard: allowed by policy rule \"{rule}\""
        else:
            reason = f"ghostguard: denied by policy rule \"{rule}\""
        return Decision(classification, outcome, reason)


_BACKENDS = ("native", "ghostguard")


def governor_from_env(env: Optional[dict[str, str]] = None) -> Governor | GhostGuardGovernor:
    """Build the governor the environment selects.

    ``GHOST_HANDS_GOVERNOR_BACKEND``: ``native`` (the default — the
    in-repo Governor, unchanged behaviour) or ``ghostguard`` (this
    backend). Unknown values raise HandsError loudly, mirroring
    Jev's ``JEV_GOVERNOR_BACKEND`` stance: a misspelled backend is a
    configuration error, never a silent fall back to a governor the
    operator did not choose."""
    environ = os.environ if env is None else env
    raw = (environ.get("GHOST_HANDS_GOVERNOR_BACKEND") or "native").strip().lower()
    if raw == "native":
        return Governor()
    if raw == "ghostguard":
        return GhostGuardGovernor()
    raise HandsError(
        f"unknown GHOST_HANDS_GOVERNOR_BACKEND {raw!r}; "
        f"use one of: {', '.join(_BACKENDS)}"
    )
