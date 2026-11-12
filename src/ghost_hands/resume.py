"""Resume context: pick up where a dead run stopped (v1.5).

A run that ends early — budget stop, stuck, an error, a closed
laptop — leaves a complete trail. ``load_resume_context`` reads
that trail and distills what a fresh run needs to continue instead
of starting over: the goal, the last page the run perceived, how
many actions it executed, and how it stopped. The CLI's
``run --resume <trail>`` seeds the new run with that page and a
history note telling the decider what already happened; the old
trail stays untouched as the record of the first attempt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .trail import read_trail


def load_resume_context(path: str | Path) -> dict[str, Any]:
    """Distill a trail into resume context. Raises ValueError/OSError
    (via read_trail) when the trail cannot be read."""
    events = read_trail(path)
    goal = ""
    last_url = ""
    executed = 0
    stop_reason = ""
    stop_summary = ""
    for ev in events:
        kind = ev.get("type")
        if kind == "perceive":
            if ev.get("goal") and not goal:
                goal = str(ev["goal"])
            if ev.get("url"):
                last_url = str(ev["url"])
        elif kind == "execute":
            executed += 1
        elif kind == "stop":
            stop_reason = str(ev.get("reason") or "")
            stop_summary = str(ev.get("summary") or "")
            # The stop event carries the driver's final URL (v1.5) —
            # fresher than the last perceive, which lags one step
            # behind the final action.
            if ev.get("url"):
                last_url = str(ev["url"])
    return {
        "goal": goal,
        "start_url": last_url,
        "executed_steps": executed,
        "stop_reason": stop_reason,
        "stop_summary": stop_summary,
        "events": len(events),
    }


def resume_note(ctx: dict[str, Any]) -> str:
    """The history note a resumed run's decider starts with."""
    stopped = ctx.get("stop_reason") or "unknown"
    summary = ctx.get("stop_summary") or ""
    detail = f" ({summary})" if summary else ""
    return (
        "Resumed run: a previous run of this goal stopped with "
        f"reason '{stopped}'{detail} after {ctx.get('executed_steps', 0)} "
        f"executed action(s), last seen at {ctx.get('start_url') or 'an unknown page'}. "
        "Its trail is the record of that attempt. Continue from the "
        "current page; do not repeat work the previous run already "
        "completed."
    )
