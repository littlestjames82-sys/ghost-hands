"""Command line: ghost-hands demo | bench | run | export | mcp |
bus-agent | bus-demo | android-status | phone-approval-test."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .actions import Action
from .deciders import ScriptedDecider
from .drivers import DEMO_PAGES, ChromiumDriver, FakeDriver, demo_steps
from .errors import HandsError
from .export import export_ghost_hands_script
from .governor import Policy
from .policies import load_pack, pack_names
from .runner import Runner
from .trail import Trail, read_trail


def _print_report(report, trail: Trail) -> None:
    print(f"stop: {report.stop_reason} — {report.summary}")
    print(f"steps executed: {report.steps}")
    print(f"actions by class: {report.actions_by_class}")
    print(
        f"perception: {report.map_chars} map chars "
        f"(~{report.est_tokens} tokens at chars/4)"
    )
    if report.trail_path:
        print(f"trail: {report.trail_path}")


def cmd_demo(args) -> int:
    driver = FakeDriver(DEMO_PAGES)
    trail = Trail()
    runner = Runner(driver, ScriptedDecider(demo_steps()), trail=trail)
    report = runner.run("demo: search for 'ghost', open the result, add a todo")
    for ev in trail.events:
        if ev["type"] == "execute":
            action = ev["action"]
            label = action["kind"]
            if "target" in action:
                label += f" [{action['target']}]"
            if action.get("text"):
                label += f" {action['text']!r}"
            if action.get("url"):
                label += f" {action['url']}"
            print(f"  step {ev['step']}: {label} ({ev.get('classification')})")
        elif ev["type"] == "result":
            print(f"         -> {ev['result']}")
    _print_report(report, trail)
    return 0 if report.stop_reason == "done" else 1


def cmd_bench(args) -> int:
    from .bench import run_bench

    return run_bench(live=getattr(args, "live", False))


def _prompt_approver(action: Action) -> bool:
    try:
        answer = input(f"Approve consequential action {action.to_dict()}? [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def cmd_run(args) -> int:
    raw = json.loads(Path(args.script).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        steps = raw["steps"]
        pages = raw.get("pages")
        start = raw.get("start")
    else:
        steps, pages, start = raw, None, None

    policy = Policy()
    if args.policy and getattr(args, "policy_pack", None):
        print(
            "error: --policy and --policy-pack are alternatives; pass one",
            file=sys.stderr,
        )
        return 2
    if getattr(args, "policy_pack", None):
        try:
            policy = load_pack(args.policy_pack).policy
        except HandsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    elif args.policy:
        policy = Policy.from_dict(json.loads(Path(args.policy).read_text(encoding="utf-8")))

    approver_name = getattr(args, "approver", None)
    if args.approve_all and approver_name:
        print(
            "error: --approve-all and --approver are alternatives; pass one",
            file=sys.stderr,
        )
        return 2

    trail = Trail(args.trail)
    phone_approver = None
    if args.approve_all:
        approver = lambda action: True  # noqa: E731 - the explicit opt-in
    elif approver_name == "phone":
        from .phone_approver import PhoneApprover

        try:
            phone_approver = PhoneApprover(
                bridge_url=getattr(args, "android_bridge", None),
                token=getattr(args, "android_token", None),
                trail=trail,
            )
        except HandsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        approver = phone_approver
    else:
        approver = _prompt_approver

    if args.driver == "chromium":
        driver = ChromiumDriver()
    elif args.driver == "android":
        from .android_driver import AndroidDriver

        driver = AndroidDriver(
            bridge_url=getattr(args, "android_bridge", None),
            token=getattr(args, "android_token", None),
        )
    elif args.driver == "simphone":
        from .simphone import SimPhoneDriver

        driver = SimPhoneDriver()
    else:
        driver = FakeDriver(pages or DEMO_PAGES, start_url=start)

    decider = ScriptedDecider(steps)
    if phone_approver is not None:
        from .phone_approver import ObservingDecider

        decider = ObservingDecider(decider, phone_approver)
    runner = Runner(
        driver,
        decider,
        policy=policy,
        approver=approver,
        trail=trail,
        start_url=start if args.driver in ("chromium", "android", "simphone") else None,
    )
    try:
        report = runner.run(args.goal or "")
    finally:
        driver.close()
    _print_report(report, trail)
    return 0 if report.stop_reason == "done" else 1


def cmd_export(args) -> int:
    events = read_trail(args.trail_file)
    pages = None
    if args.pages:
        pages = json.loads(Path(args.pages).read_text(encoding="utf-8"))
        if isinstance(pages, dict) and "pages" in pages:
            pages = pages["pages"]
    script = export_ghost_hands_script(
        events, driver=args.driver, start_url=args.start_url, pages=pages
    )
    if args.output:
        Path(args.output).write_text(script, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(script)
    return 0


def cmd_mcp(args) -> int:
    from .mcp_server import main as mcp_main

    mcp_main()
    return 0


def _bus_client_from_args(args):
    from .busclient import BusClient

    bus_url = (
        getattr(args, "bus_url", None)
        or os.environ.get("GHOSTBUS_URL")
        or "http://127.0.0.1:8377"
    )
    workspace = getattr(args, "workspace", None) or os.environ.get(
        "GHOSTBUS_WORKSPACE"
    )
    key = getattr(args, "key", None) or os.environ.get("GHOSTBUS_KEY")
    return BusClient(bus_url, workspace=workspace, key=key)


def cmd_bus_agent(args) -> int:
    from .busagent import BusAgent
    from .busclient import BusError

    try:
        pack = load_pack(args.policy_pack) if args.policy_pack else None
    except HandsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    channel = (
        getattr(args, "approval_channel", None)
        or os.environ.get("GHOST_HANDS_APPROVAL_CHANNEL")
        or "bus"
    ).strip().lower()
    if channel not in ("bus", "phone"):
        print(
            f"error: unknown approval channel {channel!r} — use bus or phone",
            file=sys.stderr,
        )
        return 2
    client = _bus_client_from_args(args)
    agent = BusAgent(
        client,
        policy_pack=pack,
        approval_timeout=args.approval_timeout,
        approval_channel=channel,
        step_budget=args.step_budget,
        forced_driver=args.driver,
        log=lambda msg: print(f"[bus-agent] {msg}", flush=True),
    )
    try:
        agent.register()
    except BusError as exc:
        print(f"error: cannot reach GhostBus at {client.base_url}: {exc}",
              file=sys.stderr)
        return 1
    if args.once:
        result = agent.run_once()
        if result is None:
            print("[bus-agent] no hands tasks queued")
            return 0
        print(json.dumps(result, indent=2))
        return 0 if result.get("status") in ("done",) else 1
    print("[bus-agent] serving — Ctrl-C to stop")
    try:
        agent.serve(poll_interval=args.poll_interval, max_tasks=args.max_tasks)
    except KeyboardInterrupt:
        print("[bus-agent] stopped")
    return 0


def cmd_android_status(args) -> int:
    from .android_driver import AndroidDriver

    try:
        driver = AndroidDriver(
            bridge_url=getattr(args, "android_bridge", None),
            token=getattr(args, "android_token", None),
        )
        status = driver.status()
    except HandsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"bridge: {driver.bridge_url}")
    print(f"app: {status.get('app')} {status.get('version')}")
    print(f"protocol: {status.get('protocol')}")
    print(f"service connected: {status.get('service_connected')}")
    print(f"foreground package: {status.get('foreground_package')}")
    print(f"api level: {status.get('api_level')}")
    print(
        "screenshot: "
        + ("supported" if driver.capabilities.get("screenshot") else "needs API 30+")
    )
    return 0


def cmd_phone_approval_test(args) -> int:
    """Ryan's one-command on-device proof: a harmless test approval
    travels to the phone; approving it runs nothing."""
    from .phone_approver import PhoneApprover

    try:
        approver = PhoneApprover(
            bridge_url=getattr(args, "android_bridge", None),
            token=getattr(args, "android_token", None),
            timeout=args.timeout,
            poll_interval=args.poll_interval,
        )
    except HandsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        "test approval sent to the phone — approve or deny it in "
        "MrGhosty (notification or Device tab)…",
        flush=True,
    )
    status = approver.test_approval()
    if approver.last_error:
        print(f"ERROR: {approver.last_error}")
        return 1
    print(status.upper())
    return 0 if status == "approved" else 1


def cmd_bus_demo(args) -> int:
    from .busagent import BusDemoUnavailable, run_bus_demo

    try:
        evidence = run_bus_demo(
            log=lambda msg: print(f"[bus-demo] {msg}", flush=True)
        )
    except BusDemoUnavailable as exc:
        print(f"BUS DEMO UNAVAILABLE: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(evidence, indent=2, default=str))
    ok = (
        evidence["fake_result"]["status"] == "done"
        and evidence["chrome_result"]["status"] == "done"
        and evidence["chrome_task_status"] == "done"
    )
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghost-hands",
        description="Governed agent hands for the web — our own CDP stack, zero dependencies.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="run the scripted demo on the fake mini-web")
    p_bench = sub.add_parser("bench", help="run the Ghost Hands bench suite")
    p_bench.add_argument(
        "--live",
        action="store_true",
        help="also run the live-network cases (example.com, Wikipedia)",
    )

    p_run = sub.add_parser("run", help="run scripted steps from a JSON file")
    p_run.add_argument("--script", required=True, help="JSON steps file")
    p_run.add_argument(
        "--driver",
        choices=("fake", "chromium", "android", "simphone"),
        default="fake",
    )
    p_run.add_argument(
        "--approver",
        choices=("prompt", "phone"),
        help="who answers 'ask' verdicts: the terminal prompt (default) "
        "or Ryan's phone via the MrGhosty bridge (v0.6; works with "
        "every driver). Conflicts with --approve-all.",
    )
    p_run.add_argument(
        "--android-bridge",
        help="MrGhosty bridge URL for --driver android "
        "(env GHOST_HANDS_ANDROID_BRIDGE; default http://127.0.0.1:8378)",
    )
    p_run.add_argument(
        "--android-token",
        help="Ghost Hands pairing token for --driver android — prefer the "
        "GHOST_HANDS_ANDROID_TOKEN env var; the token is never stored",
    )
    p_run.add_argument("--policy", help="JSON policy file")
    p_run.add_argument(
        "--policy-pack",
        choices=pack_names(),
        help="named policy pack (alternative to --policy): readonly | standard | strict",
    )
    p_run.add_argument("--approve-all", action="store_true",
                       help="approve every consequential action without asking")
    p_run.add_argument("--trail", default="ghost_hands_trail.jsonl",
                       help="trail output path (default: ghost_hands_trail.jsonl)")
    p_run.add_argument("--goal", default="", help="goal text recorded in the report")

    p_export = sub.add_parser("export", help="graduate a trail into a Ghost Hands script")
    p_export.add_argument("trail_file")
    p_export.add_argument("-o", "--output", help="output .py path (default: stdout)")
    p_export.add_argument(
        "--driver",
        choices=("chromium", "fake"),
        default="chromium",
        help="body the replay script drives (default: chromium)",
    )
    p_export.add_argument(
        "--start-url",
        help="page the replay opens first (default: the trail's first perceived URL)",
    )
    p_export.add_argument(
        "--pages",
        help="JSON file with the fake mini-web to embed (for --driver fake)",
    )

    sub.add_parser("mcp", help="serve the MCP tools on stdio")

    p_bus = sub.add_parser(
        "bus-agent",
        help="work as the ghost-hands agent on a GhostBus workspace",
    )
    p_bus.add_argument("--bus-url",
                       help="GhostBus base URL (env GHOSTBUS_URL; default http://127.0.0.1:8377)")
    p_bus.add_argument("--workspace",
                       help="hosted workspace id — API lives under /w/<id>/ (env GHOSTBUS_WORKSPACE)")
    p_bus.add_argument("--key",
                       help="workspace key (env GHOSTBUS_KEY; never stored)")
    p_bus.add_argument("--policy-pack", choices=pack_names(),
                       help="policy pack for bus runs (default: standard)")
    p_bus.add_argument("--approval-timeout", type=float, default=None,
                       help="seconds to wait for an approval (default: the pack's, 600 for standard)")
    p_bus.add_argument("--approval-channel", choices=("bus", "phone"), default=None,
                       help="where 'ask' verdicts go: the bus (default) or Ryan's "
                       "phone via the MrGhosty bridge (env GHOST_HANDS_APPROVAL_CHANNEL). "
                       "With phone, no approval is ever requested FROM the bus — the "
                       "bus only gets a comment with the phone's decision.")
    p_bus.add_argument("--poll-interval", type=float, default=5.0)
    p_bus.add_argument("--step-budget", type=int, default=30)
    p_bus.add_argument("--driver", choices=("fake", "chromium", "simphone", "android"),
                       help="force a body for every task (default: per-task auto); "
                       "android is configured via GHOST_HANDS_ANDROID_BRIDGE / "
                       "GHOST_HANDS_ANDROID_TOKEN")
    p_bus.add_argument("--once", action="store_true",
                       help="handle at most one task, then exit")
    p_bus.add_argument("--max-tasks", type=int, default=None,
                       help="stop after N tasks (default: serve forever)")

    sub.add_parser(
        "bus-demo",
        help="prove bus-agent end to end against a local real GhostBus server",
    )

    p_android = sub.add_parser(
        "android-status",
        help="check the MrGhosty Ghost Hands bridge on the phone",
    )
    p_android.add_argument(
        "--android-bridge",
        help="bridge URL (env GHOST_HANDS_ANDROID_BRIDGE; "
        "default http://127.0.0.1:8378)",
    )
    p_android.add_argument(
        "--android-token",
        help="pairing token — prefer the GHOST_HANDS_ANDROID_TOKEN env "
        "var; never printed, never stored",
    )

    p_phone_test = sub.add_parser(
        "phone-approval-test",
        help="prove phone approvals: send a harmless test approval to "
        "the phone and wait for Ryan's decision (approving runs nothing)",
    )
    p_phone_test.add_argument(
        "--android-bridge",
        help="bridge URL (env GHOST_HANDS_ANDROID_BRIDGE; "
        "default http://127.0.0.1:8378)",
    )
    p_phone_test.add_argument(
        "--android-token",
        help="pairing token — prefer the GHOST_HANDS_ANDROID_TOKEN env "
        "var; never printed, never stored",
    )
    p_phone_test.add_argument("--timeout", type=float, default=600.0,
                              help="seconds to wait for the phone's decision (default 600)")
    p_phone_test.add_argument("--poll-interval", type=float, default=1.0,
                              help="seconds between status polls (default 1)")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "demo": cmd_demo,
        "bench": cmd_bench,
        "run": cmd_run,
        "export": cmd_export,
        "mcp": cmd_mcp,
        "bus-agent": cmd_bus_agent,
        "bus-demo": cmd_bus_demo,
        "android-status": cmd_android_status,
        "phone-approval-test": cmd_phone_approval_test,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
