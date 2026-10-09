"""BusClient: a stdlib client for the GhostBus REST API.

GhostBus (~/workspace/ghostbus, zero-dependency Node) exposes the same
workspace two ways this client must handle:

- the single-workspace relay (``src/http-server.mjs``): API at the root;
- the hosted server (``src/hosted-server.mjs``): each workspace lives
  under a ``/w/<id>/`` prefix, with its own key.

So a client is configured with a base URL plus an optional workspace
id; the effective prefix is ``<base>/w/<workspace>`` when a workspace
is given, else ``<base>`` (a base URL that already ends in ``/w/<id>``
also works — it is used verbatim).

Auth, matching the bus's own model:

- the **workspace key** (when the bus has one) rides as ``x-bus-key``;
- the **per-agent token** issued once at registration rides as
  ``x-agent-token``. It is kept in memory only.

Secrets come from constructor arguments (the CLI feeds them from flags
or GHOSTBUS_* env vars) and are **never written to disk or to a trail**.
Only urllib is used — zero dependencies, like everything here.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from .errors import HandsError


class BusError(HandsError):
    """A GhostBus call failed: transport error, or the bus answered
    with an error status and a message. Carries the HTTP status."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


class BusClient:
    def __init__(
        self,
        base_url: str,
        workspace: Optional[str] = None,
        key: Optional[str] = None,
        agent_name: str = "ghost-hands",
        timeout: float = 75.0,
    ) -> None:
        base = (base_url or "").rstrip("/")
        if not base:
            raise BusError("a GhostBus base URL is required")
        if workspace:
            base = f"{base}/w/{workspace}"
        self.base_url = base
        self.key = key or None
        self.agent_name = agent_name
        self.token: Optional[str] = None
        self.timeout = float(timeout)

    # -- transport -----------------------------------------------------------
    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        params: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        url = self.base_url + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.key:
            headers["x-bus-key"] = self.key
        if self.token:
            headers["x-agent-token"] = self.token
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(
                req, timeout=timeout or self.timeout
            ) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            message = raw
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and parsed.get("error"):
                    message = str(parsed["error"])
            except ValueError:
                pass
            raise BusError(
                f"GhostBus {method} {path} -> HTTP {exc.code}: {message}",
                status=exc.code,
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise BusError(
                f"GhostBus {method} {path} unreachable at {self.base_url}: {exc}"
            ) from exc
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            return {"raw": raw}

    def _agent_body(self, **fields: Any) -> dict:
        body = {"agent": self.agent_name}
        body.update(fields)
        return body

    # -- presence --------------------------------------------------------------
    def health(self) -> dict:
        return self._request("GET", "/health")

    def register(self, role: str = "", capabilities: Optional[list] = None) -> dict:
        """Register this agent; stores the one-time token (memory only)
        when the bus issues one."""
        out = self._request(
            "POST",
            "/api/agents",
            body=self._agent_body(
                role=role, capabilities=list(capabilities or [])
            ),
        )
        if isinstance(out, dict) and out.get("token"):
            self.token = str(out["token"])
        return out

    def heartbeat(self) -> dict:
        return self._request("POST", "/api/heartbeat", body=self._agent_body())

    def list_agents(self) -> list:
        out = self._request("GET", "/api/agents")
        return out.get("agents", []) if isinstance(out, dict) else []

    def status(self) -> dict:
        return self._request("GET", "/api/status")

    # -- messages ---------------------------------------------------------------
    def send_message(
        self,
        to: str,
        body: str,
        channel: str = "general",
        thread_id: Optional[int] = None,
    ) -> dict:
        return self._request(
            "POST",
            "/api/messages",
            body=self._agent_body(
                to=to, body=body, channel=channel, threadId=thread_id
            ),
        )

    def inbox(self, unread_only: bool = False, limit: int = 50) -> list:
        out = self._request(
            "GET",
            "/api/inbox",
            params={
                "agent": self.agent_name,
                "unread": "1" if unread_only else None,
                "limit": limit,
            },
        )
        return out.get("messages", []) if isinstance(out, dict) else []

    # -- tasks ---------------------------------------------------------------------
    def list_tasks(
        self, status: Optional[str] = None, assignee: Optional[str] = None
    ) -> list:
        out = self._request(
            "GET", "/api/tasks", params={"status": status, "assignee": assignee}
        )
        return out.get("tasks", []) if isinstance(out, dict) else []

    def get_task(self, task_id: int) -> Optional[dict]:
        for task in self.list_tasks():
            if int(task.get("id", -1)) == int(task_id):
                return task
        return None

    def create_task(
        self,
        title: str,
        body: str = "",
        assignee: Optional[str] = None,
        priority: str = "normal",
        needs_approval: bool = False,
        blocked_by: Optional[list] = None,
    ) -> dict:
        return self._request(
            "POST",
            "/api/tasks",
            body=self._agent_body(
                title=title,
                body=body,
                assignee=assignee,
                priority=priority,
                needsApproval=bool(needs_approval),
                blockedBy=list(blocked_by or []),
            ),
        )

    def approve_task(self, task_id: int) -> dict:
        return self._request(
            "POST", f"/api/tasks/{int(task_id)}/approve", body=self._agent_body()
        )

    def claim_task(self, task_id: int) -> dict:
        return self._request(
            "POST", f"/api/tasks/{int(task_id)}/claim", body=self._agent_body()
        )

    def complete_task(self, task_id: int, result: str = "") -> dict:
        return self._request(
            "POST",
            f"/api/tasks/{int(task_id)}/complete",
            body=self._agent_body(result=result),
        )

    def cancel_task(self, task_id: int) -> dict:
        return self._request(
            "POST", f"/api/tasks/{int(task_id)}/cancel", body=self._agent_body()
        )

    def comment_task(self, task_id: int, body: str) -> dict:
        return self._request(
            "POST",
            f"/api/tasks/{int(task_id)}/comment",
            body=self._agent_body(body=body),
        )

    # -- shared files ----------------------------------------------------------------
    def put_file(self, path: str, text: str) -> dict:
        return self._request(
            "PUT", "/api/files", body=self._agent_body(path=path, text=text)
        )

    def get_file(self, path: str) -> dict:
        return self._request("GET", "/api/files", params={"path": path})

    def list_files(self) -> list:
        out = self._request("GET", "/api/files/list")
        return out.get("files", []) if isinstance(out, dict) else []

    # -- events: long-poll + sync -------------------------------------------------------
    def wait_events(
        self, since_seq: Optional[int] = None, timeout: float = 25.0
    ) -> tuple[list, int, bool]:
        """Long-poll ``GET /api/wait``: returns (events, headSeq, waited).
        The server clamps the wait to 55s; the HTTP timeout here is set
        comfortably past the requested wait."""
        out = self._request(
            "GET",
            "/api/wait",
            params={
                "agent": self.agent_name,
                "timeout": max(1, int(timeout)),
                "sinceSeq": since_seq,
            },
            timeout=float(timeout) + 30.0,
        )
        if not isinstance(out, dict):
            return [], since_seq or 0, False
        return (
            out.get("events", []),
            int(out.get("headSeq", since_seq or 0)),
            bool(out.get("waited", False)),
        )

    def sync(self, since_seq: int = 0, limit: int = 500) -> dict:
        return self._request(
            "GET", "/api/sync", params={"sinceSeq": since_seq, "limit": limit}
        )
