"""The Trail: a JSONL provenance log of everything a run did and why.

Events: perceive / decide / govern / execute / result / stop, plus ``heal``
(v0.2): a targeted action whose element moved between perception and
execution was re-found by descriptor and retried once. v0.3 adds the
driver-originated events the Runner wires in through the driver's event
sink: ``dialog`` (a JS dialog appeared; the event records the handling
decision), ``net`` (a completed network request: method, URL, status,
type — capped per run), and ``download`` (a browser download completed:
filename + byte size). v0.4 adds ``approval``: an approval requested or
decided over an external channel (GhostBus in the bus agent). The govern and execute events are written *before*
the action runs (record-before-execute), so even a crash leaves an honest
account of what was about to happen.

v1.0: the trail is **tamper-evident**. Every event carries ``prev`` (the
previous event's hash, or ``"genesis"``) and ``hash`` (SHA-256 over the
canonical event plus ``prev``) — a hash chain over the whole record.
Editing, deleting, or reordering any event breaks every hash after it,
and ``verify_trail`` / ``ghost-hands verify-trail`` proves a trail is
exactly the record that was written. Trails written before v1.0 have
no hashes; verification reports them as legacy rather than forging a
verdict over a chain that was never there.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

EVENT_TYPES = (
    "perceive",
    "decide",
    "govern",
    "execute",
    "result",
    "stop",
    "heal",
    "dialog",
    "net",
    "download",
    # v0.4: an approval was requested / decided over an external channel
    # (GhostBus, phone push). Fields: channel, phase ("request" |
    # "decision"), classification, run_id, approved (on decisions).
    "approval",
    # v1.0: a governed plan-cache event. Fields: phase ("hit" |
    # "fallback" | "store"), key, remaining (on hits).
    "cache",
    # v1.3: a stuck run used its recovery chance instead of stopping.
    # Fields: reason, attempt.
    "recovery",
    # v1.10: a step failed terminally (heal chain exhausted, driver
    # raised) and the run spent an error-recovery chance instead of
    # stopping. Fields: error, attempt.
    "error_recovery",
)

GENESIS = "genesis"


def _event_hash(event: dict[str, Any], prev: str) -> str:
    """SHA-256 over the canonical event (hash fields excluded) chained
    to the previous event's hash."""
    body = {k: v for k, v in event.items() if k not in ("hash", "prev")}
    payload = json.dumps(
        {"prev": prev, "event": body},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Trail:
    """Append-only trail. Writes to ``path`` if given; always keeps events
    in memory so the MCP server and tests can read them back."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self.events: list[dict[str, Any]] = []
        self._last_hash: str = GENESIS
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                # A trail records typed text verbatim in its action
                # records (passwords included — see evaluate.py's
                # trail audit). It is a credential-bearing file:
                # tighten a pre-existing loose one (v0.9.1).
                try:
                    os.chmod(self.path, 0o600)
                except OSError:
                    pass
                # Appending to an existing chained trail continues its
                # chain from the last recorded hash.
                try:
                    lines = self.path.read_text(encoding="utf-8").splitlines()
                    for line in reversed(lines):
                        line = line.strip()
                        if not line:
                            continue
                        last = json.loads(line)
                        if isinstance(last, dict) and last.get("hash"):
                            self._last_hash = str(last["hash"])
                        break
                except (OSError, ValueError):
                    pass

    def record(self, event_type: str, step: int, **fields: Any) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown trail event type: {event_type!r}")
        event: dict[str, Any] = {
            "type": event_type,
            "step": step,
            "ts": round(time.time(), 3),
        }
        event.update(fields)
        event["prev"] = self._last_hash
        event["hash"] = _event_hash(event, self._last_hash)
        self._last_hash = event["hash"]
        self.events.append(event)
        if self.path:
            # Owner-only from creation (0o600): before v0.9.1 the
            # trail landed with the process umask — group/world
            # readable on typical setups despite carrying typed text.
            fd = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
            )
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")
        return event

    def accounting(self) -> dict[str, Any]:
        return accounting(self.events)


def read_trail(path: str | Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events


def verify_trail(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Verify a trail's v1.0 hash chain.

    Returns a dict: ``ok`` (the chain is intact), ``checked`` (events
    examined), ``hashed`` / ``legacy`` (events with / without a hash),
    ``first_bad`` (index of the first event that fails, or None) and a
    plain-language ``reason``. A trail with no hashed events at all is
    legacy: ``ok`` is False and the reason says so — absence of a chain
    is reported, never silently passed. Legacy events *before* the
    first hashed event are tolerated (a v1.0 run appended to an old
    file); a hashless event after the chain has started is a gap and
    fails.
    """
    total_hashed = sum(1 for e in events if e.get("hash"))
    hashed = 0
    legacy = 0
    expected_prev = GENESIS
    chain_started = False
    for index, event in enumerate(events):
        digest = event.get("hash")
        if not digest:
            if chain_started:
                return {
                    "ok": False,
                    "checked": len(events),
                    "hashed": total_hashed,
                    "legacy": legacy,
                    "first_bad": index,
                    "reason": f"event {index} has no hash but the chain "
                    "had already started — the record has a gap",
                }
            legacy += 1
            continue
        chain_started = True
        if event.get("prev") != expected_prev:
            return {
                "ok": False,
                "checked": len(events),
                "hashed": total_hashed,
                "legacy": legacy,
                "first_bad": index,
                "reason": f"event {index} does not chain to the previous "
                "event — an event was removed, reordered, or inserted",
            }
        if _event_hash(event, expected_prev) != digest:
            return {
                "ok": False,
                "checked": len(events),
                "hashed": total_hashed,
                "legacy": legacy,
                "first_bad": index,
                "reason": f"event {index} hash mismatch — its contents "
                "were changed after it was written",
            }
        hashed += 1
        expected_prev = str(digest)
    if hashed == 0:
        return {
            "ok": False,
            "checked": len(events),
            "hashed": 0,
            "legacy": legacy,
            "first_bad": None,
            "reason": "legacy trail: no hash chain (written before v1.0)",
        }
    return {
        "ok": True,
        "checked": len(events),
        "hashed": total_hashed,
        "legacy": legacy,
        "first_bad": None,
        "reason": "hash chain intact",
    }


def accounting(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-run accounting: steps taken, perception size (~tokens at
    chars/4), and how many actions landed in each governor class."""
    steps = 0
    map_chars = 0
    by_class: dict[str, int] = {}
    for ev in events:
        if ev.get("type") == "execute":
            steps += 1
        elif ev.get("type") == "perceive":
            map_chars += int(ev.get("map_chars", 0))
        elif ev.get("type") == "govern":
            cls = ev.get("classification", "unknown")
            by_class[cls] = by_class.get(cls, 0) + 1
    return {
        "steps": steps,
        "map_chars": map_chars,
        "est_tokens": round(map_chars / 4),
        "actions_by_class": by_class,
    }
