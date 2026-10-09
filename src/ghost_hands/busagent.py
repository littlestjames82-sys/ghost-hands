"""BusAgent: Ghost Hands working as an agent on GhostBus.

The agent registers on the bus as ``ghost-hands`` (role ``hands``),
claims tasks addressed to it (assignee) or tagged ``hands``, and runs
them through the *same* Runner / Governor / Trail as every other Ghost
Hands surface. Nothing about governance changes because the work
arrived over a bus:

- A task in the bus's ``needs-approval`` state is never claimed — the
  bus's own gate must clear first (someone calls the bus approve
  endpoint); the agent only ever sees ``queued`` tasks.
- Claim leases (15 minutes on the bus) are respected: the agent claims
  exclusively, posts progress comments while it works, and completes
  with a structured JSON report. If a run outlives its lease, the
  completion can fail — the agent then posts the report as a comment
  and says so, instead of pretending.
- Every run's JSONL trail is uploaded to the bus as a shared file, and
  the completion report names it.

Approvals over the bus: when the Governor says "ask", the approver
posts ``APPROVAL NEEDED: <class> <summary> — reply APPROVE <run-id> or
DENY <run-id>`` as a task comment **and** as a message to the task's
creator (broadcast when the creator is unknown), then waits — inbox
polls paced by the bus's ``/api/wait`` long-poll — up to the approval
timeout. APPROVE proceeds; DENY or a timeout denies, the run stops
honestly, and both the request and the decision are recorded in the
trail (``approval`` events, ``channel: bus``). Timeouts are short in
tests, 600s by default (or whatever the active policy pack sets).

Task bodies: JSON — ``{"goal": ..., "start_url": ..., "steps": [...],
"pages": {...}, "driver": "fake"|"chromium"|"simphone"}`` — or simple
lines: ``goal: ...`` / ``url: ...`` (also ``start_url:``, ``driver:``).
With ``steps`` the ScriptedDecider runs them; with only a goal, the
deterministic RuleDecider drives.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from .actions import Action
from .busclient import BusClient, BusError
from .deciders import RuleDecider, ScriptedDecider
from .drivers import DEMO_PAGES, ChromiumDriver, FakeDriver
from .errors import HandsError
from .governor import Governor
from .phone_approver import ObservingDecider, PhoneApprover
from .policies import PolicyPack, load_pack
from .runner import Runner
from .simphone import SimPhoneDriver
from .trail import Trail

AGENT_NAME = "ghost-hands"
AGENT_ROLE = "hands"
CAPABILITIES = [
    "navigate",
    "click",
    "type",
    "press",
    "select",
    "scroll",
    "extract",
    "screenshot",
    "fill_form",
    "set_file",
    "download",
    "pdf",
    "wait_for",
    "simphone",
    "android",
]


# ---------------------------------------------------------------------------
# Task body parsing
# ---------------------------------------------------------------------------


@dataclass
class TaskSpec:
    goal: str = ""
    start_url: Optional[str] = None
    steps: Optional[list] = None
    pages: Optional[dict] = None
    driver: Optional[str] = None

    @property
    def executable(self) -> bool:
        return bool(self.steps) or bool(self.goal)


def parse_task_body(task: dict) -> TaskSpec:
    """Parse a bus task into a TaskSpec (JSON body, or goal:/url: lines)."""
    body = (task.get("body") or "").strip()
    title = (task.get("title") or "").strip()
    spec = TaskSpec(goal=title)
    if not body:
        return spec
    if body.startswith("{"):
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        if isinstance(data, dict):
            spec.goal = str(data.get("goal") or title)
            spec.start_url = (
                data.get("start_url") or data.get("url") or data.get("start")
            )
            steps = data.get("steps")
            if isinstance(steps, list) and steps:
                spec.steps = steps
            pages = data.get("pages")
            if isinstance(pages, dict) and pages:
                spec.pages = pages
            if data.get("driver"):
                spec.driver = str(data["driver"])
            return spec
        # Not valid JSON after all — fall through to line parsing.
    goal_lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = re.match(r"(?i)^(goal|url|start_url|start|driver)\s*:\s*(.+)$", stripped)
        if m:
            key, value = m.group(1).lower(), m.group(2).strip()
            if key == "goal":
                spec.goal = value
            elif key == "driver":
                spec.driver = value
            else:
                spec.start_url = value
        else:
            goal_lines.append(stripped)
    if goal_lines and (not spec.goal or spec.goal == title):
        spec.goal = " ".join(goal_lines)
    return spec


_TAG_LINE = re.compile(r"(?im)^\s*tags?\s*:\s*(.+)$")
_HANDS_WORD = re.compile(r"\bhands\b", re.IGNORECASE)


def is_for_hands(task: dict, agent_name: str = AGENT_NAME) -> bool:
    """Is this queued task addressed to the hands agent? Assignee match,
    a ``[hands]`` marker, a ``tags:`` line naming hands, or a JSON body
    whose tags/assignee fields name hands. Unaddressed, untagged tasks
    belong to someone else — the agent does not grab them."""
    assignee = task.get("assignee")
    if assignee:
        return assignee == agent_name
    title = task.get("title") or ""
    body = task.get("body") or ""
    if "[hands]" in title.lower() or "[hands]" in body.lower():
        return True
    m = _TAG_LINE.search(body) or _TAG_LINE.search(title)
    if m and _HANDS_WORD.search(m.group(1)):
        return True
    if body.strip().startswith("{"):
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        if isinstance(data, dict):
            tags = data.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            if any(str(t).lower() in ("hands", agent_name) for t in tags):
                return True
            if str(data.get("assignee") or "") in (agent_name, "hands"):
                return True
    return False


def blockers_open(task: dict, all_tasks: list) -> list:
    """blockedBy ids whose tasks are not done/cancelled (a missing
    blocker counts as open — conservative, like the bus's own claim)."""
    by_id = {int(t.get("id", -1)): t for t in all_tasks}
    open_ids = []
    for bid in task.get("blockedBy") or []:
        blocker = by_id.get(int(bid))
        if blocker is None or blocker.get("status") not in ("done", "cancelled"):
            open_ids.append(int(bid))
    return open_ids


# ---------------------------------------------------------------------------
# Approvals over the bus
# ---------------------------------------------------------------------------


def _action_summary(action: Action) -> str:
    bits = [action.kind]
    if action.target is not None:
        bits.append(f"[{action.target}]")
    if action.text:
        bits.append(repr(action.text[:40]))
    if action.url:
        bits.append(action.url[:80])
    if action.fields:
        bits.append(f"fields={sorted(action.fields)[:5]}")
    return " ".join(bits)


class BusApprover:
    """The Governor's approver, speaking GhostBus.

    Reads the classification from the run's own trail (the Runner
    records the govern event *before* it calls the approver), posts the
    request as a task comment + a message to the task creator, and
    waits for an ``APPROVE <run-id>`` / ``DENY <run-id>`` reply.
    """

    def __init__(
        self,
        client: BusClient,
        task: dict,
        trail: Trail,
        run_id: str,
        timeout: float = 600.0,
        poll_interval: float = 1.0,
    ) -> None:
        self.client = client
        self.task = task
        self.trail = trail
        self.run_id = run_id
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)
        self._counter = 0
        self._seq: Optional[int] = None

    def _govern_context(self, action: Action) -> tuple[str, int]:
        for ev in reversed(self.trail.events):
            if ev.get("type") == "govern":
                return str(ev.get("classification", "consequential")), int(
                    ev.get("step", 0)
                )
        # No govern event yet (approver used outside a Runner): classify
        # directly, without an element.
        return Governor().classify(action), 0

    def __call__(self, action: Action) -> bool:
        self._counter += 1
        approval_id = f"{self.run_id}-a{self._counter}"
        classification, step = self._govern_context(action)
        summary = _action_summary(action)
        text = (
            f"APPROVAL NEEDED: {classification} {summary} — "
            f"reply APPROVE {approval_id} or DENY {approval_id}"
        )
        self.trail.record(
            "approval",
            step,
            channel="bus",
            phase="request",
            run_id=approval_id,
            classification=classification,
            action=action.to_dict(),
            task_id=self.task.get("id"),
            timeout_s=self.timeout,
        )
        posted = True
        try:
            self.client.comment_task(int(self.task["id"]), text)
            creator = self.task.get("from")
            target = creator if creator and creator != self.client.agent_name else "*"
            self.client.send_message(target, text)
        except BusError:
            posted = False
        if not posted:
            return self._decide(step, approval_id, False, "approval channel failed", None)
        if self.timeout <= 0:
            return self._decide(step, approval_id, False, "approval timeout (0s)", None)

        deadline = time.time() + self.timeout
        while time.time() < deadline:
            reply = self._poll_reply(approval_id, deadline)
            if reply is not None:
                approved, by = reply
                return self._decide(
                    step,
                    approval_id,
                    approved,
                    f"{'approved' if approved else 'denied'} by {by} over the bus",
                    by,
                )
            time.sleep(min(self.poll_interval, max(0.0, deadline - time.time())))
        return self._decide(step, approval_id, False, "approval timeout", None)

    def _decide(self, step, approval_id, approved, reason, by) -> bool:
        self.trail.record(
            "approval",
            step,
            channel="bus",
            phase="decision",
            run_id=approval_id,
            approved=approved,
            reason=reason,
            by=by,
        )
        return approved

    def _poll_reply(self, approval_id: str, deadline: float) -> Optional[tuple]:
        """One wait cycle: long-poll for bus events, then read the inbox
        for an APPROVE/DENY carrying this approval id."""
        remaining = deadline - time.time()
        if remaining <= 0:
            return None
        try:
            events, head, _waited = self.client.wait_events(
                since_seq=self._seq, timeout=min(10.0, remaining)
            )
            self._seq = head
            _ = events  # events wake us; the inbox is the source of truth
        except BusError:
            time.sleep(min(self.poll_interval, remaining))
        try:
            messages = self.client.inbox(unread_only=True, limit=50)
        except BusError:
            return None
        aid = approval_id.upper()
        for msg in messages:
            if msg.get("from") == self.client.agent_name:
                continue
            body = str(msg.get("body") or "")
            upper = body.upper()
            if aid not in upper:
                continue
            if f"DENY {aid}" in upper or upper.strip().startswith("DENY"):
                return (False, msg.get("from"))
            if f"APPROVE {aid}" in upper:
                return (True, msg.get("from"))
        return None


# ---------------------------------------------------------------------------
# Approvals on the phone (v0.6): the bus carries the work, the phone
# carries the conscience. With --approval-channel phone, no approval
# is ever requested FROM the bus — the ask travels to Ryan's phone via
# the MrGhosty bridge (PhoneApprover), and the bus only receives a
# comment recording the phone's decision.
# ---------------------------------------------------------------------------


class _PhoneChannelApprover:
    """BusAgent approver that routes asks to the phone and reports the
    outcome back to the task as a comment. The trail's approval events
    are PhoneApprover's own (channel: phone)."""

    def __init__(
        self,
        client: BusClient,
        task: dict,
        trail: Trail,
        timeout: float = 600.0,
        log: Optional[Callable[[str], None]] = None,
        phone: Optional[PhoneApprover] = None,
    ) -> None:
        self.client = client
        self.task = task
        self.log = log or (lambda msg: None)
        # Bridge URL + token come from the environment only (never
        # from a task body) — same rule as the android driver factory.
        self.phone = phone or PhoneApprover(trail=trail, timeout=timeout)

    def __call__(self, action: Action) -> bool:
        approved = self.phone(action)
        status = self.phone.last_status or ("approved" if approved else "error")
        summary = self.phone.last_summary or action.kind
        try:
            self.client.comment_task(
                int(self.task["id"]),
                f"ghost-hands: phone approval {status} — {summary} "
                "(decided on Ryan's phone, not on the bus)",
            )
        except BusError as exc:
            self.log(f"phone-approval outcome comment failed (continuing): {exc}")
        except Exception as exc:  # a comment must never fail the run's record
            self.log(f"phone-approval outcome comment failed (continuing): {exc}")
        return approved


# ---------------------------------------------------------------------------
# Progress reporting decider wrapper
# ---------------------------------------------------------------------------


class _ProgressDecider:
    """Wraps the task's decider; posts a bus comment every N decisions
    so a human watching the task sees the run move. Comment failures
    never fail the run."""

    def __init__(self, inner, client: BusClient, task_id: int, every: int = 5, log=None):
        self.inner = inner
        self.client = client
        self.task_id = task_id
        self.every = every
        self.count = 0
        self.log = log or (lambda *a: None)

    def decide(self, goal, element_map, history):
        action = self.inner.decide(goal, element_map, history)
        self.count += 1
        if self.every and self.count % self.every == 0:
            try:
                self.client.comment_task(
                    self.task_id,
                    f"ghost-hands progress: step {self.count} — next: "
                    f"{_action_summary(action)}",
                )
            except BusError as exc:
                self.log(f"progress comment failed (continuing): {exc}")
        return action


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------


def default_driver_factory(spec: TaskSpec, forced: Optional[str] = None):
    """Pick the body for a task: explicit task/CLI driver wins; embedded
    pages mean the fake web; a phone:// URL means the sim phone; an
    http(s) URL means real Chromium; otherwise the demo fake web."""
    kind = (forced or spec.driver or "").strip().lower()
    if kind == "android":
        # Configured purely from the environment (GHOST_HANDS_ANDROID_
        # BRIDGE / GHOST_HANDS_ANDROID_TOKEN) — a bus task must never
        # carry the pairing token in its body.
        from .android_driver import AndroidDriver

        return AndroidDriver()
    if kind == "chromium":
        return ChromiumDriver()
    if kind == "simphone":
        driver = SimPhoneDriver()
        return driver
    if kind == "fake":
        return FakeDriver(spec.pages or DEMO_PAGES, start_url=spec.start_url)
    if spec.pages:
        return FakeDriver(spec.pages, start_url=spec.start_url)
    if spec.start_url and spec.start_url.startswith("phone://"):
        return SimPhoneDriver()
    if spec.start_url and spec.start_url.startswith(("http://", "https://")):
        return ChromiumDriver()
    return FakeDriver(DEMO_PAGES, start_url=spec.start_url)


class BusAgent:
    def __init__(
        self,
        client: BusClient,
        policy_pack: Optional[PolicyPack] = None,
        approval_timeout: Optional[float] = None,
        approval_channel: str = "bus",
        step_budget: int = 30,
        driver_factory: Optional[Callable[[TaskSpec], Any]] = None,
        forced_driver: Optional[str] = None,
        log: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.client = client
        self.pack = policy_pack or load_pack("standard")
        # Effective approval timeout: explicit flag beats the pack's.
        self.approval_timeout = (
            float(approval_timeout)
            if approval_timeout is not None
            else float(self.pack.approval_timeout)
        )
        channel = str(approval_channel or "bus").strip().lower()
        if channel not in ("bus", "phone"):
            raise HandsError(
                f"unknown approval channel {approval_channel!r} — use bus or phone"
            )
        self.approval_channel = channel
        self.step_budget = step_budget
        self.driver_factory = driver_factory
        self.forced_driver = forced_driver
        self.log = log or (lambda msg: None)
        self.registered = False

    # -- bus lifecycle ---------------------------------------------------------
    def register(self) -> dict:
        out = self.client.register(role=AGENT_ROLE, capabilities=CAPABILITIES)
        self.registered = True
        self.log(f"registered on the bus as {self.client.agent_name} (role: hands)")
        return out

    def find_task(self) -> Optional[dict]:
        queued = self.client.list_tasks(status="queued")
        all_tasks = self.client.list_tasks()
        for task in queued:
            if task.get("status") != "queued":
                continue  # needs-approval tasks are invisible here by design
            if not is_for_hands(task, self.client.agent_name):
                continue
            if blockers_open(task, all_tasks):
                continue
            spec = parse_task_body(task)
            if not spec.executable:
                self.log(
                    f"task #{task.get('id')} is for hands but has no goal/steps; skipping"
                )
                continue
            return task
        return None

    # -- one task ----------------------------------------------------------------
    def run_task(self, task: dict) -> dict:
        task_id = int(task["id"])
        spec = parse_task_body(task)
        run_id = f"gh-{task_id}-{int(time.time())}"
        try:
            self.client.claim_task(task_id)
        except BusError as exc:
            self.log(f"claim of task #{task_id} failed: {exc}")
            return {"task_id": task_id, "status": "claim-failed", "error": str(exc)}
        self.log(f"claimed task #{task_id}: {task.get('title')!r}")
        try:
            self.client.comment_task(
                task_id,
                f"ghost-hands claimed this task — starting: {spec.goal or task.get('title')}",
            )
        except BusError:
            pass

        trail = Trail()
        trail_name = f"ghost-hands/trail-task-{task_id}-{run_id}.jsonl"
        driver = None
        report_dict: dict[str, Any]
        try:
            factory = self.driver_factory or (
                lambda s: default_driver_factory(s, self.forced_driver)
            )
            driver = factory(spec)
            decider = (
                ScriptedDecider(spec.steps) if spec.steps else RuleDecider()
            )
            decider = _ProgressDecider(decider, self.client, task_id, log=self.log)
            if self.approval_channel == "phone":
                approver = _PhoneChannelApprover(
                    self.client,
                    task,
                    trail,
                    timeout=self.approval_timeout,
                    log=self.log,
                )
                decider = ObservingDecider(decider, approver.phone)
            else:
                approver = BusApprover(
                    self.client,
                    task,
                    trail,
                    run_id=run_id,
                    timeout=self.approval_timeout,
                )
            start_url = spec.start_url
            if isinstance(driver, FakeDriver):
                start_url = None  # the factory already positioned the fake web
            runner = Runner(
                driver,
                decider,
                policy=self.pack.policy,
                approver=approver,
                trail=trail,
                step_budget=self.step_budget,
                start_url=start_url,
            )
            report = runner.run(spec.goal or str(task.get("title") or ""))
            report_dict = report.to_dict()
        except Exception as exc:  # honest error report, still completed below
            report_dict = {
                "goal": spec.goal,
                "stop_reason": "error",
                "steps": 0,
                "summary": f"agent error: {type(exc).__name__}: {exc}",
                "actions_by_class": {},
            }
        finally:
            if driver is not None:
                try:
                    driver.close()
                except Exception:
                    pass

        # Upload the trail as a shared bus file, whatever happened.
        trail_text = "".join(json.dumps(ev) + "\n" for ev in trail.events)
        try:
            self.client.put_file(trail_name, trail_text)
        except BusError as exc:
            self.log(f"trail upload failed: {exc}")
            trail_name = f"{trail_name} (UPLOAD FAILED: {exc})"

        result = {
            "agent": self.client.agent_name,
            "task_id": task_id,
            "run_id": run_id,
            "status": report_dict.get("stop_reason", "error"),
            "stop_reason": report_dict.get("stop_reason", "error"),
            "steps": report_dict.get("steps", 0),
            "summary": report_dict.get("summary", ""),
            "actions_by_class": report_dict.get("actions_by_class", {}),
            "trail_file": trail_name,
            "policy_pack": self.pack.name,
        }
        try:
            self.client.complete_task(task_id, result=json.dumps(result))
        except BusError as exc:
            # The 15-minute claim lease may have expired mid-run. The
            # report still lands — as a comment — and we say so.
            self.log(f"complete failed ({exc}); posting the report as a comment")
            result["complete_error"] = str(exc)
            try:
                self.client.comment_task(
                    task_id,
                    "ghost-hands finished but could not complete the task "
                    f"({exc}). Report: {json.dumps(result)}",
                )
            except BusError:
                pass
        self.log(
            f"task #{task_id} finished: {result['status']} — {result['summary']}"
        )
        return result

    # -- the loop -------------------------------------------------------------------
    def run_once(self) -> Optional[dict]:
        if not self.registered:
            self.register()
        task = self.find_task()
        if task is None:
            return None
        return self.run_task(task)

    def serve(
        self, poll_interval: float = 5.0, max_tasks: Optional[int] = None
    ) -> int:
        if not self.registered:
            self.register()
        handled = 0
        while max_tasks is None or handled < max_tasks:
            try:
                result = self.run_once()
            except BusError as exc:
                self.log(f"bus poll failed (retrying): {exc}")
                result = None
            if result is not None:
                handled += 1
                continue
            try:
                self.client.heartbeat()
            except BusError:
                pass
            time.sleep(poll_interval)
        return handled


# ---------------------------------------------------------------------------
# bus-demo: prove it against the REAL GhostBus node server, locally
# ---------------------------------------------------------------------------


class BusDemoUnavailable(HandsError):
    """The real GhostBus server cannot run here (no node, no source)."""


def default_ghostbus_dir() -> Path:
    env = os.environ.get("GHOSTBUS_HOME")
    if env:
        return Path(env)
    return Path.home() / "workspace" / "ghostbus"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_health(base_url: str, seconds: float = 15.0) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def boot_ghostbus_server(
    ghostbus_dir: Path, store_dir: Optional[Path] = None, key: str = ""
):
    """Boot the real GhostBus http-server.mjs on an ephemeral port.
    Returns (process, base_url).

    The store defaults to GhostBus's ``:memory:`` store because the
    demo is ephemeral by design. A file-backed store (pass
    ``store_dir``) works just as well: the shared-``.tmp`` rename race
    the v0.4 demo originally routed around (HTTP 400 ENOENT on rename
    under concurrent agent + poller traffic) was fixed in GhostBus
    0.4.1 — unique temp file per save plus serialized saves — and is
    regression-tested in the GhostBus suite since 0.5.0; the v0.7
    bench runs this same demo against a file store to keep proving
    it. Same server, same REST API either way."""
    node = shutil.which("node")
    if node is None:
        raise BusDemoUnavailable("node is not on PATH — GhostBus cannot boot")
    server_js = ghostbus_dir / "src" / "http-server.mjs"
    if not server_js.is_file():
        raise BusDemoUnavailable(f"GhostBus source not found at {ghostbus_dir}")
    port = _free_port()
    env = dict(os.environ)
    if key:
        env["GHOSTBUS_KEY"] = key
    else:
        env.pop("GHOSTBUS_KEY", None)
    store = ":memory:" if store_dir is None else str(store_dir / "ghostbus-data.json")
    proc = subprocess.Popen(
        [
            node,
            str(server_js),
            "--port",
            str(port),
            "--store",
            store,
        ],
        cwd=str(ghostbus_dir),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    if not _wait_health(base_url):
        proc.terminate()
        raise BusDemoUnavailable(
            f"GhostBus server did not answer /health at {base_url}"
        )
    return proc, base_url


_FIXTURE_FORM = """<!doctype html><html><head><title>Bus Fixture Order</title></head>
<body><h1>Fixture order</h1>
<form action="/done">
<input id="item" name="item" type="text" placeholder="Item name">
<button type="submit">Place order</button>
</form></body></html>"""

_FIXTURE_DONE = """<!doctype html><html><head><title>Done</title></head>
<body><h1>Order placed</h1></body></html>"""


def _serve_fixture() -> tuple[ThreadingHTTPServer, str]:
    pages = {"/form": _FIXTURE_FORM, "/done": _FIXTURE_DONE}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler API
            body = pages.get(self.path.split("?", 1)[0])
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


_APPROVAL_ID_RE = re.compile(r"reply APPROVE (\S+) or DENY")


def run_bus_demo(
    ghostbus_dir: Optional[Path] = None,
    approval_timeout: float = 60.0,
    log: Optional[Callable[[str], None]] = None,
    store_dir: Optional[Path] = None,
) -> dict:
    """End-to-end proof against the REAL GhostBus node server:

    a second client ("requester") creates two tasks — one on the fake
    web, one on real Chromium against a local fixture — the agent runs
    both, and the Chromium task's consequential submit is approved by
    the requester over the bus, mid-run. Returns the evidence dict.
    Raises BusDemoUnavailable when node/GhostBus cannot run here; there
    is deliberately NO fake-server fallback in this harness — tests that
    need an in-process double test the client, not this demo."""
    log = log or (lambda msg: None)
    ghostbus_dir = ghostbus_dir or default_ghostbus_dir()
    evidence: dict[str, Any] = {"server": "real GhostBus node http-server.mjs"}
    proc, base_url = boot_ghostbus_server(
        ghostbus_dir, store_dir=store_dir, key="bus-demo-key"
    )
    evidence["store"] = "file" if store_dir is not None else ":memory:"
    evidence["bus_url"] = base_url
    fixture_server, fixture_base = _serve_fixture()
    try:
        requester = BusClient(base_url, key="bus-demo-key", agent_name="requester")
        requester.register(role="requester", capabilities=["creates tasks"])
        agent_client = BusClient(base_url, key="bus-demo-key", agent_name=AGENT_NAME)
        agent = BusAgent(
            agent_client,
            approval_timeout=approval_timeout,
            log=log,
        )
        agent.register()

        # -- Phase A: FakeDriver task --------------------------------------
        fake_task = requester.create_task(
            title="Demo: run the built-in demo web flow",
            body=json.dumps(
                {
                    "goal": "demo flow",
                    "start_url": next(iter(DEMO_PAGES)),
                    "pages": DEMO_PAGES,
                    "steps": [
                        {"kind": "type", "target": 1, "text": "ghost"},
                        {"kind": "click", "target": 2},
                        {"kind": "click", "target": 1},
                    ],
                }
            ),
            assignee=AGENT_NAME,
        )
        evidence["fake_task_id"] = fake_task["id"]
        result_a = agent.run_once()
        assert result_a is not None, "agent did not pick up the fake task"
        evidence["fake_result"] = result_a
        trail_a = requester.get_file(result_a["trail_file"])
        evidence["fake_trail_events"] = len(
            (trail_a.get("text") or "").splitlines()
        )

        # -- Phase B: Chromium task with a bus-approved consequential submit --
        chrome_task = requester.create_task(
            title="Demo: place the fixture order",
            body=json.dumps(
                {
                    "goal": "place the fixture order",
                    "start_url": fixture_base + "/form",
                    "driver": "chromium",
                    "steps": [
                        {"kind": "type", "target": 1, "text": "ghost widget"},
                        {"kind": "click", "target": 2},
                    ],
                }
            ),
            assignee=AGENT_NAME,
        )
        evidence["chrome_task_id"] = chrome_task["id"]

        outcome: dict[str, Any] = {}

        def run_agent() -> None:
            try:
                outcome["result"] = agent.run_once()
            except Exception as exc:  # surfaced to the caller below
                outcome["error"] = exc

        thread = threading.Thread(target=run_agent, daemon=True)
        thread.start()
        approval_id = None
        deadline = time.time() + 120
        while time.time() < deadline and approval_id is None:
            if "error" in outcome:
                raise outcome["error"]
            task_now = requester.get_task(int(chrome_task["id"]))
            for comment in (task_now or {}).get("comments", []):
                m = _APPROVAL_ID_RE.search(str(comment.get("body", "")))
                if m:
                    approval_id = m.group(1)
                    break
            if approval_id is None:
                time.sleep(0.5)
        assert approval_id, "no APPROVAL NEEDED comment appeared on the task"
        evidence["approval_id"] = approval_id
        requester.send_message(AGENT_NAME, f"APPROVE {approval_id}")
        log(f"requester approved {approval_id} over the bus")
        thread.join(timeout=180)
        if "error" in outcome:
            raise outcome["error"]
        result_b = outcome.get("result")
        assert result_b is not None, "agent did not run the chromium task"
        evidence["chrome_result"] = result_b
        trail_b = requester.get_file(result_b["trail_file"])
        trail_events = [
            json.loads(line)
            for line in (trail_b.get("text") or "").splitlines()
            if line.strip()
        ]
        approvals = [ev for ev in trail_events if ev.get("type") == "approval"]
        evidence["chrome_trail_events"] = len(trail_events)
        evidence["approval_events"] = approvals
        task_final = requester.get_task(int(chrome_task["id"]))
        evidence["chrome_task_status"] = (task_final or {}).get("status")
        evidence["chrome_task_result"] = (task_final or {}).get("result")
        return evidence
    finally:
        fixture_server.shutdown()
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
