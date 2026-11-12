"""The governed plan cache: learn a task once, replay it for free.

Stagehand's answer to model cost is an action cache; Ghost Hands v1.0
takes the same idea further and keeps it governed. When a run finishes
with ``done``, the Runner can store the executed steps — each action in
its redacted, placeholder form, paired with the descriptor of the
element it acted on — in a local plan cache keyed by the normalized
goal and the start URL.

The next run with the same goal and start URL replays the plan
*through the same loop*: every cached step is re-perceived, re-found by
its recorded descriptor (numbers may have moved; identity is what is
cached), re-classified, re-approved when the policy says ask, and
re-recorded — now hash-chained — in the new run's trail. The decider
is not called for a cached step at all: no model cost, no model
variance. If a cached target cannot be re-found, the cache falls back
to the live decider for that step and the trail records the fallback;
a run that ends any way other than ``done`` is never stored.

The cache file is local, owner-only (0600), and trusted local data —
treat it like a script, because that is what it is. It never contains
a secret: actions are stored before secret resolution, in placeholder
form, exactly as they appear in the trail.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional


def plan_key(goal: str, start_url: str) -> str:
    normalized_goal = " ".join((goal or "").lower().split())
    normalized_url = (start_url or "").split("#", 1)[0].strip()
    material = f"{normalized_goal}|{normalized_url}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class PlanCache:
    """A goal+start-URL keyed store of proven action plans.

    ``path=None`` keeps the cache in memory (tests, one-process hosts);
    with a path it persists as one JSON file, written owner-only.
    A corrupt or unreadable cache file loads as empty rather than
    breaking a run — the cache is an optimization, never a dependency.
    """

    def __init__(self, path: Optional[str | Path] = None) -> None:
        self.path = Path(path) if path else None
        self._plans: dict[str, dict[str, Any]] = {}
        if self.path and self.path.exists():
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                plans = payload.get("plans") if isinstance(payload, dict) else None
                if isinstance(plans, dict):
                    self._plans = {
                        str(k): v for k, v in plans.items() if isinstance(v, dict)
                    }
            except (OSError, ValueError):
                self._plans = {}

    def lookup(self, goal: str, start_url: str) -> Optional[dict[str, Any]]:
        entry = self._plans.get(plan_key(goal, start_url))
        return entry if entry and entry.get("steps") else None

    def store(
        self,
        goal: str,
        start_url: str,
        steps: list[dict[str, Any]],
        summary: str = "",
    ) -> str:
        key = plan_key(goal, start_url)
        existing = self._plans.get(key) or {}
        self._plans[key] = {
            "key": key,
            "goal": goal,
            "start_url": start_url,
            "steps": steps,
            # The done summary of the proving run: a fully replayed
            # plan ends with this summary, because a plan only exists
            # at all when its proving run ended ``done``.
            "summary": summary,
            "created": round(time.time(), 3),
            "hits": int(existing.get("hits", 0)),
        }
        self._save()
        return key

    def note_hit(self, key: str) -> None:
        entry = self._plans.get(key)
        if entry is not None:
            entry["hits"] = int(entry.get("hits", 0)) + 1
            entry["last_hit"] = round(time.time(), 3)
            self._save()

    def _save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"version": 1, "plans": self._plans}, indent=1)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def __len__(self) -> int:
        return len(self._plans)
