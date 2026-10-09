"""Trail -> HTML audit report (v0.8): the receipts, made readable.

The JSONL trail is the record, but JSONL is for machines. ``ghost-hands
report trail.jsonl`` renders one run into a single self-contained HTML
file — inline CSS, no external assets, no JavaScript (collapsibles are
plain ``<details>``) — that a human can open offline and audit: what
the hands perceived, what they decided, what the Governor said, who
approved what, and what actually happened.

Rendering safety:

- Every string that came from a trail is HTML-escaped. A page title or
  element name is attacker-shaped data; it must never become markup
  in the report.
- The report does not expand what the trail exposed. Typed values are
  truncated at 80 characters, and when the acted-on element's
  descriptor (recorded on the execute event) says ``type=password``,
  a typed value renders as ``•••• (N chars)`` — the same redaction
  question the trail itself answers, applied at render time.
- Corrupt lines in the input are skipped and counted in the footer,
  never fatal: a partial trail is still a record.
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .trail import EVENT_TYPES, accounting

_VALUE_CAP = 80
_BULLETS = "••••"
_KNOWN_TYPES = frozenset(EVENT_TYPES)


def _is_net_event(ev: dict) -> bool:
    """Network-capture events: typed ``net`` — except that entries sunk
    from the Chromium driver carry the CDP resource type in their
    ``type`` field (``Trail.record`` lets fields win), so a real trail
    line for a request reads ``"type": "Document"`` with method/url/
    status alongside. Recognize both shapes."""
    if ev.get("type") == "net":
        return True
    return (
        ev.get("type") not in _KNOWN_TYPES
        and "url" in ev
        and ("method" in ev or "status" in ev or bool(ev.get("truncated")))
    )


def _net_resource_type(ev: dict) -> str:
    if ev.get("resource_type"):
        return str(ev["resource_type"])
    kind = str(ev.get("type") or "")
    return "" if kind == "net" else kind


def esc(value: Any) -> str:
    """Escape any trail value for HTML text or attribute context."""
    return html.escape("" if value is None else str(value), quote=True)


def _fmt_ts(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _fmt_clock(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _fmt_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes)}m {secs:.0f}s"


def parse_trail_lines(lines) -> tuple[list[dict[str, Any]], int]:
    """Parse JSONL text lines into events, tolerating corruption:
    bad lines are skipped and counted, never fatal."""
    import json

    events: list[dict[str, Any]] = []
    skipped = 0
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if isinstance(event, dict) and event.get("type"):
            events.append(event)
        else:
            skipped += 1
    return events, skipped


def read_trail_tolerant(path: str | Path) -> tuple[list[dict[str, Any]], int]:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    return parse_trail_lines(text.splitlines())


# ---------------------------------------------------------------------------
# Action rendering (plain language, with the trail's own redaction rules)
# ---------------------------------------------------------------------------


def _element_label(element: Optional[dict]) -> str:
    if not element:
        return ""
    name = element.get("name") or ""
    tag = element.get("tag") or ""
    if name:
        return f' "{name}"'
    if tag:
        return f" <{tag}>"
    return ""


def _is_password_target(element: Optional[dict]) -> bool:
    return bool(element) and str(element.get("type") or "").lower() == "password"


def _render_value(value: Any, masked: bool) -> str:
    text = "" if value is None else str(value)
    if masked:
        return f"{_BULLETS} ({len(text)} chars)"
    if len(text) > _VALUE_CAP:
        return text[:_VALUE_CAP] + "…"
    return text


def describe_action(action: dict, element: Optional[dict] = None) -> str:
    """One plain-language line for an action dict, e.g.
    ``click [3] "Place order"`` or ``type "…" into [2] "Password"``.
    ``element`` is the execute event's descriptor, when recorded."""
    if not isinstance(action, dict):
        return "(unknown action)"
    kind = action.get("kind", "?")
    target = action.get("target")
    at = f"[{target}]" if target is not None else ""
    label = _element_label(element)
    masked = _is_password_target(element)

    if kind == "navigate":
        return f"navigate to {action.get('url', '')}".rstrip()
    if kind in ("click", "double_click", "right_click", "hover"):
        verb = {
            "click": "click",
            "double_click": "double-click",
            "right_click": "right-click",
            "hover": "hover over",
        }[kind]
        return f"{verb} {at}{label}".strip()
    if kind == "type":
        rendered = _render_value(action.get("text"), masked)
        return f'type "{rendered}" into {at}{label}'.strip()
    if kind == "fill_form":
        fields = action.get("fields") or {}
        parts = []
        for key in sorted(fields):
            field_masked = masked or "password" in str(key).lower()
            parts.append(f"{key} = \"{_render_value(fields[key], field_masked)}\"")
        suffix = " and submit" if action.get("submit") else ""
        return f"fill form ({'; '.join(parts)}){suffix}"
    if kind == "press":
        return f"press {action.get('key', '')}".rstrip()
    if kind == "scroll":
        return f"scroll {action.get('direction', 'down')} {action.get('amount', '')}".strip()
    if kind == "extract":
        mode = action.get("mode") or "text"
        return f"extract ({mode}) {at}{label}".strip()
    if kind == "select":
        return f"select \"{_render_value(action.get('value'), masked)}\" on {at}{label}".strip()
    if kind == "set_file":
        return f"upload file {action.get('path', '')} to {at}{label}".strip()
    if kind == "download":
        dest = action.get("url") or ""
        return f"download {at}{label} {dest}".strip()
    if kind == "drag":
        return f"drag {at} to [{action.get('to_target')}]".strip()
    if kind == "click_at":
        return f"click at coordinates ({action.get('x')}, {action.get('y')}) [raw]"
    if kind == "screenshot":
        return f"screenshot {action.get('path', '')}".strip()
    if kind == "pdf":
        return f"print PDF {action.get('path', '')}".strip()
    if kind == "set_viewport":
        return f"set viewport {action.get('value') or ''} {action.get('width', '')}x{action.get('height', '')}".strip()
    if kind == "wait":
        if action.get("text"):
            return f"wait for text \"{action.get('text')}\""
        return f"wait {action.get('seconds', '')}s".strip()
    if kind == "done":
        return f"done — {action.get('summary', '')}".rstrip()
    return f"{kind} {at}{label}".strip()


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Header + stat-card numbers for one trail."""
    acct = accounting(events)
    approvals_requested = 0
    approvals_granted = 0
    approvals_denied = 0
    channels: set[str] = set()
    heals = downloads = dialogs = net_events = 0
    stop_reason = ""
    stop_summary = ""
    goal = ""
    first_url = ""
    timestamps = []
    for ev in events:
        ts = ev.get("ts")
        if isinstance(ts, (int, float)):
            timestamps.append(float(ts))
        if ev.get("goal") and not goal:
            goal = str(ev["goal"])
        if _is_net_event(ev):
            net_events += 1
        kind = ev.get("type")
        if kind == "perceive" and not first_url:
            first_url = str(ev.get("url") or "")
        elif kind == "approval":
            if ev.get("channel"):
                channels.add(str(ev["channel"]))
            if ev.get("phase") == "request":
                approvals_requested += 1
            elif ev.get("phase") == "decision":
                if ev.get("approved"):
                    approvals_granted += 1
                else:
                    approvals_denied += 1
        elif kind == "heal":
            heals += 1
        elif kind == "download":
            downloads += 1
        elif kind == "dialog":
            dialogs += 1
        elif kind == "stop":
            stop_reason = str(ev.get("reason") or "")
            stop_summary = str(ev.get("summary") or "")
    duration = (max(timestamps) - min(timestamps)) if len(timestamps) >= 2 else 0.0
    body = "web (Chromium / fake web)"
    urls = [str(ev.get("url") or "") for ev in events if ev.get("type") == "perceive"]
    if any(u.startswith("android://") for u in urls):
        body = "Android (MrGhosty bridge)"
    elif any(u.startswith("phone://") for u in urls):
        body = "SimPhone (simulated phone)"
    return {
        "goal": goal,
        "body": body,
        "first_url": first_url,
        "started": _fmt_ts(min(timestamps)) if timestamps else "",
        "ended": _fmt_ts(max(timestamps)) if timestamps else "",
        "duration_s": duration,
        "duration": _fmt_duration(duration),
        "stop_reason": stop_reason,
        "stop_summary": stop_summary,
        "steps": acct["steps"],
        "map_chars": acct["map_chars"],
        "est_tokens": acct["est_tokens"],
        "actions_by_class": acct["actions_by_class"],
        "approvals_requested": approvals_requested,
        "approvals_granted": approvals_granted,
        "approvals_denied": approvals_denied,
        "approval_channels": sorted(channels),
        "heals": heals,
        "downloads": downloads,
        "dialogs": dialogs,
        "net_events": net_events,
        "event_count": len(events),
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

_CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin: 0; background: #141416; color: #e8e8ec;
  font: 15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.wrap { max-width: 880px; margin: 0 auto; padding: 32px 20px 64px; }
header { border-bottom: 3px solid #6716f3; padding-bottom: 18px; margin-bottom: 24px; }
h1 { margin: 0 0 4px; font-size: 28px; letter-spacing: .2px; }
h1 .ghost { color: #a678ff; }
.sub { color: #a9a9b3; margin: 2px 0; }
.sub b { color: #e8e8ec; }
.stop { display: inline-block; margin-top: 10px; padding: 3px 12px; border-radius: 999px;
  background: #26232e; border: 1px solid #6716f3; color: #d9c8ff; font-size: 13px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px; margin: 0 0 28px; }
.card { background: #1d1d21; border: 1px solid #2e2e35; border-radius: 10px; padding: 12px 14px; }
.card .n { font-size: 22px; font-weight: 700; color: #fff; }
.card .k { font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: #a9a9b3; }
.step { background: #1a1a1e; border: 1px solid #2a2a31; border-left: 4px solid #6716f3;
  border-radius: 8px; padding: 14px 18px; margin: 0 0 14px; }
.step h3 { margin: 0 0 8px; font-size: 15px; color: #cdb9ff; }
.step h3 .clock { color: #8b8b96; font-weight: 400; font-size: 13px; }
.row { margin: 4px 0; }
.row .tag { display: inline-block; min-width: 86px; color: #8b8b96; font-size: 12px;
  text-transform: uppercase; letter-spacing: .06em; }
.chip { display: inline-block; padding: 1px 9px; border-radius: 999px; font-size: 12px;
  font-weight: 600; margin-right: 6px; }
.chip.readonly { background: #23301f; color: #a5d68f; border: 1px solid #3d5a2e; }
.chip.write { background: #33291a; color: #f0c674; border: 1px solid #6b5222; }
.chip.consequential { background: #3a1f33; color: #ff9de2; border: 1px solid #7a2d63; }
.chip.unknown { background: #26262c; color: #a9a9b3; border: 1px solid #3a3a44; }
.verdict { font-weight: 600; }
.verdict.allow { color: #a5d68f; }
.verdict.deny { color: #ff8f8f; }
.verdict.ask { color: #f0c674; }
.reason { color: #a9a9b3; font-size: 13px; }
.result-ok { color: #cfcfda; }
.result-err { color: #ff8f8f; }
.special { margin: 6px 0 6px 86px; padding: 8px 12px; border-radius: 6px;
  background: #211d2b; border: 1px solid #38295e; font-size: 13.5px; }
.special .tag { min-width: 0; margin-right: 8px; color: #cdb9ff; }
details { margin: 6px 0 6px 86px; }
details summary { cursor: pointer; color: #a678ff; font-size: 13px; }
details pre { background: #101013; border: 1px solid #2a2a31; border-radius: 6px;
  padding: 10px 12px; overflow-x: auto; font-size: 12.5px; color: #cfcfda;
  white-space: pre-wrap; word-break: break-word; }
footer { margin-top: 36px; padding-top: 14px; border-top: 1px solid #2a2a31;
  color: #8b8b96; font-size: 13px; }
.mono { font-family: ui-monospace, "SF Mono", Consolas, monospace; }
"""


def _chip(classification: str) -> str:
    cls = classification or "unknown"
    return f'<span class="chip {esc(cls)}">{esc(cls)}</span>'


def _govern_line(ev: dict, approval_decisions: dict[int, list[dict]]) -> str:
    classification = str(ev.get("classification") or "")
    outcome = str(ev.get("outcome") or "")
    reason = str(ev.get("reason") or "")
    step = ev.get("step")
    if outcome == "allow":
        verdict = '<span class="verdict allow">allowed</span>'
    elif outcome == "deny":
        verdict = '<span class="verdict deny">denied</span>'
    elif outcome == "ask":
        decisions = approval_decisions.get(step, [])
        if decisions:
            last = decisions[-1]
            channel = str(last.get("channel") or "an approver")
            if last.get("approved"):
                verdict = (
                    '<span class="verdict ask">ask → approved</span> '
                    f'<span class="reason">via {esc(channel)}</span>'
                )
            else:
                verdict = (
                    '<span class="verdict deny">ask → denied</span> '
                    f'<span class="reason">via {esc(channel)}</span>'
                )
        else:
            verdict = '<span class="verdict ask">ask → decision below</span>'
    else:
        verdict = f'<span class="verdict">{esc(outcome)}</span>'
    reason_html = f' <span class="reason">{esc(reason)}</span>' if reason else ""
    return (
        f'<div class="row"><span class="tag">Governor</span>'
        f"{_chip(classification)}{verdict}{reason_html}</div>"
    )


def _special_lines(step_events: list[dict]) -> list[str]:
    lines: list[str] = []
    net_batch: list[dict] = []
    for ev in step_events:
        kind = ev.get("type")
        if _is_net_event(ev):
            net_batch.append(ev)
            continue
        if kind == "heal":
            found = ev.get("found")
            lines.append(
                '<div class="special"><span class="tag">↻ heal</span>'
                f"target [{esc(ev.get('from_target'))}] moved; re-found "
                f"as [{esc(ev.get('to_target'))}] by descriptor "
                f"({'found' if found else 'NOT found'}) — "
                f"trigger: {esc(ev.get('trigger'))}</div>"
            )
        elif kind == "dialog":
            lines.append(
                '<div class="special"><span class="tag">dialog</span>'
                f"{esc(ev.get('dialog_type'))} dialog "
                f"“{esc(ev.get('message'))}” → "
                f"<b>{esc(ev.get('decision'))}</b> "
                f"(policy: {esc(ev.get('policy'))})</div>"
            )
        elif kind == "download":
            lines.append(
                '<div class="special"><span class="tag">⬇ download</span>'
                f"{esc(ev.get('filename'))} — {esc(ev.get('bytes'))} bytes "
                f"({esc(ev.get('state'))})</div>"
            )
        elif kind == "approval":
            phase = ev.get("phase")
            channel = esc(ev.get("channel") or "?")
            if phase == "request":
                summary = esc(ev.get("summary") or ev.get("kind") or "")
                lines.append(
                    '<div class="special"><span class="tag">✋ approval asked</span>'
                    f"via {channel}: {summary}</div>"
                )
            elif phase == "decision":
                approved = bool(ev.get("approved"))
                word = "APPROVED" if approved else "DENIED"
                by = ev.get("decided_by") or ev.get("by") or channel
                reason = esc(ev.get("reason") or "")
                lines.append(
                    '<div class="special"><span class="tag">✋ approval</span>'
                    f"<b>{word}</b> via {channel} "
                    f"(decided by {esc(by)})"
                    f"{' — ' + reason if reason else ''}</div>"
                )
    if net_batch:
        rows = []
        for ev in net_batch:
            if ev.get("truncated"):
                rows.append(f"— capture capped: {ev.get('note', '')}")
            else:
                rows.append(
                    f"{ev.get('method', '')} {ev.get('url', '')} "
                    f"→ {ev.get('status', '')} ({_net_resource_type(ev)})"
                )
        lines.append(
            "<details><summary>network — "
            f"{len(net_batch)} event(s)</summary><pre>"
            + esc("\n".join(rows))
            + "</pre></details>"
        )
    return lines


def render_report(events: list[dict[str, Any]], skipped: int = 0) -> str:
    """Render a parsed trail (list of event dicts) as one HTML page."""
    info = summarize(events)

    # Group by step, preserving first-seen order.
    steps: dict[Any, list[dict]] = {}
    order: list[Any] = []
    for ev in events:
        step = ev.get("step", 0)
        if step not in steps:
            steps[step] = []
            order.append(step)
        steps[step].append(ev)

    approval_decisions: dict[Any, list[dict]] = {}
    for ev in events:
        if ev.get("type") == "approval" and ev.get("phase") == "decision":
            approval_decisions.setdefault(ev.get("step", 0), []).append(ev)

    out: list[str] = []
    out.append("<!DOCTYPE html>")
    out.append('<html lang="en"><head><meta charset="utf-8">')
    out.append(
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
    )
    out.append("<title>Ghost Hands — trail audit report</title>")
    out.append(f"<style>{_CSS}</style></head><body><div class=\"wrap\">")

    # -- header ------------------------------------------------------------
    out.append("<header>")
    out.append('<h1><span class="ghost">👻</span> Ghost Hands — trail audit report</h1>')
    if info["goal"]:
        out.append(f'<p class="sub">Goal: <b>{esc(info["goal"])}</b></p>')
    out.append(f'<p class="sub">Body: <b>{esc(info["body"])}</b></p>')
    if info["first_url"]:
        out.append(f'<p class="sub">Started at: <b class="mono">{esc(info["first_url"])}</b></p>')
    if info["started"]:
        out.append(
            f'<p class="sub">{esc(info["started"])} → {esc(info["ended"])} '
            f'({esc(info["duration"])})</p>'
        )
    if info["stop_reason"]:
        out.append(
            f'<span class="stop">stopped: {esc(info["stop_reason"])}'
            f' — {esc(info["stop_summary"])}</span>'
        )
    else:
        out.append('<p class="sub">No stop event recorded in this trail.</p>')
    out.append("</header>")

    # -- stat cards ----------------------------------------------------------
    by_class = info["actions_by_class"]
    class_bits = ", ".join(f"{k}: {v}" for k, v in sorted(by_class.items())) or "—"
    approvals_n = info["approvals_requested"]
    approvals_val = (
        f"{info['approvals_granted']}✓ / {info['approvals_denied']}✗"
        if approvals_n
        else "0"
    )
    cards = [
        (str(info["steps"]), "steps executed"),
        (info["duration"], "wall duration"),
        (f"{info['map_chars']:,}", "perception chars"),
        (f"~{info['est_tokens']:,}", "est. tokens (chars/4)"),
        (class_bits, "actions by class"),
        (approvals_val, f"approvals asked: {approvals_n}"),
        (str(info["heals"]), "self-heals"),
        (
            f"{info['downloads']} / {info['dialogs']} / {info['net_events']}",
            "downloads / dialogs / net",
        ),
    ]
    out.append('<div class="cards">')
    for number, label in cards:
        out.append(
            f'<div class="card"><div class="n">{esc(number)}</div>'
            f'<div class="k">{esc(label)}</div></div>'
        )
    out.append("</div>")

    # -- timeline ------------------------------------------------------------
    for step in order:
        step_events = steps[step]
        perceive = next((e for e in step_events if e.get("type") == "perceive"), None)
        decide = next((e for e in step_events if e.get("type") == "decide"), None)
        governs = [e for e in step_events if e.get("type") == "govern"]
        executes = [e for e in step_events if e.get("type") == "execute"]
        results = [e for e in step_events if e.get("type") == "result"]
        stop = next((e for e in step_events if e.get("type") == "stop"), None)
        first_ts = next(
            (e.get("ts") for e in step_events if e.get("ts") is not None), None
        )
        clock = _fmt_clock(first_ts)
        out.append('<section class="step">')
        out.append(
            f"<h3>Step {esc(step)}"
            + (f' <span class="clock">{esc(clock)}</span>' if clock else "")
            + "</h3>"
        )
        if perceive is not None:
            detail = (
                f"url: {perceive.get('url', '')}\n"
                f"title: {perceive.get('title', '')}\n"
                f"elements: {perceive.get('elements', '')}\n"
                f"map_chars: {perceive.get('map_chars', '')}\n"
                f"signature: {perceive.get('signature', '')}"
            )
            out.append(
                '<div class="row"><span class="tag">Perceived</span>'
                f"{esc(perceive.get('url', ''))} — "
                f"{esc(perceive.get('elements', ''))} elements, "
                f"{esc(perceive.get('map_chars', ''))} map chars</div>"
                "<details><summary>perception record</summary><pre>"
                + esc(detail)
                + "</pre></details>"
            )
        element_desc = None
        for ex in executes:
            if ex.get("element"):
                element_desc = ex["element"]
                break
        if decide is not None:
            action = decide.get("action") or {}
            out.append(
                '<div class="row"><span class="tag">Decision</span>'
                f"{esc(describe_action(action, element_desc))}</div>"
            )
        for gov in governs:
            out.append(_govern_line(gov, approval_decisions))
        for ex in executes:
            raw = ' <span class="reason">[raw coordinates]</span>' if ex.get("raw") else ""
            healed = ' <span class="reason">[after heal]</span>' if ex.get("healed") else ""
            out.append(
                '<div class="row"><span class="tag">Executed</span>'
                f"{esc(describe_action(ex.get('action') or {}, ex.get('element')))}"
                f"{raw}{healed}</div>"
            )
        for res in results:
            ok = res.get("ok")
            cls = "result-ok" if ok else "result-err"
            mark = "✓" if ok else "✗"
            out.append(
                '<div class="row"><span class="tag">Result</span>'
                f'<span class="{cls}">{mark} {esc(res.get("result", ""))}</span></div>'
            )
        out.extend(_special_lines(step_events))
        if stop is not None:
            out.append(
                '<div class="row"><span class="tag">Stop</span>'
                f"<b>{esc(stop.get('reason', ''))}</b> — "
                f"{esc(stop.get('summary', ''))}</div>"
            )
        out.append("</section>")

    # -- footer ----------------------------------------------------------------
    skipped_note = (
        f" {skipped} unreadable line(s) were skipped while parsing."
        if skipped
        else ""
    )
    out.append(
        "<footer>Generated by Ghost Hands report — the trail is the "
        f"record. {info['event_count']} events rendered.{skipped_note}</footer>"
    )
    out.append("</div></body></html>")
    return "\n".join(out)


def write_report(
    trail_path: str | Path, out_path: Optional[str | Path] = None
) -> Path:
    """Read a trail JSONL (tolerantly) and write its HTML report.
    Default output: the input path with an ``.html`` suffix."""
    events, skipped = read_trail_tolerant(trail_path)
    target = (
        Path(out_path)
        if out_path
        else Path(trail_path).with_suffix(".html")
    )
    target.write_text(render_report(events, skipped=skipped), encoding="utf-8")
    return target
