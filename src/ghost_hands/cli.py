"""Command line: ghost-hands demo | bench | run | export | mcp |
bus-agent | bus-demo | android-status | phone-approval-test | eval |
report | doctor | phone-proof."""

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

    # v1.5: --resume <trail> seeds this run from a dead run's trail:
    # start where it stopped (unless the script names its own start)
    # and tell the decider what already happened.
    initial_history = None
    if getattr(args, "resume", None):
        from .resume import load_resume_context, resume_note

        try:
            resume_ctx = load_resume_context(args.resume)
        except (OSError, ValueError) as exc:
            print(f"error: cannot read resume trail: {exc}", file=sys.stderr)
            return 2
        initial_history = [
            {"kind": "system", "note": resume_note(resume_ctx)}
        ]
        if not start and resume_ctx.get("start_url"):
            start = resume_ctx["start_url"]

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
    plan_cache = None
    if getattr(args, "cache", None):
        from .cache import PlanCache

        plan_cache = PlanCache(args.cache)
    runner = Runner(
        driver,
        decider,
        policy=policy,
        approver=approver,
        trail=trail,
        start_url=start if args.driver in ("chromium", "android", "simphone") else None,
        plan_cache=plan_cache,
        require_verify=bool(getattr(args, "require_verify", False)),
        max_llm_tokens=getattr(args, "max_llm_tokens", None),
        recovery_attempts=int(getattr(args, "recovery_attempts", 1)),
        initial_history=initial_history,
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


def cmd_verify_trail(args) -> int:
    """Verify a trail's v1.0 hash chain: intact, tampered, or legacy."""
    from .trail import read_trail, verify_trail

    try:
        events = read_trail(args.trail_file)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read trail: {exc}", file=sys.stderr)
        return 2
    verdict = verify_trail(events)
    print(f"events: {verdict['checked']} ({verdict['hashed']} hashed, "
          f"{verdict['legacy']} legacy)")
    print(f"verdict: {verdict['reason']}")
    if verdict["ok"]:
        return 0
    return 2 if verdict["hashed"] == 0 else 1


def cmd_batch(args) -> int:
    """v1.6: run several run scripts as one runbook. Each script
    gets a fresh body and its own trail; asks are denied unless
    --approve-all (a batch never prompts). Prints one line per
    script plus a roll-up with total real LLM tokens; exit 0 only
    when every script ends 'done'."""
    from .drivers import DEMO_PAGES, ChromiumDriver, FakeDriver
    from .governor import Policy
    from .policies import load_pack

    policy = Policy()
    if getattr(args, "policy_pack", None):
        try:
            policy = load_pack(args.policy_pack).policy
        except HandsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    approver = (lambda action: True) if args.approve_all else None
    trail_dir = Path(args.trail_dir) if getattr(args, "trail_dir", None) else None
    if trail_dir:
        trail_dir.mkdir(parents=True, exist_ok=True)
    shared_driver = ChromiumDriver() if args.driver == "chromium" else None
    results: list[dict] = []
    try:
        for script_path in args.scripts:
            path = Path(script_path)
            name = path.stem
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    steps = raw["steps"]
                    pages = raw.get("pages")
                    start = raw.get("start")
                    goal = raw.get("goal") or name
                else:
                    steps, pages, start, goal = raw, None, None, name
                actions = [Action.from_dict(s) for s in steps]
            except (OSError, ValueError, KeyError, TypeError) as exc:
                print(f"{name}: INVALID SCRIPT — {exc}")
                results.append({"name": name, "stop_reason": "invalid"})
                continue
            if shared_driver is not None:
                driver = shared_driver
            else:
                driver = FakeDriver(pages or DEMO_PAGES, start_url=start)
            trail = Trail(trail_dir / f"{name}.jsonl") if trail_dir else Trail()
            runner = Runner(
                driver,
                ScriptedDecider([a.to_dict() for a in actions]),
                policy=policy,
                approver=approver,
                trail=trail,
                start_url=start if shared_driver is not None else None,
                require_verify=bool(getattr(args, "require_verify", False)),
            )
            try:
                report = runner.run(str(goal))
            finally:
                if shared_driver is None:
                    driver.close()
            results.append(
                {
                    "name": name,
                    "stop_reason": report.stop_reason,
                    "summary": report.summary,
                    "llm_calls": report.llm_calls,
                    "llm_tokens": report.llm_prompt_tokens
                    + report.llm_completion_tokens,
                }
            )
            print(
                f"{name}: {report.stop_reason} — {report.summary} "
                f"({report.steps} steps)"
            )
    finally:
        if shared_driver is not None:
            shared_driver.close()
    done = sum(1 for r in results if r["stop_reason"] == "done")
    total_tokens = sum(r.get("llm_tokens", 0) for r in results)
    total_calls = sum(r.get("llm_calls", 0) for r in results)
    print(
        f"BATCH: {done}/{len(results)} done; "
        f"{total_tokens:,} LLM tokens across {total_calls} model calls "
        "(scripted runs report 0 — they make none)"
    )
    return 0 if done == len(results) else 1


def cmd_check(args) -> int:
    """v1.5: preflight a run script against the Governor — without
    executing anything. Every step is validated, classified, and
    evaluated under the chosen policy; secret placeholders are
    checked for presence in the environment (names only, values
    never read or printed). Exit 1 when any step would be denied,
    is invalid, or names a missing secret; asks are reported, not
    failures — they are the approval prompts a real run would raise."""
    from .actions import Action
    from .governor import Governor, Policy
    from .policies import load_pack
    from .secrets import _env_key, action_secret_refs

    try:
        raw = json.loads(Path(args.script).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"error: cannot read script: {exc}", file=sys.stderr)
        return 2
    if isinstance(raw, dict):
        steps = raw.get("steps")
        start = raw.get("start") or ""
    else:
        steps, start = raw, ""
    if not isinstance(steps, list) or not all(
        isinstance(s, dict) for s in steps
    ):
        print(
            "error: script must be a list of action objects or an "
            "object with a 'steps' list",
            file=sys.stderr,
        )
        return 2
    policy = Policy()
    if getattr(args, "policy_pack", None):
        try:
            policy = load_pack(args.policy_pack).policy
        except HandsError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    elif getattr(args, "policy", None):
        try:
            policy = Policy.from_dict(
                json.loads(Path(args.policy).read_text(encoding="utf-8"))
            )
        except (OSError, ValueError) as exc:
            print(f"error: cannot read policy: {exc}", file=sys.stderr)
            return 2
    governor = Governor(policy)
    counts = {"allow": 0, "ask": 0, "deny": 0, "invalid": 0}
    missing_secrets: list[str] = []
    for i, step in enumerate(steps, start=1):
        try:
            action = Action.from_dict(step)
        except (ValueError, TypeError) as exc:
            counts["invalid"] += 1
            print(f"step {i}: INVALID — {exc}")
            continue
        decision = governor.evaluate(action, None, start)
        counts[decision.outcome] = counts.get(decision.outcome, 0) + 1
        notes = []
        if decision.outcome != "allow":
            notes.append(decision.reason)
        for ref in action_secret_refs(action):
            if os.environ.get(_env_key(ref)):
                notes.append(f"secret {ref}: set")
            else:
                notes.append(f"secret {ref}: MISSING ({_env_key(ref)} not set)")
                missing_secrets.append(ref)
        suffix = (" — " + "; ".join(notes)) if notes else ""
        print(
            f"step {i}: {action.kind} [{decision.classification}] "
            f"-> {decision.outcome}{suffix}"
        )
    print(
        f"check: {len(steps)} step(s): {counts['allow']} allow, "
        f"{counts['ask']} ask, {counts['deny']} deny, "
        f"{counts['invalid']} invalid; "
        f"{len(missing_secrets)} missing secret(s)"
    )
    if counts["deny"] or counts["invalid"] or missing_secrets:
        return 1
    return 0


def cmd_costs(args) -> int:
    """v1.4: aggregate real LLM token spend across trail files.

    Reads each trail's stop event (via report.summarize) and totals
    the API-reported usage recorded since v1.2. Trails from runs
    whose decider reported no usage are counted as unreported —
    never estimated into the totals."""
    from .report import summarize
    from .trail import read_trail

    rows: list[dict] = []
    totals = {"runs": 0, "calls": 0, "prompt": 0, "completion": 0, "unreported": 0}
    for path in args.trail_files:
        try:
            events = read_trail(path)
        except (OSError, ValueError) as exc:
            print(f"error: cannot read trail {path}: {exc}", file=sys.stderr)
            return 2
        info = summarize(events)
        usage = info.get("llm_usage") or {}
        row = {
            "file": str(path),
            "goal": info.get("goal", ""),
            "stop_reason": info.get("stop_reason", ""),
            "calls": int(usage.get("llm_calls") or 0),
            "prompt": int(usage.get("llm_prompt_tokens") or 0),
            "completion": int(usage.get("llm_completion_tokens") or 0),
        }
        row["total"] = row["prompt"] + row["completion"]
        rows.append(row)
        totals["runs"] += 1
        if row["calls"]:
            totals["calls"] += row["calls"]
            totals["prompt"] += row["prompt"]
            totals["completion"] += row["completion"]
        else:
            totals["unreported"] += 1
    if getattr(args, "json", False):
        print(json.dumps({"runs": rows, "totals": totals}, indent=2))
        return 0
    for row in rows:
        spend = (
            f"{row['total']:,} tok / {row['calls']} calls"
            if row["calls"]
            else "usage not reported"
        )
        print(f"{row['file']}: {row['goal'][:48]} [{row['stop_reason']}] {spend}")
    print(
        f"TOTAL: {totals['prompt'] + totals['completion']:,} tokens "
        f"({totals['prompt']:,} prompt + {totals['completion']:,} completion) "
        f"across {totals['calls']} model calls in {totals['runs']} run(s); "
        f"{totals['unreported']} run(s) reported no usage"
    )
    return 0


def cmd_mcp(args) -> int:
    from .mcp_server import main as mcp_main

    mcp_main()
    return 0


def cmd_report(args) -> int:
    """Render a trail JSONL as a self-contained HTML audit report
    (v0.8). Default output: the trail path with an .html suffix."""
    from .report import write_report

    try:
        target = write_report(args.trail_file, args.output)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {target}")
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


def cmd_eval(args) -> int:
    """The v0.7 model-evaluation harness: graded tasks, three
    deciders. Task failures are data; exit 0 means the harness ran."""
    from . import evaluate

    task_names = (
        [name.strip() for name in args.tasks.split(",") if name.strip()]
        if args.tasks
        else None
    )
    kinds = (
        ["stub", "rules", "openai"]
        if args.decider == "all"
        else [args.decider]
    )
    payloads: list[dict] = []
    for kind in kinds:
        notes: list[str] = []
        if kind == "openai":
            cfg = evaluate.resolve_real_model()
            if cfg is None:
                print(
                    "real-model run skipped: "
                    f"{evaluate.real_model_gap()}"
                )
                continue
            notes.append(
                "real model: "
                f"{cfg['model']} at {cfg['base_url']} "
                f"(source: {cfg['source']}); API key read from the "
                "environment, never printed or stored"
            )
        if kind == "stub":
            notes.append(evaluate.STUB_LABEL)
        try:
            scores, label = evaluate.run_eval(
                kind, task_names=task_names, budget=args.budget
            )
        except HandsError as exc:
            print(str(exc))
            return 2
        print(evaluate.format_suite(scores, label))
        print()
        payloads.append(evaluate.suite_json(scores, label, notes))
    if args.report and payloads:
        payload = (
            payloads[0] if len(payloads) == 1 else {"suites": payloads}
        )
        Path(args.report).write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        print(f"eval report written to {args.report}")
    if getattr(args, "scorecard", None) and payloads:
        Path(args.scorecard).write_text(
            evaluate.render_scorecard(payloads), encoding="utf-8"
        )
        print(f"eval scorecard written to {args.scorecard}")
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


def cmd_doctor(args) -> int:
    """One-command diagnostics for the whole stack (v0.9). Exit 1
    iff any check FAILs; --json emits the checks as structured data."""
    from .doctor import doctor_exit_code, doctor_json, format_doctor, run_doctor

    checks = run_doctor(
        bus_url=getattr(args, "bus", None),
        bridge_url=getattr(args, "android_bridge", None),
        bridge_token=getattr(args, "android_token", None),
        timeout=args.timeout,
    )
    if args.json:
        print(doctor_json(checks))
    else:
        print(format_doctor(checks))
    return doctor_exit_code(checks)


def cmd_phone_proof(args) -> int:
    """The guided on-device proof (v0.9): configuration, the adb
    forward, the bridge status, and the test approval — each step
    checked. Exit codes: 0 proven, 1 error, 2 needs-setup,
    3 not approved."""
    from .phone_proof import run_phone_proof

    input_fn = input if sys.stdin.isatty() else None
    return run_phone_proof(
        bridge_url=getattr(args, "android_bridge", None),
        token=getattr(args, "android_token", None),
        timeout=args.timeout,
        poll_interval=args.poll_interval,
        assume_yes=args.yes,
        input_fn=input_fn,
    )


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

    p_batch = sub.add_parser(
        "batch",
        help="run several scripts as one runbook (v1.6): a line per "
        "script plus a roll-up; asks are denied unless --approve-all",
    )
    p_batch.add_argument("scripts", nargs="+", help="JSON script files")
    p_batch.add_argument(
        "--driver",
        choices=("fake", "chromium"),
        default="fake",
    )
    p_batch.add_argument(
        "--trail-dir",
        help="write one trail JSONL per script into this directory",
    )
    p_batch.add_argument(
        "--approve-all",
        action="store_true",
        help="approve every 'ask' verdict in every script (the whole "
        "runbook is pre-authorized)",
    )
    p_batch.add_argument(
        "--require-verify",
        action="store_true",
        help="apply the require-verify gate to every script",
    )
    p_batch.add_argument(
        "--policy-pack",
        help="named policy pack applied to every script",
    )

    p_check = sub.add_parser(
        "check",
        help="preflight a run script against the Governor (v1.5): "
        "classify every step, flag asks/denies, and confirm secret "
        "placeholders resolve — without executing anything",
    )
    p_check.add_argument("script", help="JSON steps file (same format as run --script)")
    p_check.add_argument("--policy", help="JSON policy file")
    p_check.add_argument(
        "--policy-pack",
        help="named policy pack (same packs as run --policy-pack)",
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
        help="named policy pack (alternative to --policy): "
        "readonly | standard | strict | seatbelt",
    )
    p_run.add_argument("--approve-all", action="store_true",
                       help="approve every consequential action without asking")
    p_run.add_argument("--trail", default="ghost_hands_trail.jsonl",
                       help="trail output path (default: ghost_hands_trail.jsonl)")
    p_run.add_argument("--goal", default="", help="goal text recorded in the report")
    p_run.add_argument(
        "--cache",
        help="governed plan cache file (v1.0): a run that ends 'done' "
        "stores its proven steps here; a later run with the same --goal "
        "and start page replays them governed, with no model/script "
        "decisions for cached steps, falling back if the page changed",
    )
    p_run.add_argument(
        "--require-verify",
        action="store_true",
        help="reject a script's 'done' until a verify step has passed "
        "in the same run (v1.2): success must be proven, not claimed",
    )
    p_run.add_argument(
        "--resume",
        metavar="TRAIL",
        help="resume from a dead run's trail (v1.5): start where it "
        "stopped and seed the decider with what already happened",
    )
    p_run.add_argument(
        "--max-llm-tokens",
        type=int,
        default=None,
        help="stop the run (reason 'budget') once the decider's "
        "API-reported token spend reaches this cap (v1.3); checked "
        "between steps, never mid-action",
    )
    p_run.add_argument(
        "--recovery-attempts",
        type=int,
        default=1,
        help="governed second chances when a run gets stuck (v1.3; "
        "0 disables recovery and restores the pre-1.3 stuck stop)",
    )

    p_verify = sub.add_parser(
        "verify-trail",
        help="verify a trail's hash chain (v1.0): exit 0 intact, "
        "1 tampered/gapped, 2 legacy or unreadable",
    )
    p_verify.add_argument("trail_file", help="the trail JSONL to verify")

    p_costs = sub.add_parser(
        "costs",
        help="aggregate real LLM token spend across trail files "
        "(v1.4): API-reported usage only, unreported runs counted "
        "as unreported, never estimated",
    )
    p_costs.add_argument("trail_files", nargs="+", help="trail JSONL files")
    p_costs.add_argument(
        "--json", action="store_true", help="emit the rows and totals as JSON"
    )

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

    p_report = sub.add_parser(
        "report",
        help="render a trail JSONL as a self-contained HTML audit "
        "report (default output: the trail path with an .html suffix)",
    )
    p_report.add_argument("trail_file", help="the trail JSONL to render")
    p_report.add_argument(
        "-o", "--output", help="output .html path (default: trail path + .html)"
    )

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

    p_eval = sub.add_parser(
        "eval",
        help="run the graded model-evaluation suite (v0.7): 8 tasks "
        "against local fixtures, scored on success, steps, wall time, "
        "perception cost, approvals and heals",
    )
    p_eval.add_argument(
        "--decider",
        choices=("stub", "rules", "openai", "all"),
        default="stub",
        help="stub = local deterministic stub model server (harness "
        "verification only); rules = RuleDecider baseline; openai = a "
        "real model from the environment; all = every available one",
    )
    p_eval.add_argument(
        "--tasks",
        help="comma-separated task names to run (default: all 8)",
    )
    p_eval.add_argument(
        "--budget", type=int, default=None,
        help="per-task step budget override",
    )
    p_eval.add_argument(
        "--report",
        help="also write a JSON report to this path",
    )
    p_eval.add_argument(
        "--scorecard",
        help="also write an honest markdown scorecard to this path "
        "(v1.3): each decider reported separately, stub labeled as "
        "harness verification, never blended into one number",
    )

    p_doctor = sub.add_parser(
        "doctor",
        help="diagnose the whole stack in one pass (v0.9): runtime, "
        "Chromium, policy packs, model env, GhostBus and the phone "
        "bridge when configured, adb — PASS/WARN/FAIL rows with "
        "plain-language fixes; exit 1 iff anything FAILs",
    )
    p_doctor.add_argument(
        "--json",
        action="store_true",
        help="emit the checks as structured JSON instead of the table",
    )
    p_doctor.add_argument(
        "--bus",
        help="GhostBus base URL to check (env GHOST_HANDS_BUS_URL / "
        "GHOSTBUS_URL; unconfigured = a WARN row, bus features idle)",
    )
    p_doctor.add_argument(
        "--android-bridge",
        help="MrGhosty bridge URL to check (env "
        "GHOST_HANDS_ANDROID_BRIDGE; default http://127.0.0.1:8378)",
    )
    p_doctor.add_argument(
        "--android-token",
        help="Ghost Hands pairing token — prefer the "
        "GHOST_HANDS_ANDROID_TOKEN env var; never printed",
    )
    p_doctor.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="seconds each network probe may take (default 3)",
    )

    p_proof = sub.add_parser(
        "phone-proof",
        help="the guided on-device proof (v0.9): checks the bridge "
        "configuration, forwards the port over adb when a device is "
        "attached, verifies the bridge status, and sends the harmless "
        "test approval to the phone. Exit codes: 0 proven, 1 error, "
        "2 needs-setup, 3 not approved",
    )
    p_proof.add_argument(
        "--android-bridge",
        help="bridge URL (env GHOST_HANDS_ANDROID_BRIDGE; "
        "default http://127.0.0.1:8378)",
    )
    p_proof.add_argument(
        "--android-token",
        help="pairing token — prefer the GHOST_HANDS_ANDROID_TOKEN "
        "env var; never printed, never stored",
    )
    p_proof.add_argument("--timeout", type=float, default=600.0,
                          help="seconds to wait for the phone's decision (default 600)")
    p_proof.add_argument("--poll-interval", type=float, default=1.0,
                         help="seconds between approval status polls (default 1)")
    p_proof.add_argument("--yes", action="store_true",
                          help="run straight through without pausing between steps")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {
        "demo": cmd_demo,
        "bench": cmd_bench,
        "run": cmd_run,
        "check": cmd_check,
        "batch": cmd_batch,
        "export": cmd_export,
        "verify-trail": cmd_verify_trail,
        "costs": cmd_costs,
        "mcp": cmd_mcp,
        "report": cmd_report,
        "bus-agent": cmd_bus_agent,
        "bus-demo": cmd_bus_demo,
        "android-status": cmd_android_status,
        "phone-approval-test": cmd_phone_approval_test,
        "eval": cmd_eval,
        "doctor": cmd_doctor,
        "phone-proof": cmd_phone_proof,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
