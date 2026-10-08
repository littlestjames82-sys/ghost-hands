"""Replay export: graduate a trail into a standalone Ghost Hands script.

The exported file is a plain Python program that imports ghost_hands and
replays the trail's executed steps through Runner + ScriptedDecider —
deterministic, model-free, and still governed by the same Policy
defaults. It is *our* stack end to end: no third-party automation library
is involved at any point.

Two bodies can be exported (v0.2):

- ``driver="chromium"`` (default; the v0.1 behavior) — replay against a
  real Chromium via ChromiumDriver. A ``navigate`` step for ``start_url``
  is prepended when the recorded run began by opening a page rather than
  navigating (start_url defaults to the trail's first perceived URL).
- ``driver="fake"`` — replay against FakeDriver with the mini-web
  embedded in the script (``pages=`` required). This makes a trail
  recorded on the fake web executable anywhere, browser-free.
"""

from __future__ import annotations

import json
from typing import Any, Optional


def _descriptor_comment(element: dict | None) -> str:
    if not element:
        return ""
    bits = [f"[{element.get('number')}] <{element.get('tag')}> \"{element.get('name')}\""]
    if element.get("type"):
        bits.append(f"type={element['type']}")
    if element.get("href"):
        bits.append(f"href={element['href']}")
    if element.get("id"):
        bits.append(f"id={element['id']}")
    return "  # " + " ".join(bits)


def _first_perceived_url(events: list[dict[str, Any]]) -> Optional[str]:
    for ev in events:
        if ev.get("type") == "perceive" and ev.get("url"):
            return str(ev["url"])
    return None


def export_ghost_hands_script(
    events: list[dict[str, Any]],
    driver: str = "chromium",
    start_url: Optional[str] = None,
    pages: Optional[dict] = None,
) -> str:
    """Build a runnable Ghost Hands replay script from trail events.

    Only ``execute`` events are exported — denied or unapproved actions
    never ran, so they are not part of the graduated script.
    """
    if driver not in ("chromium", "fake"):
        raise ValueError(f"unknown export driver: {driver!r}")
    if driver == "fake" and pages is None:
        raise ValueError("driver='fake' exports need pages= (the mini-web to embed)")

    executed = [ev for ev in events if ev.get("type") == "execute"]
    actions = [ev.get("action", {}) for ev in executed]
    if start_url is None:
        start_url = _first_perceived_url(events)
    if start_url and not (
        actions and actions[0].get("kind") == "navigate" and actions[0].get("url") == start_url
    ):
        actions = [{"kind": "navigate", "url": start_url}] + actions
        executed = [{"action": actions[0], "element": None}] + executed

    step_lines: list[str] = []
    for ev, action in zip(executed, actions):
        line = f"    {json.dumps(action)},"
        line += _descriptor_comment(ev.get("element"))
        step_lines.append(line)
    steps_block = "\n".join(step_lines) if step_lines else "    # (no executed steps)"

    header = (
        "#!/usr/bin/env python3\n"
        '"""Graduated from a Ghost Hands trail.\n\n'
        "This script replays a recorded agent run deterministically: no model,\n"
        "no decision pass — just the steps that were actually executed, run\n"
        "through the same governed Runner (Governor + Trail) that produced\n"
        "them. Targets are element numbers from the recorded maps; the\n"
        "descriptor comments show what each number pointed at, so a human\n"
        "can re-map a step if the page has changed since the recording.\n"
        '"""\n'
    )
    if driver == "fake":
        pages_block = json.dumps(pages, indent=4, sort_keys=True)
        body = f'''
import json

from ghost_hands import FakeDriver, Policy, Runner, ScriptedDecider

PAGES = json.loads({pages_block!r})

STEPS = [
{steps_block}
]


def main() -> None:
    driver = FakeDriver(PAGES, start_url={start_url!r})
    try:
        runner = Runner(driver, ScriptedDecider(STEPS), policy=Policy())
        report = runner.run("replay of a graduated Ghost Hands trail")
        print(report.stop_reason, "-", report.summary)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
'''
        return header + body

    body = f'''
from ghost_hands import ChromiumDriver, Policy, Runner, ScriptedDecider

STEPS = [
{steps_block}
]


def main() -> None:
    driver = ChromiumDriver()
    try:
        runner = Runner(driver, ScriptedDecider(STEPS), policy=Policy())
        report = runner.run("replay of a graduated Ghost Hands trail")
        print(report.stop_reason, "-", report.summary)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
'''
    return header + body
