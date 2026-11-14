"""Eyes: turn an HTML snapshot into a numbered ElementMap.

Perception is deliberately cheap and text-only — no screenshots, no vision
model. The map is what a decider reads, what the governor reasons about, and
what the trail records, so one perception serves all three.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Optional

INTERACTIVE_TAGS = {"a", "button", "input", "select", "textarea"}
VOID_TAGS = {
    "input",
    "br",
    "img",
    "hr",
    "meta",
    "link",
    "area",
    "base",
    "col",
    "embed",
    "source",
    "track",
    "wbr",
}

# The selector ChromiumDriver uses to enumerate the same elements in the
# live page, in the same document order the parser below sees them.
INTERACTIVE_SELECTOR = (
    "a, button, input, select, textarea, [role='button'], [onclick]"
)


def _role_for(tag: str, attrs: dict[str, str]) -> str:
    explicit = attrs.get("role")
    if explicit:
        return explicit
    if tag == "a":
        return "link"
    if tag == "button":
        return "button"
    if tag == "select":
        return "combobox"
    if tag == "textarea":
        return "textbox"
    if tag == "input":
        itype = (attrs.get("type") or "text").lower()
        if itype in ("submit", "button", "reset", "image"):
            return "button"
        if itype == "checkbox":
            return "checkbox"
        if itype == "radio":
            return "radio"
        return "textbox"
    return "button"


@dataclass
class Element:
    number: int
    tag: str
    role: str
    name: str
    type: Optional[str] = None
    href: Optional[str] = None
    id: Optional[str] = None
    attr_name: Optional[str] = None
    placeholder: Optional[str] = None
    value: Optional[str] = None
    form_action: Optional[str] = None
    attrs: dict[str, str] = field(default_factory=dict)
    # v0.3: the field's <label> text (fill_form matches on it), the frame
    # the element lives in (None = top document), and — for AX-eyes maps —
    # the CDP backend node id used to resolve the node for actions.
    label: Optional[str] = None
    frame: Optional[str] = None
    backend_id: Optional[int] = None
    # v1.12 (P0-1): multi-signal identity for pre-action revalidation
    # and containment-checked healing. ``text`` is the element's visible
    # text (distinct from ``name``, which may come from aria-label or a
    # placeholder); ``box`` is the perception-time bounding box
    # {"x","y","w","h"} in viewport coordinates (Chromium bodies fill
    # it; fake/sim bodies leave it None); ``nth`` is the element's
    # 1-based ordinal among the map's elements sharing its (role, name),
    # assigned when the map is built — a tiebreak signal when several
    # elements share a name.
    text: Optional[str] = None
    box: Optional[dict] = None
    nth: Optional[int] = None
    # v1.13 (effect feedback): a checkbox/radio's live checked state,
    # when the perceiving body reports it. Like ``value`` (the field's
    # live content, rendered by :meth:`ElementMap.render`), it is an
    # *effect* signal, not an identity signal: both change precisely
    # because an action landed, so neither ever enters identity().
    checked: Optional[bool] = None
    # v0.5: an opaque body-native target reference (the Android bridge's
    # child-index path, e.g. "0/2/1"). Bodies that resolve targets by
    # their own addressing fill this in; web bodies leave it None. It is
    # recorded in descriptors so a trail shows exactly what was acted on.
    body_ref: Optional[str] = None

    def key(self) -> tuple:
        """Identity used for self-healing matches: tag + role + name."""
        return (self.tag, self.role, self.name)

    def identity(self) -> dict[str, Any]:
        """v1.12 (P0-1): every identity signal recorded for this element,
        as a plain dict — the shape ``find_heal_candidate`` checks a
        descriptor against. Signals that were not captured are absent,
        and absent signals never disqualify a candidate."""
        out: dict[str, Any] = {
            "tag": self.tag,
            "role": self.role,
            "name": self.name,
        }
        for key in ("text", "nth", "id", "href", "attr_name", "type", "label", "frame"):
            val = getattr(self, key)
            if val is not None:
                out[key] = val
        return out

    def descriptor(self) -> dict[str, Any]:
        """The subset recorded in the trail so a run can be replayed."""
        out: dict[str, Any] = {
            "number": self.number,
            "tag": self.tag,
            "role": self.role,
            "name": self.name,
        }
        for key in (
            "type",
            "href",
            "id",
            "attr_name",
            "placeholder",
            "value",
            "label",
            "frame",
            "backend_id",
            "body_ref",
            "text",
            "box",
            "nth",
            "checked",
        ):
            val = getattr(self, key)
            if val is not None:
                out[key] = val
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any], number: Optional[int] = None) -> "Element":
        attrs = dict(data.get("attrs", {}))
        tag = str(data.get("tag", ""))
        role = str(data.get("role") or "")
        if not role:
            role = _role_for(tag, attrs)
        return cls(
            number=int(number if number is not None else data.get("number", 0)),
            tag=tag,
            role=role,
            name=str(data.get("name", "")),
            type=data.get("type"),
            href=data.get("href"),
            id=data.get("id"),
            attr_name=data.get("attr_name"),
            placeholder=data.get("placeholder"),
            value=data.get("value"),
            form_action=data.get("form_action"),
            attrs=attrs,
            label=data.get("label"),
            frame=data.get("frame"),
            backend_id=data.get("backend_id"),
            body_ref=data.get("body_ref"),
            text=data.get("text"),
            box=data.get("box") if isinstance(data.get("box"), dict) else None,
            nth=data.get("nth"),
            checked=data.get("checked")
            if isinstance(data.get("checked"), bool)
            else None,
        )


# ---------------------------------------------------------------------------
# Sensitive field values (v1.13): masking at ingestion
# ---------------------------------------------------------------------------
# Effect feedback carries each field's live value into the element
# map — and the map is what a decider reads, what a descriptor
# records, and what the trail preserves. Ordinary working data
# (names, emails, addresses, note text) renders in full: seeing it
# is the point. But credentials, payment and government
# identifiers, one-time codes, and secret tokens are masked AT
# INGESTION — the moment a value enters an Element — so the raw
# string never reaches a render, a descriptor, or a trail through
# perception, in any body. (An explicit ``extract`` action is an
# operator-intended read and still reads the live page; masking
# governs perception, not extraction.) The masked form follows the
# phone approver's precedent (v0.6): presence + character count,
# never content — ``[masked · 16 chars]`` — so a decider can still
# see that a value landed, and the map signature still moves when
# a masked value's length changes.
#
# Control knob: GHOST_HANDS_FIELD_VALUES = masked (default) |
# full | off. ``full`` renders raw values (passwords excepted — a
# password's content is never stored or rendered in ANY mode; that
# rule is absolute). ``off`` stores no field values at all,
# restoring pre-v1.13 perception. The knob is read at ingestion
# time, the way GHOST_HANDS_QUIET is read at wait time.

_MASK_MARKER_RE = re.compile(r"\[masked · \d+ chars\]")

# Field-purpose hints, matched against the field's own words (its
# name/id/placeholder/label/aria-label, tokenized on separators and
# camelCase). Kept tight on purpose: a benign field named "note"
# or "search" never trips them.
_SENSITIVE_HINT_TOKENS = {
    "card", "cvv", "cvc", "ssn", "routing", "iban", "otp", "2fa",
    "token", "apikey", "secret",
    # compounds that tokenize as a single word
    "cardnumber", "creditcard", "taxid",
}
_SENSITIVE_HINT_PHRASES = {
    "social security", "tax id", "one time", "api key", "security code",
}


def field_values_mode() -> str:
    """The GHOST_HANDS_FIELD_VALUES knob: masked | full | off.
    Anything unrecognized reads as the default, ``masked``."""
    mode = os.environ.get("GHOST_HANDS_FIELD_VALUES", "masked").strip().lower()
    return mode if mode in ("masked", "full", "off") else "masked"


def _mask_marker(value: str) -> str:
    return f"[masked · {len(value)} chars]"


def _hint_tokens(text: str) -> list[str]:
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text or "")
    return re.findall(r"[a-z0-9]+", camel_split.lower())


def _hints_sensitive(hints: str) -> bool:
    tokens = _hint_tokens(hints)
    if any(t in _SENSITIVE_HINT_TOKENS for t in tokens):
        return True
    joined = " ".join(tokens)
    return any(p in joined for p in _SENSITIVE_HINT_PHRASES)


def _autocomplete_sensitive(autocomplete: Optional[str]) -> bool:
    if not autocomplete:
        return False
    for token in autocomplete.lower().split():
        if token.startswith("cc-") or token == "one-time-code":
            return True
    return False


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _value_looks_sensitive(value: str) -> bool:
    """Shape-only checks, applied whatever the field claims to be.
    A bare short number (a 6-digit code in an unmarked field, a
    quantity) is NOT sensitive by shape alone — one-time codes mask
    via their field signals, never by pattern."""
    stripped = value.strip()
    # (a) card number: digits/spaces/dashes, 13–19 digits, Luhn-valid
    compact = stripped.replace(" ", "").replace("-", "")
    if compact.isdigit() and 13 <= len(compact) <= 19 and _luhn_ok(compact):
        return True
    # (b) US SSN shape
    if re.fullmatch(r"\d{3}-\d{2}-\d{4}", stripped):
        return True
    # (c) secret-token shape: long, unspaced, not a URL, and drawn
    # from at least 3 of {lowercase, uppercase, digits, symbols} —
    # a sentence (spaces) or a link never trips it.
    if len(value) >= 20 and not any(ch.isspace() for ch in value):
        if not value.lower().startswith(("http://", "https://")):
            classes = (
                any(c.islower() for c in value)
                + any(c.isupper() for c in value)
                + any(c.isdigit() for c in value)
                + any(not c.isalnum() for c in value)
            )
            if classes >= 3:
                return True
    return False


def mask_field_value(
    value: Optional[str],
    *,
    input_type: Optional[str] = None,
    autocomplete: Optional[str] = None,
    hints: str = "",
) -> Optional[str]:
    """Apply the field-value policy to one ingested value.

    Returns the value to store on the Element: the raw value for
    ordinary data, a ``[masked · N chars]`` marker for sensitive
    data, or None when nothing may be stored (a password input in
    every mode; any field in ``off`` mode). Idempotent — a marker
    passes through unchanged — so layered ingestion points may
    each apply it safely. ``hints`` is the field-purpose text
    (name/id/placeholder/label/aria-label); where a body cannot
    supply a signal, the value-shape checks still apply."""
    if value is None:
        return None
    mode = field_values_mode()
    if mode == "off":
        return None
    if (input_type or "").lower() == "password":
        return None
    if _MASK_MARKER_RE.fullmatch(value):
        return value
    if mode == "full":
        return value
    if (
        _autocomplete_sensitive(autocomplete)
        or _hints_sensitive(hints)
        or _value_looks_sensitive(value)
    ):
        return _mask_marker(value)
    return value


def element_hint_text(el: "Element") -> str:
    """The field-purpose words an Element carries: its name
    attribute, id, placeholder, label, and aria-label."""
    parts = [
        el.attr_name or "",
        el.id or "",
        el.placeholder or "",
        el.label or "",
        el.attrs.get("aria-label", ""),
    ]
    return " ".join(p for p in parts if p)


def _fallback_name(el: "Element") -> str:
    """_MapParser._finish_name's chain minus the value itself —
    used when a masked-away value had been serving as the name."""
    a = el.attrs
    if a.get("aria-label"):
        return a["aria-label"].strip()
    if el.placeholder:
        return el.placeholder
    if el.attr_name:
        return el.attr_name
    if el.id:
        return el.id
    return "(unnamed)"


def apply_field_value_mask(el: "Element") -> None:
    """Mask ``el.value`` in place — the ingestion step for bodies
    that build Elements outside the HTML parser (Chromium DOM/AX
    perception, the fake body's field overlay). When the raw value
    had doubled as the element's name or visible text (an unnamed
    input is named by its value; a textarea's content is both),
    those are scrubbed to the same marker or fallback, so the raw
    string survives nowhere in the map or a descriptor."""
    raw = el.value
    if raw is None:
        return
    masked = mask_field_value(
        raw,
        input_type=el.type,
        autocomplete=el.attrs.get("autocomplete"),
        hints=element_hint_text(el),
    )
    if masked == raw:
        return
    el.value = masked
    if el.name == raw:
        el.name = masked if masked is not None else _fallback_name(el)
    if el.text == raw:
        el.text = masked


class _MapParser(HTMLParser):
    def __init__(self, raw_values: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        # raw_values: parse for a live-source read (an explicit
        # extract), not for perception — values are stored raw.
        # Password values stay redacted even then; that rule is
        # absolute and lives in _make_element.
        self.raw_values = raw_values
        self.elements: list[Element] = []
        self.title: str = ""
        # stack entries: {tag, attrs, text:[], element|None, form_action|None}
        self._stack: list[dict[str, Any]] = []
        self._in_title = False
        self._title_parts: list[str] = []
        # v0.3: <label for="id"> associations, resolved after the parse.
        self.labels_by_for: dict[str, str] = {}
        # v1.13 (effect feedback): open <select> records, each
        # {"element": Element, "options": [(text, value_attr, selected)]},
        # so a select's current value (its selected option) can be
        # resolved when the select closes.
        self._open_selects: list[dict[str, Any]] = []

    def _apply_label(self, entry: dict[str, Any]) -> None:
        """A closing <label>: its text labels the controls it wraps, and
        (via `for`) the control whose id it names."""
        if entry["tag"] != "label":
            return
        text = " ".join("".join(entry["text"]).split())
        if not text:
            return
        for el in entry.get("wrapped", []):
            if el.label is None:
                el.label = text
        target = entry["attrs"].get("for")
        if target:
            self.labels_by_for[target] = text

    # -- helpers ---------------------------------------------------------
    def _current_form_action(self) -> Optional[str]:
        for entry in reversed(self._stack):
            if entry["tag"] == "form":
                return entry["attrs"].get("action")
        return None

    def _mask_static_value(
        self, value: Optional[str], input_type: Optional[str], attrs: dict[str, str]
    ) -> Optional[str]:
        """Mask one statically-parsed value with the context the
        attributes carry. Runs at element creation so a masked
        value can never leak onward into a value-derived name
        (_finish_name's fallback); label-purpose hints are not
        resolved yet at this point, so ElementMap.from_html
        re-applies the policy as a post-pass once labels land."""
        if value is None or self.raw_values:
            return value
        hints = " ".join(
            p
            for p in (
                attrs.get("name"),
                attrs.get("id"),
                attrs.get("placeholder"),
                attrs.get("aria-label"),
            )
            if p
        )
        return mask_field_value(
            value,
            input_type=input_type,
            autocomplete=attrs.get("autocomplete"),
            hints=hints,
        )

    def _make_element(self, tag: str, attrs: dict[str, str]) -> Element:
        itype = (attrs.get("type") or "").lower() if tag == "input" else ""
        el = Element(
            number=len(self.elements) + 1,
            tag=tag,
            role=_role_for(tag, attrs),
            name="",
            type=attrs.get("type") if tag in ("input", "button") else None,
            href=attrs.get("href") if tag == "a" else None,
            id=attrs.get("id"),
            attr_name=attrs.get("name"),
            placeholder=attrs.get("placeholder"),
            # v1.13: a password input's value is redacted at the
            # source — it never enters the element, the rendered map,
            # a descriptor, or a trail. Any other value passes the
            # masking policy (see _mask_static_value).
            value=None
            if itype == "password"
            else self._mask_static_value(attrs.get("value"), itype or None, attrs),
            form_action=self._current_form_action(),
            attrs=dict(attrs),
            checked=("checked" in attrs)
            if itype in ("checkbox", "radio")
            else None,
        )
        self.elements.append(el)
        if tag == "select":
            self._open_selects.append({"element": el, "options": []})
        # If a <label> is currently open, this control is wrapped by it.
        for entry in self._stack:
            if entry["tag"] == "label":
                entry.setdefault("wrapped", []).append(el)
        return el

    @staticmethod
    def _finish_name(el: Element, text: str) -> None:
        a = el.attrs
        if a.get("aria-label"):
            el.name = a["aria-label"].strip()
        elif text:
            el.name = text
        elif el.placeholder:
            el.name = el.placeholder
        elif el.value and el.tag == "input":
            el.name = el.value
        elif el.attr_name:
            el.name = el.attr_name
        elif el.id:
            el.name = el.id
        else:
            el.name = "(unnamed)"

    # -- v1.13: <select> value resolution ---------------------------------
    def _finish_option_entry(self, entry: dict[str, Any]) -> None:
        """A closed <option>: record it on the innermost open select."""
        if not self._open_selects:
            return
        text = " ".join("".join(entry["text"]).split())
        self._open_selects[-1]["options"].append(
            (text, entry["attrs"].get("value"), "selected" in entry["attrs"])
        )

    def _finalize_select(self, el: Element) -> None:
        """Resolve a closed select's current value: the selected
        option's text (its value attribute when it has no text), or
        the first option's when none is marked selected — the same
        default a browser applies. Consumes the select's record."""
        for i in range(len(self._open_selects) - 1, -1, -1):
            record = self._open_selects[i]
            if record["element"] is el:
                del self._open_selects[i]
                options = record["options"]
                break
        else:
            return
        if not options:
            return
        chosen = next((o for o in options if o[2]), options[0])
        el.value = chosen[0] or chosen[1]

    # -- HTMLParser hooks -------------------------------------------------
    def handle_starttag(self, tag: str, attrs_list: list) -> None:
        attrs = {k: (v if v is not None else "") for k, v in attrs_list}
        if tag == "option" and self._stack and self._stack[-1]["tag"] == "option":
            # An <option> implicitly closes the previous one; finish
            # it now so its text stays its own (handle_data appends to
            # every open entry, so a lingering option would absorb the
            # next option's text).
            self._finish_option_entry(self._stack.pop())
        if tag == "title":
            self._in_title = True
        interactive = (
            tag in INTERACTIVE_TAGS or "role" in attrs and attrs.get("role") == "button"
            or "onclick" in attrs
        )
        if tag in VOID_TAGS:
            if interactive:
                el = self._make_element(tag, attrs)
                self._finish_name(el, "")
            return
        entry: dict[str, Any] = {"tag": tag, "attrs": attrs, "text": [], "element": None}
        if interactive:
            entry["element"] = self._make_element(tag, attrs)
        self._stack.append(entry)

    def handle_startendtag(self, tag: str, attrs_list: list) -> None:
        attrs = {k: (v if v is not None else "") for k, v in attrs_list}
        interactive = (
            tag in INTERACTIVE_TAGS
            or attrs.get("role") == "button"
            or "onclick" in attrs
        )
        if interactive:
            el = self._make_element(tag, attrs)
            # A self-closed element's name may fall back to its
            # value — but only the stored (policy-masked, and for
            # passwords always absent) form of it, never the raw
            # attribute (v1.13).
            self._finish_name(el, el.value or "")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        for entry in self._stack:
            entry["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            self.title = " ".join("".join(self._title_parts).split())
        # pop up to and including the matching open tag
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i]["tag"] == tag:
                popped = self._stack[i:]
                del self._stack[i:]
                # v1.13: options in this batch belong to a select in
                # the same batch (or still open below it) — record
                # them before that select's value is resolved.
                for entry in popped:
                    if entry["tag"] == "option":
                        self._finish_option_entry(entry)
                for entry in popped:
                    el = entry["element"]
                    if el is not None:
                        text = " ".join("".join(entry["text"]).split())
                        el.text = text or None  # v1.12: visible-text signal
                        self._finish_name(el, text)
                        if el.tag == "textarea" and el.value is None and text:
                            el.value = text  # v1.13: content is the value
                        if el.tag == "select":
                            self._finalize_select(el)  # v1.13
                    self._apply_label(entry)
                break


def _assign_nth(elements: list[Element]) -> None:
    """v1.12 (P0-1): stamp each element with its 1-based ordinal among
    the elements sharing its (role, name), in document order. Two
    "Delete" buttons become nth 1 and nth 2 — the tiebreak that lets a
    heal tell them apart when a page re-renders."""
    seen: dict[tuple, int] = {}
    for el in elements:
        group = (el.role, el.name)
        seen[group] = seen.get(group, 0) + 1
        el.nth = seen[group]


# Input types whose `value` is a label or a payload the map must not
# echo (a submit's caption is already its name; a hidden input's value
# is typically a token; a file input's is a local path).
_NO_VALUE_INPUT_TYPES = {"submit", "button", "reset", "image", "file", "hidden"}


def _effect_suffix(el: Element) -> str:
    """v1.13 (effect feedback): the control's live state as a render
    suffix — `` value="..."`` for fields carrying user data, ``
    checked`` for a ticked checkbox/radio — or "" when there is
    nothing meaningful to report. This is the signal that an action
    *landed*: a decider that typed "shift proof" sees it in the next
    map, and the map signature (a hash of the render) moves with it,
    so a completed-but-visually-static page no longer reads as
    stuck. A password input's content is NEVER rendered — not the
    text, not a marker that could confirm a guess."""
    if el.tag == "input":
        itype = (el.type or "text").lower()
        if itype in ("checkbox", "radio"):
            return " checked" if el.checked else ""
        if itype == "password" or itype in _NO_VALUE_INPUT_TYPES:
            return ""
    elif el.tag not in ("textarea", "select"):
        return ""
    if not el.value:
        return ""
    shown = " ".join(str(el.value).split())
    if len(shown) > 60:
        shown = shown[:59] + "…"
    shown = shown.replace("\\", "\\\\").replace('"', '\\"')
    return f' value="{shown}"'


def _line_for(el: Element) -> str:
    """One rendered map line. Shared by render() and render_focused()
    so the two can never drift apart."""
    line = f'[{el.number}] <{el.tag}> "{el.name}"'
    if el.type:
        line += f" type={el.type}"
    if el.href:
        line += f" href={el.href}"
    line += _effect_suffix(el)
    if el.frame:
        line += f" frame={el.frame}"
    return line


@dataclass
class ElementMap:
    elements: list[Element] = field(default_factory=list)
    url: str = ""
    title: str = ""
    budget: int = 4000
    source_hash: str = ""

    @classmethod
    def from_html(
        cls, html: str, url: str = "", budget: int = 4000, *, _raw: bool = False
    ) -> "ElementMap":
        # _raw: parse WITHOUT the field-value policy — reserved for
        # live-source reads (an explicit extract re-parsing the
        # page), never for perception. Password values stay
        # redacted even then.
        parser = _MapParser(raw_values=_raw)
        parser.feed(html or "")
        parser.close()
        # elements left open at EOF still need names
        for entry in parser._stack:
            el = entry["element"]
            if el is not None and not el.name:
                text = " ".join("".join(entry["text"]).split())
                el.text = text or None  # v1.12: visible-text signal
                _MapParser._finish_name(el, text)
            parser._apply_label(entry)
        # v1.13: controls left open at EOF (unclosed <select>,
        # <option>, <textarea>) still resolve their current values.
        for entry in parser._stack:
            if entry["tag"] == "option":
                parser._finish_option_entry(entry)
        for entry in parser._stack:
            el = entry["element"]
            if el is None:
                continue
            text = " ".join("".join(entry["text"]).split())
            if el.tag == "textarea" and el.value is None and text:
                el.value = text
            if el.tag == "select":
                parser._finalize_select(el)
        # resolve <label for="id"> associations
        for el in parser.elements:
            if el.id and el.label is None and el.id in parser.labels_by_for:
                el.label = parser.labels_by_for[el.id]
        if not _raw:
            # v1.13 masking post-pass: with labels resolved, every
            # parsed value (static attribute, textarea content,
            # select resolution) passes the policy with its full
            # field context. Idempotent over the creation-time pass.
            for el in parser.elements:
                apply_field_value_mask(el)
        _assign_nth(parser.elements)
        source_hash = hashlib.sha256((html or "").encode()).hexdigest()[:16]
        return cls(
            elements=parser.elements,
            url=url,
            title=parser.title,
            budget=budget,
            source_hash=source_hash,
        )

    @classmethod
    def from_elements(
        cls, items: list[dict[str, Any]], url: str = "", budget: int = 4000
    ) -> "ElementMap":
        """Accept a driver-supplied, pre-parsed element list."""
        els = [Element.from_dict(d, number=i + 1) for i, d in enumerate(items)]
        _assign_nth(els)
        source_hash = hashlib.sha256(repr(items).encode()).hexdigest()[:16]
        return cls(elements=els, url=url, budget=budget, source_hash=source_hash)

    def diff_note(self, previous: Optional["ElementMap"]) -> Optional[str]:
        """v1.4: a one-line summary of what changed since the previous
        map — navigation, elements appeared/gone (by identity key) —
        or None when nothing meaningful changed. The Runner hands
        this to the decider so it can orient on the delta instead of
        re-deriving it from the full map every step."""
        if previous is None:
            return None
        parts: list[str] = []
        if self.url != previous.url:
            parts.append(f"navigated to {self.url or '(same page)'}")
        from collections import Counter

        def label(key: tuple) -> str:
            tag, role, name = key
            shown = name or tag
            return f"{shown} ({role or tag})" if name else shown

        old_counts = Counter(el.key() for el in previous.elements)
        new_counts = Counter(el.key() for el in self.elements)
        appeared = [
            label(k) for k in (new_counts - old_counts).elements()
        ]
        gone = [label(k) for k in (old_counts - new_counts).elements()]
        if appeared:
            shown = ", ".join(appeared[:5])
            extra = f" (+{len(appeared) - 5} more)" if len(appeared) > 5 else ""
            parts.append(f"appeared: {shown}{extra}")
        if gone:
            shown = ", ".join(gone[:5])
            extra = f" (+{len(gone) - 5} more)" if len(gone) > 5 else ""
            parts.append(f"gone: {shown}{extra}")
        if not parts:
            return None
        return "; ".join(parts)

    def get(self, number: Optional[int]) -> Optional[Element]:
        if number is None:
            return None
        for el in self.elements:
            if el.number == number:
                return el
        return None

    def find_by_text(self, text: str) -> Optional[Element]:
        needle = text.strip().lower()
        for el in self.elements:
            if needle and needle in el.name.lower():
                return el
        return None

    def find_by_descriptor(self, descriptor: dict[str, Any]) -> Optional[Element]:
        """Re-find an element by the descriptor recorded at perception
        time (tag + role + name). This is the self-healing lookup: the
        element's *number* may have changed when the page mutated, but its
        identity usually has not."""
        wanted = (
            descriptor.get("tag", ""),
            descriptor.get("role", ""),
            descriptor.get("name", ""),
        )
        for el in self.elements:
            if el.key() == wanted:
                return el
        return None

    def find_heal_candidate(self, descriptor: dict[str, Any]) -> Optional[Element]:
        """v1.12 (P0-1): the containment-checked heal lookup. Stricter
        than :meth:`find_by_descriptor` on purpose — a heal moves an
        *approved* action onto a new element, so a wrong match is worse
        than no match:

        - the descriptor's element must have lived in the main document:
          an element inside a frame (``frame`` set) NEVER heals, and
          candidates inside frames are never eligible;
        - a candidate must match every identity signal the descriptor
          recorded: tag + role + name, plus id / href / attr_name /
          visible text whenever the descriptor carries them (absent
          signals, e.g. on pre-v1.12 descriptors, never disqualify);
        - every element in a map is interactive by construction, so a
          text match is always an interactive element;
        - ambiguity is refused: with several matching candidates, only
          an exact ``nth`` tiebreak singles one out — otherwise None,
          and the run fails honestly instead of clicking a guess."""
        if descriptor.get("frame"):
            return None
        wanted = (
            descriptor.get("tag", ""),
            descriptor.get("role", ""),
            descriptor.get("name", ""),
        )
        matches: list[Element] = []
        for el in self.elements:
            if el.frame:
                continue
            if el.key() != wanted:
                continue
            if descriptor.get("id") and el.id != descriptor["id"]:
                continue
            if descriptor.get("href") and el.href != descriptor["href"]:
                continue
            if descriptor.get("attr_name") and el.attr_name != descriptor["attr_name"]:
                continue
            dtext = descriptor.get("text")
            if dtext and el.text and el.text != dtext:
                continue
            matches.append(el)
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        nth = descriptor.get("nth")
        if nth is not None:
            exact = [el for el in matches if el.nth == nth]
            if len(exact) == 1:
                return exact[0]
        return None

    def map_diff(self, previous: Optional["ElementMap"], max_changed: int = 10) -> Optional[str]:
        """v1.12 (P0-3): a line-level diff (difflib) of the rendered map
        against the previous step's map — "- [3] ..." / "+ [3] ..."
        lines — returned only when the change is small enough to be
        more useful than the summary note (at most ``max_changed``
        changed lines). A moved or renumbered element shows up as an
        explicit -/+ pair instead of silently changing meaning. None
        when nothing changed or the change is large; the caller then
        falls back to :meth:`diff_note` and the full map."""
        if previous is None:
            return None
        old = previous.render(budget=100_000).splitlines()
        new = self.render(budget=100_000).splitlines()
        if old == new:
            return None
        matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
        out: list[str] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            out.extend(f"- {line}" for line in old[i1:i2])
            out.extend(f"+ {line}" for line in new[j1:j2])
        if not out or len(out) > max_changed:
            return None
        return "\n".join(out)

    def render(self, budget: Optional[int] = None) -> str:
        limit = self.budget if budget is None else budget
        lines: list[str] = []
        used = 0
        for i, el in enumerate(self.elements):
            line = _line_for(el)
            if used + len(line) + 1 > limit:
                remaining = len(self.elements) - i
                lines.append(
                    f"… truncated: {remaining} more element(s) not shown "
                    f"(map budget {limit} chars)"
                )
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    def render_focused(self, goal: str, budget: Optional[int] = None) -> str:
        """Render the map for a *goal*, spending the char budget on the
        elements most likely to matter (v1.1).

        Plain :meth:`render` walks the page in order and truncates the
        tail — on a big page the checkout button can sit past the cut
        while the model pays tokens for a nav menu. Here, when the full
        map fits the budget this returns exactly what render() returns;
        when it does not, elements are scored by overlap between the
        goal's words and their name/tag/type/href, the best-scoring ones
        are kept, and the kept lines still print in page order under
        their original numbers (an action's target number never
        changes meaning)."""
        limit = self.budget if budget is None else budget
        full = self.render(budget=10 ** 9)
        if len(full) <= limit:
            return self.render(budget=limit)
        words = {
            w
            for w in re.findall(r"[a-z0-9]+", (goal or "").lower())
            if len(w) >= 3
        }

        line_for = _line_for

        def score(el: Element) -> int:
            hay = f"{el.name} {el.tag} {el.type or ''} {el.href or ''}".lower()
            return sum(1 for w in words if w in hay)

        ranked = sorted(self.elements, key=lambda el: (-score(el), el.number))
        kept: list[Element] = []
        used = 0
        reserve = 90  # room for the omission note
        for el in ranked:
            cost = len(line_for(el)) + 1
            if used + cost + reserve > limit and kept:
                continue
            kept.append(el)
            used += cost
        kept.sort(key=lambda el: el.number)
        lines = [line_for(el) for el in kept]
        omitted = len(self.elements) - len(kept)
        if omitted:
            lines.append(
                f"… focused: {omitted} element(s) not shown "
                f"(goal-relevant kept; map budget {limit} chars)"
            )
        return "\n".join(lines)

    def signature(self) -> str:
        # Map + full-source hash: a page change outside the interactive
        # elements (a list item appearing, a banner swapping) still counts
        # as progress for the Runner's stuck detection.
        material = self.render(budget=100_000) + "|" + self.source_hash
        return hashlib.sha256(material.encode()).hexdigest()[:16]

    def __len__(self) -> int:
        return len(self.elements)


# ---------------------------------------------------------------------------
# Structured extraction over raw HTML (v0.3)
# ---------------------------------------------------------------------------
# The `extract` action's `list` and `table` modes. ChromiumDriver computes
# the same shapes in-page; these stdlib parsers serve FakeDriver and are
# unit-tested directly. Shapes are contractual:
#   list  -> [{"text": str, "href": str|None}, ...]   (every <a>, in order)
#   table -> [{header: cell, ...}, ...]              (first <table>; when
#           the first row has no <th>, keys are col1..colN)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, Any]] = []
        self._current: Optional[dict[str, Any]] = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs_list: list) -> None:
        if tag == "a" and self._current is None:
            attrs = {k: (v if v is not None else "") for k, v in attrs_list}
            self._current = {"text": "", "href": attrs.get("href")}
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current is not None:
            self._current["text"] = " ".join("".join(self._parts).split())
            self.links.append(self._current)
            self._current = None


def extract_links(html: str) -> list[dict[str, Any]]:
    parser = _LinkParser()
    parser.feed(html or "")
    parser.close()
    return parser.links


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, bool]]] = []  # (cell text, is_header)
        self._depth = 0
        self._done = False
        self._row: Optional[list[tuple[str, bool]]] = None
        self._cell: Optional[list[str]] = None
        self._cell_header = False

    def handle_starttag(self, tag: str, attrs_list: list) -> None:
        if self._done:
            return
        if tag == "table":
            self._depth += 1
        elif self._depth == 1:
            if tag == "tr":
                self._row = []
            elif tag in ("td", "th") and self._row is not None:
                self._cell = []
                self._cell_header = tag == "th"

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._done:
            return
        if tag == "table":
            self._depth -= 1
            if self._depth == 0:
                self._done = True
        elif self._depth == 1:
            if tag in ("td", "th") and self._cell is not None and self._row is not None:
                text = " ".join("".join(self._cell).split())
                self._row.append((text, self._cell_header))
                self._cell = None
            elif tag == "tr" and self._row is not None:
                if self._row:
                    self.rows.append(self._row)
                self._row = None


def extract_table(html: str) -> list[dict[str, Any]]:
    parser = _TableParser()
    parser.feed(html or "")
    parser.close()
    if not parser.rows:
        return []
    first = parser.rows[0]
    if any(is_header for _, is_header in first):
        headers = [text for text, _ in first]
        data_rows = parser.rows[1:]
    else:
        headers = [f"col{i + 1}" for i in range(len(first))]
        data_rows = parser.rows
    out: list[dict[str, Any]] = []
    for row in data_rows:
        record: dict[str, Any] = {}
        for i, header in enumerate(headers):
            record[header] = row[i][0] if i < len(row) else ""
        out.append(record)
    return out
