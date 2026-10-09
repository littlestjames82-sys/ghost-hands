"""SimPhoneDriver — an in-memory Android-style body for Ghost Hands.

This is the second body proving the Body Protocol (docs/BODY_PROTOCOL.md):
the same Runner, Governor, Deciders, and Trail that drive Chromium on
the web drive this simulated phone, unchanged. It models just enough of
a phone to be honest about the contract:

- a **home screen** with app icons ("Notes", "Settings"),
- a **Notes app**: a list, an editor (type, tap Save, the note
  persists), and a "Delete all notes" button,
- a **Settings app**: toggle switches that flip when tapped,
- a **navigation stack**: ``press Back`` pops, ``press Home`` resets,
- ``screenshot`` renders a *real PNG* (stdlib zlib/struct, tiny 3x5
  bitmap font) of the current screen's text.

Semantics map to the phone world: click = tap, type = set text on the
focused field, press Back/Home = the system keys. Actions the body
cannot do (drag, fill_form, set_file, ...) fail honestly with a
HandsError naming the gap — never a fake success.

The real Android body (MrGhosty's accessibility service) is future
work; the mapping table in docs/BODY_PROTOCOL.md defines how it will
implement this same contract.
"""

from __future__ import annotations

import binascii
import struct
import time
import zlib
from pathlib import Path
from typing import Any, Callable, Optional

from .actions import Action
from .errors import HandsError
from .eyes import Element, ElementMap

HOME_URL = "phone://home"

_SCREEN_TITLES = {
    "home": "Home",
    "notes": "Notes",
    "notes_edit": "Edit note",
    "settings": "Settings",
}

_SCREEN_URLS = {
    "home": "phone://home",
    "notes": "phone://notes",
    "notes_edit": "phone://notes/edit",
    "settings": "phone://settings",
}


# ---------------------------------------------------------------------------
# Tiny PNG text renderer (stdlib only): 3x5 bitmap font, scaled.
# ---------------------------------------------------------------------------

_FONT: dict[str, tuple[str, ...]] = {
    "A": ("010", "101", "111", "101", "101"),
    "B": ("110", "101", "110", "101", "110"),
    "C": ("011", "100", "100", "100", "011"),
    "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"),
    "F": ("111", "100", "110", "100", "100"),
    "G": ("011", "100", "101", "101", "011"),
    "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"),
    "J": ("001", "001", "001", "101", "010"),
    "K": ("101", "101", "110", "101", "101"),
    "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"),
    "N": ("101", "111", "111", "111", "101"),
    "O": ("010", "101", "101", "101", "010"),
    "P": ("110", "101", "110", "100", "100"),
    "Q": ("010", "101", "101", "010", "001"),
    "R": ("110", "101", "110", "101", "101"),
    "S": ("011", "100", "010", "001", "110"),
    "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"),
    "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "101", "111", "101"),
    "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"),
    "Z": ("111", "001", "010", "100", "111"),
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "010", "010", "010", "010"),
    "2": ("110", "001", "010", "100", "111"),
    "3": ("110", "001", "010", "001", "110"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "110", "001", "110"),
    "6": ("011", "100", "110", "101", "010"),
    "7": ("111", "001", "001", "010", "010"),
    "8": ("010", "101", "010", "101", "010"),
    "9": ("010", "101", "011", "001", "110"),
    " ": ("000", "000", "000", "000", "000"),
    ".": ("000", "000", "000", "000", "010"),
    ",": ("000", "000", "000", "010", "100"),
    ":": ("000", "010", "000", "010", "000"),
    ";": ("000", "010", "000", "010", "100"),
    "-": ("000", "000", "111", "000", "000"),
    "_": ("000", "000", "000", "000", "111"),
    "/": ("001", "001", "010", "100", "100"),
    "(": ("001", "010", "010", "010", "001"),
    ")": ("100", "010", "010", "010", "100"),
    "[": ("011", "010", "010", "010", "011"),
    "]": ("110", "010", "010", "010", "110"),
    "+": ("000", "010", "111", "010", "000"),
    "#": ("101", "111", "101", "111", "101"),
    "!": ("010", "010", "010", "000", "010"),
    "?": ("110", "001", "010", "000", "010"),
    "'": ("010", "010", "000", "000", "000"),
    "=": ("000", "111", "000", "111", "000"),
    ">": ("100", "010", "001", "010", "100"),
    "<": ("001", "010", "100", "010", "001"),
    "*": ("000", "101", "010", "101", "000"),
    "%": ("101", "001", "010", "100", "101"),
}
_FALLBACK_GLYPH = ("111", "111", "111", "111", "111")

_BG = (26, 24, 43)
_FG = (235, 233, 255)
_ACCENT = (103, 22, 243)  # Ghost Developer Studio violet


def render_text_png(lines: list[str], scale: int = 3) -> bytes:
    """Render text lines to a real PNG using only zlib/struct.

    Deliberately minimal — a legible rendering of the screen's text, not
    a pixel-faithful phone screenshot. Returns the PNG bytes."""
    char_w = 4 * scale  # 3 glyph columns + 1 spacing, scaled
    line_h = 7 * scale  # 5 glyph rows + 2 spacing, scaled
    pad = 4 * scale
    longest = max((len(line) for line in lines), default=0)
    width = pad * 2 + max(longest, 1) * char_w
    height = pad * 2 + max(len(lines), 1) * line_h
    pixels = bytearray(bytes(_BG) * (width * height))

    def put(x: int, y: int, rgb: tuple[int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            i = (y * width + x) * 3
            pixels[i : i + 3] = bytes(rgb)

    # Violet accent bar down the left edge.
    for y in range(height):
        for x in range(scale * 2):
            put(x, y, _ACCENT)

    for row_i, line in enumerate(lines):
        y0 = pad + row_i * line_h
        for col_i, ch in enumerate(line.upper()):
            glyph = _FONT.get(ch, _FALLBACK_GLYPH)
            x0 = pad + col_i * char_w
            for gy, bits in enumerate(glyph):
                for gx, bit in enumerate(bits):
                    if bit == "1":
                        for dy in range(scale):
                            for dx in range(scale):
                                put(x0 + gx * scale + dx, y0 + gy * scale + dy, _FG)

    raw = b"".join(
        b"\x00" + bytes(pixels[y * width * 3 : (y + 1) * width * 3])
        for y in range(height)
    )

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", binascii.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw)))
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# The simulated phone
# ---------------------------------------------------------------------------


class SimPhoneDriver:
    """An in-memory phone. See the module docstring for the model."""

    #: Body Protocol capability flags (docs/BODY_PROTOCOL.md).
    capabilities = {
        "perceive": True,
        "screenshot": True,
        "tabs": False,
        "session": False,
        "viewport": False,
        "pdf": False,
        "network_capture": False,
    }

    def __init__(self) -> None:
        self._stack: list[str] = ["home"]
        self.notes: list[str] = []
        self._draft: str = ""
        self._editing: Optional[int] = None  # index into notes, or None = new
        self.toggles: dict[str, bool] = {"Wi-Fi": True, "Bluetooth": False}
        self.interactions: list[str] = []
        self.event_sink: Optional[Callable[[str, dict], None]] = None
        self.last_screenshot: Optional[bytes] = None

    # -- screen model -------------------------------------------------------
    @property
    def _screen(self) -> str:
        return self._stack[-1]

    def _screen_items(self) -> list[dict[str, Any]]:
        """The current screen as element dicts (ElementMap.from_elements
        input). Numbering is positional and deterministic per screen."""
        screen = self._screen
        if screen == "home":
            return [
                {"tag": "button", "role": "app_icon", "name": "Notes", "id": "app:notes"},
                {"tag": "button", "role": "app_icon", "name": "Settings", "id": "app:settings"},
            ]
        if screen == "notes":
            items: list[dict[str, Any]] = [
                {
                    "tag": "button",
                    "role": "note",
                    "name": note,
                    "id": f"note:item:{i}",
                    "value": note,
                }
                for i, note in enumerate(self.notes)
            ]
            items.append(
                {"tag": "button", "role": "button", "name": "New note", "id": "note:new"}
            )
            items.append(
                {
                    "tag": "button",
                    "role": "button",
                    "name": "Delete all notes",
                    "id": "note:delete-all",
                }
            )
            return items
        if screen == "notes_edit":
            return [
                {
                    "tag": "textarea",
                    "role": "text_field",
                    "name": "Note text",
                    "id": "field:note-text",
                    "value": self._draft,
                },
                {"tag": "button", "role": "button", "name": "Save", "id": "btn:save"},
            ]
        if screen == "settings":
            return [
                {
                    "tag": "button",
                    "role": "toggle",
                    "name": name,
                    "id": f"toggle:{name}",
                    "value": "on" if on else "off",
                }
                for name, on in self.toggles.items()
            ]
        return []

    def _screen_text(self) -> str:
        screen = self._screen
        if screen == "home":
            return "Home — apps: Notes, Settings"
        if screen == "notes":
            return "\n".join(self.notes) if self.notes else "(no notes yet)"
        if screen == "notes_edit":
            return self._draft
        if screen == "settings":
            return "\n".join(
                f"{name}: {'on' if on else 'off'}" for name, on in self.toggles.items()
            )
        return ""

    # -- Driver protocol ------------------------------------------------------
    def open(self, url: str) -> None:
        target = (url or HOME_URL).rstrip("/").lower()
        for screen, screen_url in _SCREEN_URLS.items():
            if target == screen_url.rstrip("/"):
                self._stack = ["home"] if screen == "home" else ["home", screen]
                return
        if target in ("", "phone:", "phone://"):
            self._stack = ["home"]
            return
        raise HandsError(f"SimPhoneDriver cannot open {url!r} (not a phone:// screen)")

    def current_url(self) -> str:
        return _SCREEN_URLS[self._screen]

    def perceive(self) -> ElementMap:
        items = self._screen_items()
        element_map = ElementMap.from_elements(items, url=self.current_url())
        element_map.title = _SCREEN_TITLES[self._screen]
        return element_map

    def snapshot_html(self) -> str:
        """An HTML rendering of the current screen — the offline-eyes
        fallback, shaped so ElementMap.from_html sees the same controls."""
        parts = [
            f"<html><head><title>{_SCREEN_TITLES[self._screen]}</title></head><body>"
        ]
        for item in self._screen_items():
            if item["role"] == "text_field":
                parts.append(
                    f'<textarea id="note-text" placeholder="{item["name"]}">'
                    f'{item.get("value", "")}</textarea>'
                )
            else:
                parts.append(f'<button>{item["name"]}</button>')
        parts.append("</body></html>")
        return "".join(parts)

    def close(self) -> None:
        pass

    # -- internals ------------------------------------------------------------
    def _verify_target(self, element: Element) -> None:
        fresh = self.perceive()
        current = fresh.get(element.number)
        if current is None:
            raise HandsError(
                f"target missing: [{element.number}] <{element.tag}> "
                f'"{element.name}" is no longer on the screen'
            )
        if current.key() != element.key():
            raise HandsError(
                f"target mismatch: [{element.number}] was <{element.tag}> "
                f'"{element.name}", now <{current.tag}> "{current.name}"'
            )

    def _push(self, screen: str) -> None:
        if self._stack[-1] != screen:
            self._stack.append(screen)

    def _back(self) -> str:
        if len(self._stack) > 1:
            self._stack.pop()
            return f"back -> {_SCREEN_TITLES[self._screen]}"
        return "back at home screen (nowhere to go)"

    def _tap(self, element: Element) -> str:
        eid = element.id or ""
        if eid == "app:notes":
            self._push("notes")
            return 'tapped app icon "Notes" -> Notes'
        if eid == "app:settings":
            self._push("settings")
            return 'tapped app icon "Settings" -> Settings'
        if eid == "note:new":
            self._draft = ""
            self._editing = None
            self._push("notes_edit")
            return "tapped \"New note\" -> editor"
        if eid.startswith("note:item:"):
            idx = int(eid.rsplit(":", 1)[1])
            self._editing = idx
            self._draft = self.notes[idx]
            self._push("notes_edit")
            return f"tapped note [{idx}] -> editor"
        if eid == "note:delete-all":
            count = len(self.notes)
            self.notes.clear()
            return f"tapped \"Delete all notes\" -> deleted {count} note(s)"
        if eid == "btn:save":
            if self._editing is not None and self._editing < len(self.notes):
                self.notes[self._editing] = self._draft
                verb = "updated"
            else:
                self.notes.append(self._draft)
                verb = "saved"
            text, self._draft, self._editing = self._draft, "", None
            self._back()
            return f"tapped \"Save\" -> note {verb}: {text!r}"
        if eid.startswith("toggle:"):
            name = eid.split(":", 1)[1]
            self.toggles[name] = not self.toggles[name]
            state = "on" if self.toggles[name] else "off"
            return f"tapped toggle \"{name}\" -> {state}"
        return f"tapped [{element.number}] \"{element.name}\" (no effect)"

    # -- actions ---------------------------------------------------------------
    def act(self, action: Action, element: Optional[Element]) -> str:
        kind = action.kind
        if kind == "navigate":
            self.open(action.url or HOME_URL)
            return f"navigated to {self.current_url()}"
        if kind == "press":
            key = (action.key or "").strip()
            self.interactions.append(f"press {key}")
            if key.lower() == "back":
                return self._back()
            if key.lower() == "home":
                self._stack = ["home"]
                return "home -> Home"
            return f"pressed {key} (sim phone)"
        if kind == "wait":
            if action.text:
                if action.text in self._screen_text():
                    return f"wait satisfied: text {action.text!r} (after 0.0s)"
                raise HandsError(
                    f"wait timed out waiting for text {action.text!r} on the phone screen"
                )
            time.sleep(min(float(action.seconds or 0), 5.0))
            return f"waited {action.seconds}s"
        if kind == "scroll":
            return f"scrolled {action.direction or 'down'} (sim phone: one screen)"
        if kind == "extract":
            if element is not None:
                self._verify_target(element)
                if element.value is not None:
                    return element.value
                return element.name
            return self._screen_text()
        if kind == "screenshot":
            lines = [f"SIM PHONE - {_SCREEN_TITLES[self._screen].upper()}"]
            for item in self._screen_items():
                extra = f" = {item['value']}" if item.get("value") else ""
                lines.append(f"{item['name']}{extra}")
            if self._screen == "notes" and not self.notes:
                lines.append("(no notes)")
            png = render_text_png(lines)
            self.last_screenshot = png
            if action.path:
                Path(action.path).write_bytes(png)
                return f"screenshot saved to {action.path} (PNG, {len(png)} bytes)"
            return f"screenshot rendered (PNG, {len(png)} bytes; pass a path to save it)"

        if kind in ("click", "type", "select", "hover", "double_click", "right_click"):
            if element is not None:
                self._verify_target(element)
        if kind == "click":
            if element is None:
                raise HandsError("click requires a target element")
            return self._tap(element)
        if kind == "type":
            if element is None:
                raise HandsError("type requires a target element")
            if element.id != "field:note-text":
                raise HandsError(
                    f"cannot type into [{element.number}] \"{element.name}\" — not a text field"
                )
            self._draft = action.text or ""
            return f"typed into [{element.number}] \"{element.name}\""
        if kind in ("hover", "double_click", "right_click"):
            if element is None:
                raise HandsError(f"{kind} requires a target element")
            self.interactions.append(f"{kind} [{element.number}]")
            return f"{kind} [{element.number}] \"{element.name}\" (sim phone)"
        if kind == "select":
            raise HandsError("SimPhoneDriver has no select controls")
        raise HandsError(
            f"SimPhoneDriver cannot perform {kind!r} — the phone body does not "
            "support it (see docs/BODY_PROTOCOL.md capability flags)"
        )
