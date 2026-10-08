"""The Trail: a JSONL provenance log of everything a run did and why.

Events: perceive / decide / govern / execute / result / stop, plus ``heal``
(v0.2): a targeted action whose element moved between perception and
execution was re-found by descriptor and retried once. v0.3 adds the
driver-originated events the Runner wires in through the driver's event
sink: ``dialog`` (a JS dialog appeared; the event records the handling
decision), ``net`` (a completed network request: method, URL, status,
type — capped per run), and ``download`` (a browser download completed:
filename + byte size). The govern and execute events are written *before*
the action runs (record-before-execute), so even a crash leaves an honest
account of what was about to happen.
"""

from __future__ import annotations

import json
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
)


class Trail:
    """Append-only trail. Writes to ``path`` if given; always keeps events
    in memory so the MCP server and tests can read them back."""

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self.events: list[dict[str, Any]] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, step: int, **fields: Any) -> dict[str, Any]:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown trail event type: {event_type!r}")
        event: dict[str, Any] = {
            "type": event_type,
            "step": step,
            "ts": round(time.time(), 3),
        }
        event.update(fields)
        self.events.append(event)
        if self.path:
            with self.path.open("a", encoding="utf-8") as fh:
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
