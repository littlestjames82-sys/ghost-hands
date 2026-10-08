"""Command line: ghost-hands demo | bench | run | export | mcp."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .actions import Action
from .deciders import ScriptedDecider
from .drivers import DEMO_PAGES, ChromiumDriver, FakeDriver, demo_steps
from .export import export_ghost_hands_script
from .governor import Policy
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
    if args.policy:
        policy = Policy.from_dict(json.loads(Path(args.policy).read_text(encoding="utf-8")))

    approver = (lambda action: True) if args.approve_all else _prompt_approver

    if args.driver == "chromium":
        driver = ChromiumDriver()
    else:
        driver = FakeDriver(pages or DEMO_PAGES, start_url=start)

    trail = Trail(args.trail)
    runner = Runner(
        driver,
        ScriptedDecider(steps),
        policy=policy,
        approver=approver,
        trail=trail,
        start_url=start if args.driver == "chromium" else None,
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
    p_run.add_argument("--driver", choices=("fake", "chromium"), default="fake")
    p_run.add_argument("--policy", help="JSON policy file")
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
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "demo": cmd_demo,
        "bench": cmd_bench,
        "run": cmd_run,
        "export": cmd_export,
        "mcp": cmd_mcp,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
