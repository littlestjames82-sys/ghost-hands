"""Secret placeholders: the model never sees a credential.

A decider (or a script, or a replayed plan) may write a credential as a
placeholder — ``{{secret:NAME}}`` — inside a ``type`` action's text, a
``select`` action's value, or a ``fill_form`` field value. The placeholder
is all the decider, the Governor, the approver, and the trail ever see.

Resolution happens exactly once, at the execution boundary inside the
Runner (and the MCP session): the placeholder is replaced with the real
value from the environment — ``GHOST_HANDS_SECRET_<NAME>`` — or from an
in-memory mapping handed to the Runner by the host program. The resolved
action is what the driver receives, and nothing else: driver results are
scrubbed of every resolved value before they are recorded or returned.

A missing secret is an honest error, never a fallback to the literal
placeholder text: typing ``{{secret:PASSWORD}}`` into a login form would
be both broken and a leak of the naming scheme.

This is the local, zero-cloud answer to the hosted credential vaults the
hosted browser-agent platforms sell: no vault service, no account, the
secret never leaves the machine's environment, and the provenance trail
*proves* it was never in the record.
"""

from __future__ import annotations

import os
import re
from typing import Mapping, Optional

from .actions import Action

PLACEHOLDER_RE = re.compile(r"\{\{secret:([A-Za-z0-9_.\-]+)\}\}")

ENV_PREFIX = "GHOST_HANDS_SECRET_"

REDACTION = "••••"


def _env_key(name: str) -> str:
    return ENV_PREFIX + name.upper().replace("-", "_").replace(".", "_")


class SecretProvider:
    """Resolves secret names to values: an explicit in-memory mapping
    first, then the ``GHOST_HANDS_SECRET_*`` environment. Values are
    never written anywhere by this class."""

    def __init__(
        self,
        values: Optional[Mapping[str, str]] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._values = {str(k).upper(): str(v) for k, v in (values or {}).items()}
        self._env = os.environ if env is None else env

    def get(self, name: str) -> Optional[str]:
        if name.upper() in self._values:
            return self._values[name.upper()]
        value = self._env.get(_env_key(name))
        return value if value else None


def action_secret_refs(action: Action) -> list[str]:
    """The secret names an action references, in first-seen order."""
    refs: list[str] = []

    def _scan(text: Optional[str]) -> None:
        if not text:
            return
        for match in PLACEHOLDER_RE.finditer(str(text)):
            name = match.group(1)
            if name not in refs:
                refs.append(name)

    _scan(action.text)
    _scan(action.value)
    if action.fields:
        for value in action.fields.values():
            _scan(value if isinstance(value, str) else None)
    return refs


def resolve_action(
    action: Action, provider: SecretProvider
) -> tuple[Action, list[str], list[str]]:
    """Return (resolved action, resolved values, missing names).

    The input action is never mutated. When a name is missing the
    returned action still carries its placeholder for that name, and
    the caller must refuse to execute it.
    """
    values: list[str] = []
    missing: list[str] = []

    def _sub(text: Optional[str]) -> Optional[str]:
        if text is None:
            return None

        def _replace(match: re.Match) -> str:
            name = match.group(1)
            value = provider.get(name)
            if value is None:
                if name not in missing:
                    missing.append(name)
                return match.group(0)
            if value not in values:
                values.append(value)
            return value

        return PLACEHOLDER_RE.sub(_replace, str(text))

    data = action.to_dict()
    if "text" in data:
        data["text"] = _sub(data["text"])
    if "value" in data:
        data["value"] = _sub(data["value"])
    if data.get("fields"):
        data["fields"] = {
            key: (_sub(val) if isinstance(val, str) else val)
            for key, val in data["fields"].items()
        }
    return Action.from_dict(data), values, missing


def scrub_text(text: str, values: list[str]) -> str:
    """Replace every resolved secret value in a driver result with the
    redaction marker, so a page that echoes a typed value back cannot
    smuggle the credential into the trail or a decider's next prompt."""
    out = text or ""
    for value in values:
        if value:
            out = out.replace(value, REDACTION)
    return out
