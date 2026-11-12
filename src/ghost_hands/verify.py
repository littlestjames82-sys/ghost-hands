"""The ``verify`` action: prove it, don't claim it.

The most common browser-agent failure is not a failed click — it is a
confident ``done`` for a task that never happened. Ghost Hands answers
that with a first-class, readonly assertion action a decider or script
can place before ``done``:

- ``target`` — an element with that number exists on the current page;
- ``text`` — the text appears in the page (title, element names, or the
  rendered body text), case-insensitively, whitespace-normalized;
- ``url`` — the current URL contains the given substring;
- ``absent_text`` (v1.5) — the text does NOT appear in the page:
  prove the error is gone, not just that the success showed up.

Every condition that is set must hold. Verification is evaluated by the
Runner (and the MCP session) against the live perception — drivers
never see a ``verify`` action, exactly as they never see ``done``.
A failed verification is an honest ``error`` stop with the failed
condition named, recorded in the trail like every other result.
"""

from __future__ import annotations

import html as _html
import re
from typing import Any

from .actions import Action
from .eyes import ElementMap


def _normalize(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _page_text(driver: Any, element_map: ElementMap) -> str:
    parts: list[str] = [element_map.title or ""]
    for el in element_map.elements:
        for value in (el.name, el.label, el.placeholder, el.value):
            if value:
                parts.append(str(value))
    snapshot = getattr(driver, "snapshot_html", None)
    if callable(snapshot):
        try:
            raw = snapshot() or ""
        except Exception:
            raw = ""
        raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
        raw = re.sub(r"(?s)<[^>]+>", " ", raw)
        parts.append(_html.unescape(raw))
    return _normalize(" ".join(parts))


def evaluate_verify(
    action: Action, element_map: ElementMap, driver: Any
) -> tuple[bool, str]:
    """Evaluate a verify action's conditions. Returns (ok, message)."""
    checks: list[str] = []
    failures: list[str] = []

    if action.target is not None:
        if element_map.get(action.target) is not None:
            checks.append(f"element [{action.target}] present")
        else:
            failures.append(f"element [{action.target}] is not on the page")

    if action.text:
        if _normalize(action.text) in _page_text(driver, element_map):
            checks.append(f"text {action.text!r} present")
        else:
            failures.append(f"text {action.text!r} not found on the page")

    if action.absent_text:
        if _normalize(action.absent_text) not in _page_text(driver, element_map):
            checks.append(f"text {action.absent_text!r} absent")
        else:
            failures.append(
                f"text {action.absent_text!r} is still on the page"
            )

    if action.url:
        try:
            current = driver.current_url() or ""
        except Exception:
            current = ""
        current = current or element_map.url or ""
        if action.url in current:
            checks.append(f"url contains {action.url!r}")
        else:
            failures.append(
                f"url {current!r} does not contain {action.url!r}"
            )

    if not checks and not failures:
        return False, (
            "verification failed: a verify action needs at least one "
            "condition (target, text, url, or absent_text)"
        )
    if failures:
        return False, "verification failed: " + "; ".join(failures)
    return True, "verified: " + "; ".join(checks)
