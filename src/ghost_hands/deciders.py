"""Deciders (brains): given a goal and the current ElementMap, pick ONE
action. The Runner owns the loop; a decider never touches the page itself.

Three brains ship in v0.1:
- ``ScriptedDecider`` — an explicit list of steps (replay, tests, CLI run).
- ``RuleDecider`` — deterministic, offline pattern matching. No model, no
  network, fully reproducible.
- ``OpenAICompatibleDecider`` — one decision pass per step against any
  OpenAI-compatible chat endpoint. Key and base URL come from the
  environment only and are never stored.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Optional, Protocol

from . import actions as A
from .actions import Action
from .errors import HandsError
from .eyes import ElementMap


class Decider(Protocol):
    def decide(
        self, goal: str, element_map: ElementMap, history: list[dict]
    ) -> Action: ...


class ScriptedDecider:
    """Plays back a fixed list of action dicts, then reports done."""

    def __init__(self, steps: list[dict]) -> None:
        self._steps = [Action.from_dict(s) for s in steps]
        self._i = 0

    def decide(self, goal: str, element_map: ElementMap, history: list[dict]) -> Action:
        if self._i >= len(self._steps):
            return A.done("script complete")
        action = self._steps[self._i]
        self._i += 1
        return action


class RuleDecider:
    """Deterministic offline rules over element names.

    Understood goal shapes:
    - "go to <url>"
    - "click <text>"
    - "type <text> into <field name>"
    - "search for <query>"  (find the search box, type, submit)
    Anything else falls back to the clickable element with the best keyword
    overlap against the goal; when nothing matches, it reports done honestly
    instead of flailing.
    """

    def __init__(self) -> None:
        self._intent_cache: dict[str, list[tuple]] = {}

    # -- intent parsing ------------------------------------------------------
    def _intents(self, goal: str) -> list[tuple]:
        if goal in self._intent_cache:
            return self._intent_cache[goal]
        g = goal.strip()
        low = g.lower()
        intents: list[tuple] = []
        m = re.search(r"go to\s+(https?://\S+)", low)
        if m:
            intents.append(("navigate", m.group(1)))
        m = re.search(r"search for\s+(.+)", g, flags=re.IGNORECASE)
        if m and "click" not in low:
            intents.append(("search", m.group(1).strip()))
        m = re.search(r"type\s+(.+?)\s+into\s+(.+)", g, flags=re.IGNORECASE)
        if m:
            intents.append(("type", m.group(1).strip(), m.group(2).strip()))
        m = re.search(r"click\s+(.+)", g, flags=re.IGNORECASE)
        if m and not intents:
            intents.append(("click", m.group(1).strip()))
        self._intent_cache[goal] = intents
        return intents

    @staticmethod
    def _already(history: list[dict], kind: str, **match) -> bool:
        for h in history:
            if h.get("kind") != kind:
                continue
            if all(h.get(k) == v for k, v in match.items()):
                return True
        return False

    def decide(self, goal: str, element_map: ElementMap, history: list[dict]) -> Action:
        intents = self._intents(goal)
        for intent in intents:
            action = self._fulfil(intent, element_map, history)
            if action is not None:
                return action
        if intents:
            return A.done("all goal steps satisfied")
        return self._fallback(goal, element_map, history)

    def _fulfil(self, intent: tuple, element_map: ElementMap, history: list[dict]) -> Optional[Action]:
        kind = intent[0]
        if kind == "navigate":
            if self._already(history, "navigate", url=intent[1]):
                return None
            return A.navigate(intent[1])
        if kind == "click":
            el = element_map.find_by_text(intent[1])
            if el is None:
                return A.done(f"no element matching {intent[1]!r}")
            if self._already(history, "click", target=el.number):
                return None
            return A.click(el.number)
        if kind == "type":
            text, field = intent[1], intent[2]
            el = element_map.find_by_text(field)
            if el is None:
                return A.done(f"no field matching {field!r}")
            if self._already(history, "type", target=el.number, text=text):
                return None
            return A.type(el.number, text)
        if kind == "search":
            query = intent[1]
            box = self._search_box(element_map)
            if box is None:
                return A.done("no search box on this page")
            if not self._already(history, "type", target=box.number, text=query):
                return A.type(box.number, query)
            submit = self._submit_control(element_map)
            if submit is not None and not self._already(history, "click", target=submit.number):
                return A.click(submit.number)
            if not self._already(history, "press", key="Enter"):
                return A.press("Enter")
            return None
        return None

    @staticmethod
    def _search_box(element_map: ElementMap) -> Optional[object]:
        for el in element_map.elements:
            if el.tag not in ("input", "textarea"):
                continue
            hints = " ".join(
                filter(None, [el.name, el.placeholder, el.attr_name, el.type or ""])
            ).lower()
            if "search" in hints or el.attr_name == "q" or el.type == "search":
                return el
        for el in element_map.elements:
            if el.tag == "input" and (el.type or "text") in ("text", "search"):
                return el
        return None

    @staticmethod
    def _submit_control(element_map: ElementMap) -> Optional[object]:
        for el in element_map.elements:
            if el.type == "submit":
                return el
            if el.tag == "button" and "search" in el.name.lower():
                return el
        return None

    def _fallback(self, goal: str, element_map: ElementMap, history: list[dict]) -> Action:
        words = {w for w in re.findall(r"[a-z0-9]+", goal.lower()) if len(w) > 2}
        best, best_score = None, 0
        for el in element_map.elements:
            if el.tag not in ("a", "button") and el.role not in ("link", "button"):
                continue
            name_words = set(re.findall(r"[a-z0-9]+", el.name.lower()))
            score = len(words & name_words)
            if score > best_score:
                best, best_score = el, score
        if best is not None and not self._already(history, "click", target=best.number):
            return A.click(best.number)
        return A.done("nothing on this page matches the goal")


_SYSTEM_PROMPT = (
    "You are the decider for Ghost Hands, a governed browser agent. "
    "You see a numbered map of interactive elements. Reply with exactly ONE "
    "JSON object for the next action, no prose. Shapes: "
    '{"kind":"navigate","url":"https://…"}, {"kind":"click","target":3}, '
    '{"kind":"hover","target":3}, {"kind":"double_click","target":3}, '
    '{"kind":"right_click","target":3}, {"kind":"drag","target":3,"to_target":5}, '
    '{"kind":"click_at","x":120,"y":340}, '
    '{"kind":"type","target":3,"text":"…"}, {"kind":"press","key":"Enter"}, '
    '{"kind":"press","key":"Control+a"}, '
    '{"kind":"select","target":3,"value":"…"}, '
    '{"kind":"fill_form","fields":{"Email":"a@b.c","Name":"…"},"submit":false}, '
    '{"kind":"set_file","target":3,"path":"/local/file"}, '
    '{"kind":"download","target":3}, '
    '{"kind":"scroll","direction":"down","amount":500}, '
    '{"kind":"extract","target":3,"mode":"text"}, '
    '{"kind":"extract","mode":"list"}, {"kind":"extract","mode":"table"}, '
    '{"kind":"extract","mode":"network"}, '
    '{"kind":"screenshot"}, {"kind":"pdf","path":"page.pdf"}, '
    '{"kind":"set_viewport","value":"mobile"}, '
    '{"kind":"wait","seconds":1}, {"kind":"wait","text":"Loaded","seconds":10}, '
    '{"kind":"done","summary":"…"}. Targets are element numbers from the map.'
)


class OpenAICompatibleDecider:
    """One LLM decision pass per step, against any OpenAI-compatible
    endpoint. Reads GHOST_HANDS_BASE_URL / GHOST_HANDS_API_KEY /
    GHOST_HANDS_MODEL from the environment; the key is never stored on
    disk or written to the trail."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = (base_url or os.environ.get("GHOST_HANDS_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.environ.get("GHOST_HANDS_API_KEY") or ""
        self.model = model or os.environ.get("GHOST_HANDS_MODEL") or "gpt-4o-mini"
        self.timeout = timeout
        if not self.base_url:
            raise HandsError(
                "GHOST_HANDS_BASE_URL is not set; the OpenAI-compatible decider "
                "needs an endpoint base URL in the environment"
            )
        if not self.api_key:
            raise HandsError(
                "GHOST_HANDS_API_KEY is not set; the OpenAI-compatible decider "
                "needs a key in the environment (it is never stored)"
            )

    def decide(self, goal: str, element_map: ElementMap, history: list[dict]) -> Action:
        recent = history[-5:]
        user = (
            f"Goal: {goal}\n\nPage: {element_map.url}\n"
            f"Elements:\n{element_map.render()}\n\n"
            f"Recent actions: {json.dumps(recent)}\n\nNext action JSON:"
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # network or HTTP failure: fail honestly
            raise HandsError(f"decider request failed: {exc}") from exc
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise HandsError(f"unexpected decider response shape: {exc}") from exc
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise HandsError(f"decider did not return a JSON action: {content[:120]!r}")
        try:
            return Action.from_dict(json.loads(match.group(0)))
        except (ValueError, TypeError) as exc:
            raise HandsError(f"decider returned an invalid action: {exc}") from exc
