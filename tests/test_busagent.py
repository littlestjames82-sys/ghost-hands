"""BusAgent (v0.4) against the REAL GhostBus node server.

Each test boots a fresh server (ephemeral port, :memory: store, a
workspace key) from ~/workspace/ghostbus — no fake bus. If node or the
GhostBus source is unavailable the whole module skips, loudly.
"""

import json
import re
import shutil
import threading
import time
from pathlib import Path

import pytest

from ghost_hands.busagent import (
    BusAgent,
    blockers_open,
    boot_ghostbus_server,
    default_ghostbus_dir,
    is_for_hands,
    parse_task_body,
)
from ghost_hands.busclient import BusClient
from ghost_hands.drivers import DEMO_PAGES
from pages_fixtures import SHOP_PAGES

GHOSTBUS_DIR = default_ghostbus_dir()
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not (GHOSTBUS_DIR / "src" / "http-server.mjs").exists(),
    reason="node or GhostBus source unavailable — real-bus tests cannot run",
)

KEY = "test-key"
APPROVAL_RE = re.compile(r"reply APPROVE (\S+) or DENY")


@pytest.fixture
def bus():
    proc, base = boot_ghostbus_server(GHOSTBUS_DIR, key=KEY)
    yield base
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()


def make_client(base, name):
    client = BusClient(base, key=KEY, agent_name=name)
    client.register(role="tester")
    return client


def make_agent(base, **kwargs):
    client = BusClient(base, key=KEY, agent_name="ghost-hands")
    agent = BusAgent(client, log=lambda m: None, **kwargs)
    agent.register()
    return agent


def shop_task_body(steps):
    return json.dumps(
        {
            "goal": "check out",
            "pages": SHOP_PAGES,
            "start_url": "https://shop.local/",
            "steps": steps,
        }
    )


# -- parsing + addressing (pure) -------------------------------------------------


def test_parse_json_body():
    spec = parse_task_body(
        {
            "title": "T",
            "body": json.dumps(
                {"goal": "g", "url": "https://x.local/", "steps": [{"kind": "extract"}]}
            ),
        }
    )
    assert spec.goal == "g"
    assert spec.start_url == "https://x.local/"
    assert spec.steps == [{"kind": "extract"}]
    assert spec.executable


def test_parse_line_body():
    spec = parse_task_body(
        {"title": "T", "body": "goal: read the page\nurl: https://x.local/a"}
    )
    assert spec.goal == "read the page"
    assert spec.start_url == "https://x.local/a"


def test_parse_title_fallback():
    # A title-only task drives the RuleDecider with the title as goal.
    spec = parse_task_body({"title": "Just do it", "body": ""})
    assert spec.goal == "Just do it"
    assert spec.executable
    empty = parse_task_body({"title": "", "body": ""})
    assert not empty.executable


def test_is_for_hands_matrix():
    assert is_for_hands({"assignee": "ghost-hands", "title": "", "body": ""})
    assert not is_for_hands({"assignee": "someone-else", "title": "", "body": ""})
    assert is_for_hands({"assignee": None, "title": "[hands] do it", "body": ""})
    assert is_for_hands(
        {"assignee": None, "title": "t", "body": "tags: web, hands\nbody"}
    )
    assert is_for_hands(
        {"assignee": None, "title": "t", "body": json.dumps({"tags": ["hands"]})}
    )
    assert not is_for_hands({"assignee": None, "title": "plain task", "body": "for anyone"})


def test_blockers_open():
    tasks = [
        {"id": 1, "status": "done"},
        {"id": 2, "status": "claimed"},
        {"id": 3, "status": "cancelled"},
    ]
    assert blockers_open({"blockedBy": [1, 3]}, tasks) == []
    assert blockers_open({"blockedBy": [1, 2]}, tasks) == [2]
    assert blockers_open({"blockedBy": [99]}, tasks) == [99]


# -- end to end on the real bus ------------------------------------------------------


def test_agent_runs_fake_task_and_uploads_trail(bus):
    requester = make_client(bus, "requester")
    agent = make_agent(bus)
    task = requester.create_task(
        title="Run the demo flow",
        body=json.dumps(
            {
                "goal": "demo",
                "pages": DEMO_PAGES,
                "start_url": "https://demo.local/",
                "steps": [{"kind": "extract"}],
            }
        ),
        assignee="ghost-hands",
    )
    result = agent.run_once()
    assert result is not None and result["status"] == "done"
    final = requester.get_task(task["id"])
    assert final["status"] == "done"
    posted = json.loads(final["result"])
    assert posted["trail_file"] == result["trail_file"]
    trail = requester.get_file(result["trail_file"])
    events = [json.loads(line) for line in trail["text"].splitlines()]
    types = [ev["type"] for ev in events]
    assert types[0] == "perceive" and types[-1] == "stop"
    assert agent.run_once() is None  # queue drained


def test_unaddressed_task_is_not_grabbed(bus):
    requester = make_client(bus, "requester")
    agent = make_agent(bus)
    requester.create_task(
        title="For anyone",
        body=json.dumps({"goal": "g", "steps": [{"kind": "extract"}]}),
    )
    assert agent.run_once() is None


def test_needs_approval_gate_is_respected(bus):
    requester = make_client(bus, "requester")
    agent = make_agent(bus)
    task = requester.create_task(
        title="Gated task",
        body=json.dumps(
            {
                "goal": "look",
                "pages": DEMO_PAGES,
                "start_url": "https://demo.local/",
                "steps": [{"kind": "extract"}],
            }
        ),
        assignee="ghost-hands",
        needs_approval=True,
    )
    assert agent.run_once() is None  # not claimed while gated
    assert requester.get_task(task["id"])["status"] == "needs-approval"
    requester.approve_task(task["id"])
    result = agent.run_once()
    assert result is not None and result["status"] == "done"


def test_blocked_task_waits_for_its_blocker(bus):
    requester = make_client(bus, "requester")
    other = make_client(bus, "other-agent")
    agent = make_agent(bus)
    blocker = requester.create_task(
        title="Blocker", body="goal: something else", assignee="other-agent"
    )
    blocked = requester.create_task(
        title="Blocked hands task",
        body=json.dumps(
            {
                "goal": "look",
                "pages": DEMO_PAGES,
                "start_url": "https://demo.local/",
                "steps": [{"kind": "extract"}],
            }
        ),
        assignee="ghost-hands",
        blocked_by=[blocker["id"]],
    )
    assert agent.run_once() is None
    other.claim_task(blocker["id"])
    other.complete_task(blocker["id"], result="done")
    result = agent.run_once()
    assert result is not None and result["task_id"] == blocked["id"]


# -- approvals over the bus ------------------------------------------------------


def _run_agent_thread(agent):
    outcome = {}

    def _run():
        try:
            outcome["result"] = agent.run_once()
        except Exception as exc:  # surfaced by the assertion below
            outcome["error"] = exc

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread, outcome


def _await_approval_id(requester, task_id, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = requester.get_task(task_id)
        for comment in (task or {}).get("comments", []):
            m = APPROVAL_RE.search(str(comment.get("body", "")))
            if m:
                return m.group(1)
        time.sleep(0.25)
    raise AssertionError("no APPROVAL NEEDED comment appeared")


def test_approval_approve_over_the_bus(bus):
    requester = make_client(bus, "requester")
    agent = make_agent(bus, approval_timeout=30)
    task = requester.create_task(
        title="Checkout (approve)",
        body=shop_task_body(
            [{"kind": "click", "target": 1}, {"kind": "click", "target": 1}]
        ),
        assignee="ghost-hands",
    )
    thread, outcome = _run_agent_thread(agent)
    approval_id = _await_approval_id(requester, task["id"])
    assert approval_id.startswith("gh-")
    requester.send_message("ghost-hands", f"APPROVE {approval_id}")
    thread.join(timeout=60)
    assert "error" not in outcome, outcome.get("error")
    result = outcome["result"]
    assert result["status"] == "done"
    trail = requester.get_file(result["trail_file"])
    events = [json.loads(line) for line in trail["text"].splitlines()]
    approvals = [ev for ev in events if ev["type"] == "approval"]
    assert [ev["phase"] for ev in approvals] == ["request", "decision"]
    assert approvals[0]["channel"] == "bus"
    assert approvals[1]["approved"] is True
    assert approvals[1]["by"] == "requester"
    executes = [ev for ev in events if ev["type"] == "execute"]
    assert len(executes) == 2  # link click + the approved submit


def test_approval_deny_over_the_bus(bus):
    requester = make_client(bus, "requester")
    agent = make_agent(bus, approval_timeout=30)
    task = requester.create_task(
        title="Checkout (deny)",
        body=shop_task_body(
            [{"kind": "click", "target": 1}, {"kind": "click", "target": 1}]
        ),
        assignee="ghost-hands",
    )
    thread, outcome = _run_agent_thread(agent)
    approval_id = _await_approval_id(requester, task["id"])
    requester.send_message("ghost-hands", f"DENY {approval_id}")
    thread.join(timeout=60)
    assert "error" not in outcome, outcome.get("error")
    result = outcome["result"]
    assert result["status"] == "denied"
    trail = requester.get_file(result["trail_file"])
    events = [json.loads(line) for line in trail["text"].splitlines()]
    approvals = [ev for ev in events if ev["type"] == "approval"]
    assert approvals[-1]["approved"] is False
    executes = [ev for ev in events if ev["type"] == "execute"]
    assert len(executes) == 1  # the submit never executed


def test_approval_timeout_denies(bus):
    requester = make_client(bus, "requester")
    agent = make_agent(bus, approval_timeout=1.5)
    task = requester.create_task(
        title="Checkout (nobody answers)",
        body=shop_task_body(
            [{"kind": "click", "target": 1}, {"kind": "click", "target": 1}]
        ),
        assignee="ghost-hands",
    )
    result = agent.run_once()  # main thread; the wait just times out
    assert result is not None and result["status"] == "denied"
    trail = requester.get_file(result["trail_file"])
    events = [json.loads(line) for line in trail["text"].splitlines()]
    decisions = [
        ev for ev in events if ev["type"] == "approval" and ev["phase"] == "decision"
    ]
    assert decisions and decisions[0]["approved"] is False
    assert "timeout" in decisions[0]["reason"]
    # The request was still posted as a comment for the record.
    comments = requester.get_task(task["id"])["comments"]
    assert any("APPROVAL NEEDED" in c["body"] for c in comments)


def test_hosted_workspace_prefix_end_to_end(tmp_path):
    """The hosted server shape: workspaces under /w/<id>/, each with its
    own key. Create one via the admin API, then run a full agent task
    through BusClient(workspace=..., key=...) — the exact configuration
    `ghost-hands bus-agent --workspace` uses."""
    import os
    import subprocess
    import urllib.request

    from ghost_hands.busagent import _free_port, _wait_health
    from ghost_hands.busclient import BusError

    port = _free_port()
    env = dict(os.environ, GHOSTBUS_ADMIN_KEY="admin-key")
    proc = subprocess.Popen(
        [
            shutil.which("node"),
            str(GHOSTBUS_DIR / "src" / "hosted-server.mjs"),
            "--port",
            str(port),
            "--data-dir",
            str(tmp_path / "hosted"),
        ],
        cwd=str(GHOSTBUS_DIR),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        assert _wait_health(base, seconds=15)
        req = urllib.request.Request(
            base + "/api/workspaces",
            data=json.dumps({"id": "hands-test", "name": "Hands Test"}).encode(),
            headers={"Content-Type": "application/json", "x-bus-key": "admin-key"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            created = json.loads(resp.read())
        ws_key = created["key"]

        agent_client = BusClient(base, workspace="hands-test", key=ws_key,
                                 agent_name="ghost-hands")
        agent = BusAgent(agent_client, log=lambda m: None)
        agent.register()
        requester = BusClient(base, workspace="hands-test", key=ws_key,
                              agent_name="requester")
        requester.register(role="requester")
        task = requester.create_task(
            title="Hosted workspace task",
            body=json.dumps(
                {
                    "goal": "look",
                    "pages": DEMO_PAGES,
                    "start_url": "https://demo.local/",
                    "steps": [{"kind": "extract"}],
                }
            ),
            assignee="ghost-hands",
        )
        result = agent.run_once()
        assert result is not None and result["status"] == "done"
        assert requester.get_task(task["id"])["status"] == "done"
        assert requester.get_file(result["trail_file"])["text"]

        # A wrong workspace key is refused.
        impostor = BusClient(base, workspace="hands-test", key="wrong",
                             agent_name="ghost-hands")
        with pytest.raises(BusError):
            impostor.list_tasks()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def test_bus_demo_end_to_end():
    """The shipped bus-demo harness itself (it boots its own server)."""
    from ghost_hands.busagent import run_bus_demo

    evidence = run_bus_demo(approval_timeout=30)
    assert evidence["server"].startswith("real GhostBus")
    assert evidence["fake_result"]["status"] == "done"
    assert evidence["chrome_result"]["status"] == "done"
    assert evidence["chrome_task_status"] == "done"
    assert evidence["approval_events"][-1]["approved"] is True


# ---------------------------------------------------------------------------
# v0.6: the phone approval channel — the bus carries the work, the
# phone carries the conscience. No approval is requested FROM the bus.
# ---------------------------------------------------------------------------


def test_bus_agent_rejects_unknown_approval_channel():
    from ghost_hands.busagent import BusAgent as _BA
    from ghost_hands.errors import HandsError

    with pytest.raises(HandsError, match="approval channel"):
        _BA(object(), approval_channel="smoke-signals")


def test_bus_agent_phone_approval_channel(bus, monkeypatch):
    from fake_android_bridge import TOKEN as PHONE_TOKEN, make_bridge

    server, phone_url, phone_state = make_bridge()
    try:
        monkeypatch.setenv("GHOST_HANDS_ANDROID_BRIDGE", phone_url)
        monkeypatch.setenv("GHOST_HANDS_ANDROID_TOKEN", PHONE_TOKEN)
        requester = make_client(bus, "requester")
        agent = make_agent(bus, approval_channel="phone", approval_timeout=30)
        task = requester.create_task(
            title="Phone-approved checkout",
            body=shop_task_body(
                [
                    {"kind": "click", "target": 1},  # View cart
                    {"kind": "click", "target": 1},  # Place order (asks)
                ]
            ),
            assignee="ghost-hands",
        )
        outcome = {}

        def run_agent():
            try:
                outcome["result"] = agent.run_once()
            except Exception as exc:  # surfaced below
                outcome["error"] = exc

        thread = threading.Thread(target=run_agent, daemon=True)
        thread.start()
        # Ryan's finger: approve on the 'phone' when the ask appears.
        deadline = time.time() + 30
        decided = False
        while time.time() < deadline and not decided:
            ids = phone_state.pending_approval_ids()
            if ids:
                decided = phone_state.decide_approval(ids[0], True)
            else:
                time.sleep(0.05)
        assert decided, "no approval ever reached the phone"
        thread.join(timeout=60)
        if "error" in outcome:
            raise outcome["error"]
        result = outcome["result"]
        assert result is not None and result["status"] == "done", result

        # The uploaded trail carries channel=phone approval events…
        trail_text = requester.get_file(result["trail_file"])["text"]
        events = [json.loads(line) for line in trail_text.splitlines() if line.strip()]
        approvals = [ev for ev in events if ev["type"] == "approval"]
        assert approvals, events
        assert all(ev["channel"] == "phone" for ev in approvals)
        assert approvals[-1]["approved"] is True
        assert approvals[-1]["decided_by"] == "phone"

        # …and the bus heard the outcome as a comment — but was never
        # asked to decide anything itself.
        task_now = requester.get_task(task["id"])
        comments = " ".join(
            str(c.get("body", "")) for c in task_now.get("comments", [])
        )
        assert "phone approval approved" in comments
        assert "APPROVAL NEEDED" not in comments
        inbox = requester.inbox(unread_only=False, limit=50)
        bodies = " ".join(str(m.get("body", "")) for m in inbox)
        assert "APPROVAL NEEDED" not in bodies
    finally:
        server.shutdown()
