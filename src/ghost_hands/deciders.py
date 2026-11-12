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
import time
import urllib.error
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
    '{"kind":"verify","text":"Order confirmed"}, '
    '{"kind":"verify","target":3}, {"kind":"verify","url":"/done"}, '
    '{"kind":"done","summary":"…"}. Targets are element numbers from the map. '
    "Before done, verify the result when you can: a verify action asserts "
    "text, an element, or the URL, and a failed verify stops the run honestly. "
    "For a credential, never invent or repeat its value: type the placeholder "
    '"{{secret:NAME}}" (e.g. {{secret:PASSWORD}}) and Ghost Hands substitutes '
    "the real value from the environment at execution time, outside the trail."
)


#: v0.9.1: cap on a decider endpoint's response body (see decide()).
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


_RETRYABLE_HTTP = (429, 500, 502, 503, 504)


def _retry_limit() -> int:
    raw = (os.environ.get("GHOST_HANDS_RETRIES") or "").strip()
    try:
        return max(0, min(5, int(raw))) if raw else 2
    except ValueError:
        return 2


def _retry_backoff() -> float:
    raw = (os.environ.get("GHOST_HANDS_RETRY_BACKOFF") or "").strip()
    try:
        return max(0.0, float(raw)) if raw else 0.5
    except ValueError:
        return 0.5


def _post_json(make_request, timeout: float, usage: dict) -> dict:
    """v1.8: POST JSON with bounded retry. Transient failures
    (429/5xx, network errors) are retried up to GHOST_HANDS_RETRIES
    times (default 2) with linear backoff; every retry is counted
    in usage["retries"] so the run's accounting shows what the
    network really cost. Non-retryable HTTP errors and oversized
    responses fail immediately, as before."""
    retries = _retry_limit()
    backoff = _retry_backoff()
    attempt = 0
    while True:
        req = make_request()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise HandsError(
                        "decider response too large "
                        f"(> {MAX_RESPONSE_BYTES} bytes) — refused"
                    )
                return json.loads(raw.decode("utf-8"))
        except HandsError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code in _RETRYABLE_HTTP and attempt < retries:
                attempt += 1
                usage["retries"] = int(usage.get("retries") or 0) + 1
                time.sleep(backoff * attempt)
                continue
            raise HandsError(f"decider request failed: {exc}") from exc
        except Exception as exc:  # network failure: fail honestly, after retries
            if attempt < retries:
                attempt += 1
                usage["retries"] = int(usage.get("retries") or 0) + 1
                time.sleep(backoff * attempt)
                continue
            raise HandsError(f"decider request failed: {exc}") from exc


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
        batch: Optional[bool] = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("GHOST_HANDS_BASE_URL") or "").rstrip("/")
        self.api_key = api_key or os.environ.get("GHOST_HANDS_API_KEY") or ""
        self.model = model or os.environ.get("GHOST_HANDS_MODEL") or "gpt-4o-mini"
        self.timeout = timeout
        # v1.2: batch mode lets one model call return several actions
        # (the Runner still governs, approves, and records each one
        # individually, re-perceiving between them). Opt-in: batching
        # trades per-step model judgment for fewer calls.
        if batch is None:
            batch = (os.environ.get("GHOST_HANDS_BATCH") or "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
        self.batch = batch
        # v1.8: JSON mode asks the endpoint for a strict JSON object
        # reply (response_format) — fewer parse failures on
        # providers that support it. Opt-in: some providers 400 on
        # the parameter, so it stays off unless asked for.
        self.json_mode = (
            os.environ.get("GHOST_HANDS_JSON_MODE") or ""
        ).strip().lower() in ("1", "true", "yes", "on")
        # v1.2: real token accounting, accumulated from API responses
        # that carry a usage object (ignored silently when absent).
        # v1.8: plus a retries counter (see _post_json).
        self.usage: dict = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "calls": 0,
            "retries": 0,
        }
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
        return self._request_actions(goal, element_map, history, want_batch=False)[0]

    def decide_batch(
        self, goal: str, element_map: ElementMap, history: list[dict]
    ) -> list[Action]:
        """v1.2: one model call, several actions — used by the Runner
        only when this decider was built with batch=True. Each returned
        action is still governed, approved, and recorded individually;
        the batch is a cost optimization, never a governance bypass."""
        return self._request_actions(goal, element_map, history, want_batch=True)

    def _request_actions(
        self, goal: str, element_map: ElementMap, history: list[dict], want_batch: bool
    ) -> list[Action]:
        recent = history[-5:]
        user = (
            f"Goal: {goal}\n\nPage: {element_map.url}\n"
            f"Elements:\n{element_map.render_focused(goal)}\n\n"
            f"Recent actions: {json.dumps(recent)}\n\nNext action JSON:"
        )
        if want_batch:
            user += (
                ' You may instead return {"actions": [ ... ]} with up to'
                " 5 actions when the next steps are obvious from this"
                " page (for example filling several fields of one form)."
                " Every action is still checked and approved on its own"
                " before it runs, and the page is re-read between them,"
                " so batch only steps you are confident about."
            )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
        }
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        data = json.dumps(body).encode("utf-8")

        def make_request():
            return urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST",
            )

        payload = _post_json(make_request, self.timeout, self.usage)
        usage = payload.get("usage") or {}
        self.usage["calls"] += 1
        for key in ("prompt_tokens", "completion_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and value >= 0:
                self.usage[key] += value
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise HandsError(f"unexpected decider response shape: {exc}") from exc
        return _parse_actions(content)


def _parse_actions(content: str) -> list[Action]:
    """Parse a model's reply into actions: one action object, or
    {"actions": [...]} (capped at 5) in batch mode. Shared by the
    OpenAI-compatible and Anthropic deciders so both speak exactly
    the same action dialect."""
    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if not match:
        raise HandsError(f"decider did not return a JSON action: {content[:120]!r}")
    try:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict) and isinstance(parsed.get("actions"), list):
            actions = [Action.from_dict(item) for item in parsed["actions"][:5]]
            if not actions:
                raise ValueError("empty actions list")
            return actions
        return [Action.from_dict(parsed)]
    except (ValueError, TypeError) as exc:
        raise HandsError(f"decider returned an invalid action: {exc}") from exc


def _batch_from_env() -> bool:
    return (os.environ.get("GHOST_HANDS_BATCH") or "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _user_prompt(goal: str, element_map: ElementMap, history: list[dict], want_batch: bool) -> str:
    recent = history[-5:]
    user = (
        f"Goal: {goal}\n\nPage: {element_map.url}\n"
        f"Elements:\n{element_map.render_focused(goal)}\n\n"
        f"Recent actions: {json.dumps(recent)}\n\nNext action JSON:"
    )
    if want_batch:
        user += (
            ' You may instead return {"actions": [ ... ]} with up to'
            " 5 actions when the next steps are obvious from this"
            " page (for example filling several fields of one form)."
            " Every action is still checked and approved on its own"
            " before it runs, and the page is re-read between them,"
            " so batch only steps you are confident about."
        )
    return user


class AnthropicDecider:
    """v1.6: one LLM decision pass per step against Anthropic's
    Messages API — the Claude family, which tops most browser-agent
    evaluations, as a first-class decider rather than through a
    compatibility shim. Same action dialect, same batch mode, same
    usage accounting as the OpenAI-compatible decider. Reads
    GHOST_HANDS_ANTHROPIC_BASE_URL / GHOST_HANDS_ANTHROPIC_API_KEY /
    GHOST_HANDS_ANTHROPIC_MODEL (or the ANTHROPIC_* spellings) from
    the environment; the key is never stored or trailed."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        batch: Optional[bool] = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("GHOST_HANDS_ANTHROPIC_BASE_URL")
            or os.environ.get("ANTHROPIC_BASE_URL")
            or "https://api.anthropic.com"
        ).rstrip("/")
        self.api_key = (
            api_key
            or os.environ.get("GHOST_HANDS_ANTHROPIC_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or ""
        )
        self.model = (
            model
            or os.environ.get("GHOST_HANDS_ANTHROPIC_MODEL")
            or os.environ.get("ANTHROPIC_MODEL")
            or "claude-sonnet-4-5"
        )
        self.timeout = timeout
        if batch is None:
            batch = _batch_from_env()
        self.batch = batch
        self.usage: dict = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "calls": 0,
            "retries": 0,
        }
        if not self.api_key:
            raise HandsError(
                "GHOST_HANDS_ANTHROPIC_API_KEY is not set; the Anthropic "
                "decider needs a key in the environment (it is never stored)"
            )

    def decide(self, goal: str, element_map: ElementMap, history: list[dict]) -> Action:
        return self._request_actions(goal, element_map, history, want_batch=False)[0]

    def decide_batch(
        self, goal: str, element_map: ElementMap, history: list[dict]
    ) -> list[Action]:
        return self._request_actions(goal, element_map, history, want_batch=True)

    def _request_actions(
        self, goal: str, element_map: ElementMap, history: list[dict], want_batch: bool
    ) -> list[Action]:
        body = {
            "model": self.model,
            "max_tokens": 1024,
            "temperature": 0,
            "system": _SYSTEM_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": _user_prompt(goal, element_map, history, want_batch),
                }
            ],
        }
        data = json.dumps(body).encode("utf-8")

        def make_request():
            return urllib.request.Request(
                f"{self.base_url}/v1/messages",
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                },
                method="POST",
            )

        payload = _post_json(make_request, self.timeout, self.usage)
        usage = payload.get("usage") or {}
        self.usage["calls"] += 1
        for ours, theirs in (
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
        ):
            value = usage.get(theirs)
            if isinstance(value, int) and value >= 0:
                self.usage[ours] += value
        try:
            content = "".join(
                block.get("text", "")
                for block in payload["content"]
                if isinstance(block, dict) and block.get("type") == "text"
            )
        except (KeyError, TypeError) as exc:
            raise HandsError(f"unexpected decider response shape: {exc}") from exc
        if not content:
            raise HandsError("decider returned no text content")
        return _parse_actions(content)


class FallbackDecider:
    """v1.4: two deciders, one job. The primary answers; when it
    raises HandsError (endpoint down, bad reply, rate limit), the
    fallback answers instead and the failover is counted on
    ``failovers``. Token usage is the sum of both, so the Runner's
    accounting and budget cap see the run's true cost. Batch mode
    is on when either side supports it; a side without
    ``decide_batch`` contributes single actions."""

    def __init__(self, primary, fallback) -> None:
        self.primary = primary
        self.fallback = fallback
        self.failovers = 0
        self.batch = bool(
            getattr(primary, "batch", False)
            or getattr(fallback, "batch", False)
        )

    @property
    def usage(self) -> dict:
        total = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "calls": 0,
            "retries": 0,
        }
        for side in (self.primary, self.fallback):
            side_usage = getattr(side, "usage", None)
            if isinstance(side_usage, dict):
                for key in total:
                    total[key] += int(side_usage.get(key) or 0)
        return total

    def decide(self, goal, element_map, history) -> Action:
        try:
            return self.primary.decide(goal, element_map, history)
        except HandsError:
            self.failovers += 1
            return self.fallback.decide(goal, element_map, history)

    def decide_batch(self, goal, element_map, history) -> list[Action]:
        def ask(side) -> list[Action]:
            batch_fn = getattr(side, "decide_batch", None)
            if batch_fn is not None and getattr(side, "batch", True):
                return batch_fn(goal, element_map, history)
            return [side.decide(goal, element_map, history)]

        try:
            return ask(self.primary)
        except HandsError:
            self.failovers += 1
            return ask(self.fallback)
