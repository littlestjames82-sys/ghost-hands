"""Eyes: turn an HTML snapshot into a numbered ElementMap.

Perception is deliberately cheap and text-only — no screenshots, no vision
model. The map is what a decider reads, what the governor reasons about, and
what the trail records, so one perception serves all three.
"""

from __future__ import annotations

import hashlib
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
    # v0.5: an opaque body-native target reference (the Android bridge's
    # child-index path, e.g. "0/2/1"). Bodies that resolve targets by
    # their own addressing fill this in; web bodies leave it None. It is
    # recorded in descriptors so a trail shows exactly what was acted on.
    body_ref: Optional[str] = None

    def key(self) -> tuple:
        """Identity used for self-healing matches: tag + role + name."""
        return (self.tag, self.role, self.name)

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
        )


class _MapParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[Element] = []
        self.title: str = ""
        # stack entries: {tag, attrs, text:[], element|None, form_action|None}
        self._stack: list[dict[str, Any]] = []
        self._in_title = False
        self._title_parts: list[str] = []
        # v0.3: <label for="id"> associations, resolved after the parse.
        self.labels_by_for: dict[str, str] = {}

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

    def _make_element(self, tag: str, attrs: dict[str, str]) -> Element:
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
            value=attrs.get("value"),
            form_action=self._current_form_action(),
            attrs=dict(attrs),
        )
        self.elements.append(el)
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

    # -- HTMLParser hooks -------------------------------------------------
    def handle_starttag(self, tag: str, attrs_list: list) -> None:
        attrs = {k: (v if v is not None else "") for k, v in attrs_list}
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
            self._finish_name(el, (attrs.get("value") or ""))

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
                for entry in popped:
                    el = entry["element"]
                    if el is not None:
                        text = " ".join("".join(entry["text"]).split())
                        self._finish_name(el, text)
                    self._apply_label(entry)
                break


@dataclass
class ElementMap:
    elements: list[Element] = field(default_factory=list)
    url: str = ""
    title: str = ""
    budget: int = 4000
    source_hash: str = ""

    @classmethod
    def from_html(cls, html: str, url: str = "", budget: int = 4000) -> "ElementMap":
        parser = _MapParser()
        parser.feed(html or "")
        parser.close()
        # elements left open at EOF still need names
        for entry in parser._stack:
            el = entry["element"]
            if el is not None and not el.name:
                text = " ".join("".join(entry["text"]).split())
                _MapParser._finish_name(el, text)
            parser._apply_label(entry)
        # resolve <label for="id"> associations
        for el in parser.elements:
            if el.id and el.label is None and el.id in parser.labels_by_for:
                el.label = parser.labels_by_for[el.id]
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
        source_hash = hashlib.sha256(repr(items).encode()).hexdigest()[:16]
        return cls(elements=els, url=url, budget=budget, source_hash=source_hash)

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

    def render(self, budget: Optional[int] = None) -> str:
        limit = self.budget if budget is None else budget
        lines: list[str] = []
        used = 0
        for i, el in enumerate(self.elements):
            line = f'[{el.number}] <{el.tag}> "{el.name}"'
            if el.type:
                line += f" type={el.type}"
            if el.href:
                line += f" href={el.href}"
            if el.frame:
                line += f" frame={el.frame}"
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
