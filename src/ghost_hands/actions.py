"""Typed, JSON-serializable actions — the only moves Ghost Hands can make.

A target is always an integer element number taken from the current
ElementMap (see eyes.py), never a raw selector. That keeps every decision
auditable: the trail records exactly which numbered element was acted on.

v0.3 vocabulary additions: hover, double_click, right_click, drag,
click_at (raw coordinates — the documented fallback for canvas pages),
fill_form (one governed action filling many fields), set_file (uploads),
download, pdf, set_viewport, plus richer extract modes (list / table /
network) and conditioned waits (wait for text / element / URL).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

ACTION_KINDS = (
    "navigate",
    "click",
    "type",
    "press",
    "select",
    "scroll",
    "extract",
    "screenshot",
    "wait",
    "done",
    # v0.3
    "hover",
    "double_click",
    "right_click",
    "drag",
    "click_at",
    "fill_form",
    "set_file",
    "download",
    "pdf",
    "set_viewport",
)

EXTRACT_MODES = ("text", "list", "table", "network")

_FIELDS = (
    "target",
    "url",
    "text",
    "key",
    "value",
    "direction",
    "amount",
    "seconds",
    "summary",
    "path",
    # v0.3
    "mode",
    "fields",
    "submit",
    "to_target",
    "x",
    "y",
    "width",
    "height",
)


@dataclass
class Action:
    """One atomic move. All fields except ``kind`` are optional."""

    kind: str
    target: Optional[int] = None
    url: Optional[str] = None
    text: Optional[str] = None
    key: Optional[str] = None
    value: Optional[str] = None
    direction: Optional[str] = None
    amount: Optional[int] = None
    seconds: Optional[float] = None
    summary: Optional[str] = None
    path: Optional[str] = None
    # v0.3 fields
    mode: Optional[str] = None  # extract mode: text | list | table | network
    fields: Optional[dict] = None  # fill_form: {label-or-name: value}
    submit: bool = False  # fill_form: also submit the form (consequential)
    to_target: Optional[int] = None  # drag destination element number
    x: Optional[float] = None  # click_at coordinates / viewport pieces
    y: Optional[float] = None
    width: Optional[int] = None  # set_viewport custom size
    height: Optional[int] = None

    def __post_init__(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise ValueError(f"unknown action kind: {self.kind!r}")
        if self.kind == "extract" and self.mode is not None and self.mode not in EXTRACT_MODES:
            raise ValueError(f"unknown extract mode: {self.mode!r}")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        for field in _FIELDS:
            val = getattr(self, field)
            if field == "submit":
                if val:
                    out[field] = True
                continue
            if val is not None:
                out[field] = val
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Action":
        if not isinstance(data, dict) or "kind" not in data:
            raise ValueError(f"action dict must carry a 'kind': {data!r}")
        known = {k: v for k, v in data.items() if k in ("kind",) + _FIELDS}
        return cls(**known)

    def signature(self) -> tuple:
        """Stable identity used by the Runner's stuck detection."""
        return (
            self.kind,
            self.target,
            self.url,
            self.text,
            self.key,
            self.value,
            self.mode,
            self.to_target,
            self.x,
            self.y,
            json.dumps(self.fields, sort_keys=True) if self.fields else None,
            self.submit,
        )


# -- Constructors (the vocabulary used by deciders, scripts, and the CLI) --


def navigate(url: str) -> Action:
    return Action(kind="navigate", url=url)


def click(target: int) -> Action:
    return Action(kind="click", target=int(target))


def type(target: int, text: str) -> Action:  # noqa: A001 - action vocabulary
    return Action(kind="type", target=int(target), text=text)


def press(key: str) -> Action:
    """Press a key — or a chord, e.g. press("Control+a")."""
    return Action(kind="press", key=key)


def select(target: int, value: str) -> Action:
    return Action(kind="select", target=int(target), value=value)


def scroll(direction: str = "down", amount: int = 500) -> Action:
    return Action(kind="scroll", direction=direction, amount=int(amount))


def extract(target: Optional[int] = None, mode: str = "text") -> Action:
    return Action(kind="extract", target=target, mode=mode)


def screenshot(path: Optional[str] = None) -> Action:
    return Action(kind="screenshot", path=path)


def wait(seconds: float) -> Action:
    return Action(kind="wait", seconds=float(seconds))


def wait_for(
    text: Optional[str] = None,
    target: Optional[int] = None,
    url_contains: Optional[str] = None,
    timeout: float = 10.0,
) -> Action:
    """Wait until a condition holds (text visible / element present /
    URL contains), up to ``timeout`` seconds. Times out with an honest
    error — never silently."""
    return Action(
        kind="wait",
        seconds=float(timeout),
        text=text,
        target=int(target) if target is not None else None,
        url=url_contains,
    )


def done(summary: str = "") -> Action:
    return Action(kind="done", summary=summary)


# -- v0.3 constructors -------------------------------------------------------


def hover(target: int) -> Action:
    return Action(kind="hover", target=int(target))


def double_click(target: int) -> Action:
    return Action(kind="double_click", target=int(target))


def right_click(target: int) -> Action:
    return Action(kind="right_click", target=int(target))


def drag(target: int, to_target: int) -> Action:
    return Action(kind="drag", target=int(target), to_target=int(to_target))


def click_at(x: float, y: float) -> Action:
    """Raw-coordinate click — the fallback for canvas/coordinate pages
    where no element map applies. Governed as a write; the trail marks
    it raw:true so coordinate moves are always visible in the record."""
    return Action(kind="click_at", x=float(x), y=float(y))


def fill_form(fields: dict, submit: bool = False) -> Action:
    """Fill several fields in one governed action. Keys are field
    descriptors (label text, name, placeholder, aria-label, id); values
    are the strings to enter. ``submit=True`` also triggers the form's
    submit control and makes the action consequential."""
    return Action(kind="fill_form", fields=dict(fields), submit=bool(submit))


def set_file(target: int, path: str) -> Action:
    """Attach a local file to an <input type=file>. The path must exist
    and be a file; directories are refused."""
    return Action(kind="set_file", target=int(target), path=path)


def download(target: Optional[int] = None, url: Optional[str] = None) -> Action:
    """Start a download (click the target link, or navigate to the URL)
    and wait for it to complete. The result names the file and its size;
    completion is also a ``download`` trail event."""
    return Action(kind="download", target=target, url=url)


def pdf(path: Optional[str] = None) -> Action:
    return Action(kind="pdf", path=path)


def set_viewport(preset: Optional[str] = None, width=None, height=None) -> Action:
    """Emulate a viewport: a named preset ("desktop" 1280x800, "mobile"
    390x844 with touch) or explicit width/height."""
    return Action(kind="set_viewport", value=preset, width=width, height=height)
