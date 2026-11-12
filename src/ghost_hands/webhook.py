"""Completion webhook (v1.9).

When ``GHOST_HANDS_WEBHOOK_URL`` is set, finished runs POST their
report JSON to it — the integration hook for pipelines, chats, and
dashboards that want to know a run ended without polling. A
webhook failure never fails the run: it is noted and swallowed,
because the run's own record (the trail) is the source of truth,
not the notification.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional


def post_webhook(payload: dict, url: Optional[str] = None, timeout: float = 5.0) -> bool:
    """POST ``payload`` as JSON to the configured webhook URL.
    Returns True when an endpoint was configured and answered 2xx,
    False otherwise (including 'not configured'). Never raises."""
    target = url if url is not None else (os.environ.get("GHOST_HANDS_WEBHOOK_URL") or "")
    if not target:
        return False
    try:
        req = urllib.request.Request(
            target,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False
