"""Drivers (bodies): the things that actually move.

Two bodies ship with Ghost Hands:

- ``FakeDriver`` — an in-memory mini-web of HTML pages. Deterministic, no
  browser, used by the demo, the bench, the tests, and MCP ``hands_run``.
- ``ChromiumDriver`` — a real Chromium/Chrome driven by *our own* stdlib
  Chrome DevTools Protocol client (see the CDP section below). Ghost Hands
  does not wrap Playwright, Selenium, or any other automation library; the
  automation layer is 100% ours. We drive Chromium the browser through our
  own protocol client — we did not write a browser engine, and don't claim
  to have.

v0.2 additions: tabs, session save/load (cookies + localStorage),
screenshots saved to disk, env-proxy support (with a local
credential-injecting relay for authenticated proxies — Chrome cannot take
proxy credentials in ``--proxy-server``), and stale-target detection in
both drivers: acting on an element whose descriptor no longer matches the
page raises a "target mismatch" error, which the Runner heals by
re-finding the element by descriptor and retrying once.

v0.3 additions (ChromiumDriver unless noted): two eyes — ``eyes="dom"``
(the default; a JS deep-walk that pierces *open shadow roots* and
same-origin *iframes*, elements tagged with their frame) and ``eyes="ax"``
(the CDP Accessibility tree: role + name + states, resolved back to nodes
by backendNodeId). JS-dialog handling with a driver policy (default
dismiss; every dialog + decision reported through the event sink), browser
downloads (setDownloadBehavior into a download dir; completion awaited,
reported, and sunk as a ``download`` event), file uploads
(DOM.setFileInputFiles), network capture (Network domain → the trail as
``net`` events, capped, and `extract` mode ``network``), one-action
``fill_form`` by field descriptor, structured extract modes (list/table),
richer input (hover / double-click / right-click / drag / key chords),
``pdf`` (Page.printToPDF), viewport emulation presets, conditioned
``wait`` (text / element / URL), and ``click_at`` raw coordinates.
FakeDriver implements the same new action vocabulary in its state model
so scripts and tests exercise identical semantics browser-free.

Drivers report asynchronous happenings through ``event_sink`` — a
callable ``(event_type, fields)`` the Runner/MCP wire into the trail.
Driver protocol: open(url), snapshot_html() -> str, current_url(),
act(action, element) -> result str, close(). ChromiumDriver also offers
perceive() -> ElementMap, which the Runner prefers when present.
"""

from __future__ import annotations

import html as _html
import json
import re
import time
from pathlib import Path as _Path
from typing import Callable, Optional, Protocol
from urllib.parse import urljoin, urlparse

from .actions import Action
from .errors import HandsError
from .eyes import Element, ElementMap, extract_links, extract_table


class Driver(Protocol):
    def open(self, url: str) -> None: ...
    def snapshot_html(self) -> str: ...
    def current_url(self) -> str: ...
    def act(self, action: Action, element: Optional[Element]) -> str: ...
    def close(self) -> None: ...


def _format_fill_results(results: list[dict], submitted: bool, note: str = "") -> str:
    """One-line per-field account of a fill_form action. Failures are
    named in the result — never silent."""
    filled = sum(1 for r in results if r["ok"])
    parts = [f"filled {filled}/{len(results)} fields"]
    for r in results:
        if r["ok"]:
            parts.append(f"{r['field']!r} -> [{r['target']}] ok")
        else:
            parts.append(f"{r['field']!r} FAILED: {r.get('error', 'unknown error')}")
    if submitted:
        parts.append("form submitted")
    if note:
        parts.append(note)
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# FakeDriver — an in-memory mini-web
# ---------------------------------------------------------------------------

_EMPTY_PAGE = "<html><head><title>Blank</title></head><body></body></html>"


class FakeDriver:
    """A deterministic fake web: pages are HTML strings keyed by URL.

    State model: typing stores field values; clicking a link navigates;
    clicking a button with ``data-nav`` navigates; a button with
    ``data-append-to`` + ``data-from-field`` appends an ``<li>`` built from
    the field's value into the element whose id is ``data-append-to``;
    submitting a form records it in ``self.submissions`` and navigates to
    the form's action when that page exists.

    ``mutate_hook`` (v0.2, test support): a one-shot callable invoked with
    the driver right *after* a snapshot is taken — i.e. in the gap between
    perception and action, exactly where real pages mutate under an agent.
    The snapshot that triggered it still returns the pre-mutation HTML.
    """

    def __init__(self, pages: dict[str, str], start_url: Optional[str] = None) -> None:
        if not pages:
            raise HandsError("FakeDriver needs at least one page")
        self.pages = dict(pages)
        self._overrides: dict[str, str] = {}
        self.fields: dict[str, str] = {}
        self.submissions: list[dict] = []
        self.mutate_hook: Optional[Callable[["FakeDriver"], None]] = None
        self._url = start_url or next(iter(self.pages))
        if self._url not in self.pages:
            self.pages[self._url] = _EMPTY_PAGE
        # v0.3: synthetic network log (page loads), the event sink the
        # Runner/MCP wire into the trail, and misc action state.
        self.network_log: list[dict] = [
            {"method": "GET", "url": self._url, "status": 200, "type": "Document"}
        ]
        self.event_sink: Optional[Callable[[str, dict], None]] = None
        self.viewport: Optional[str] = None
        self.interactions: list[str] = []

    # -- protocol ---------------------------------------------------------
    def open(self, url: str) -> None:
        self._navigate(url)

    def snapshot_html(self) -> str:
        html = self._overrides.get(self._url, self.pages.get(self._url, _EMPTY_PAGE))
        if self.mutate_hook is not None:
            hook, self.mutate_hook = self.mutate_hook, None
            hook(self)
        return html

    def current_url(self) -> str:
        return self._url

    def close(self) -> None:
        pass

    # -- internals --------------------------------------------------------
    def _navigate(self, url: str) -> str:
        dest = urljoin(self._url, url) if self._url else url
        self._url = dest
        if dest not in self.pages and dest not in self._overrides:
            self.pages[dest] = _EMPTY_PAGE
        self.network_log.append(
            {"method": "GET", "url": dest, "status": 200, "type": "Document"}
        )
        return dest

    def _sink(self, event_type: str, **fields) -> None:
        if self.event_sink is not None:
            self.event_sink(event_type, fields)

    def _current_map(self) -> ElementMap:
        raw = self._overrides.get(self._url, self.pages.get(self._url, _EMPTY_PAGE))
        return ElementMap.from_html(raw, url=self._url)

    @staticmethod
    def _field_matches(el: Element, key: str) -> bool:
        needle = key.strip().lower()
        if not needle:
            return False
        candidates = [
            el.label or "",
            el.attr_name or "",
            el.placeholder or "",
            el.attrs.get("aria-label", ""),
            el.id or "",
            el.name or "",
        ]
        return any(
            c and (c.strip().lower() == needle or needle in c.strip().lower())
            for c in candidates
        )

    def _fill_form(self, action: Action) -> str:
        results: list[dict] = []
        filled_form_action: Optional[str] = None
        for key, raw_value in (action.fields or {}).items():
            value = str(raw_value)
            target_el = None
            for el in self._current_map().elements:
                if el.tag in ("input", "textarea", "select") and self._field_matches(el, key):
                    target_el = el
                    break
            if target_el is None:
                results.append({"field": key, "ok": False, "error": "no matching field"})
                continue
            self._store_field(target_el, value)
            if filled_form_action is None:
                filled_form_action = target_el.form_action
            results.append(
                {"field": key, "ok": True, "target": target_el.number, "value": value}
            )
        submitted = False
        note = ""
        if action.submit:
            submit_el = next(
                (
                    el
                    for el in self._current_map().elements
                    if el.type == "submit"
                    or (el.tag == "button" and el.form_action is not None)
                ),
                None,
            )
            if submit_el is not None:
                self._click(submit_el)
                submitted = True
            else:
                note = "submit requested but no submit control found"
        return _format_fill_results(results, submitted, note)

    def _wait_condition(self, action: Action) -> str:
        """Conditioned wait against the fake web: poll the (static) page
        until the condition holds or the timeout expires."""
        timeout = float(action.seconds if action.seconds is not None else 10.0)
        deadline = time.time() + min(timeout, 30.0)
        what = (
            f"text {action.text!r}"
            if action.text
            else f"element [{action.target}]"
            if action.target
            else f"URL containing {action.url!r}"
        )
        while True:
            if action.text and action.text in self._page_text():
                return f"wait satisfied: {what} (after 0.0s)"
            if action.target and self._current_map().get(action.target) is not None:
                return f"wait satisfied: {what} (after 0.0s)"
            if action.url and action.url in self._url:
                return f"wait satisfied: {what} (after 0.0s)"
            if time.time() >= deadline:
                raise HandsError(
                    f"wait timed out after {timeout}s waiting for {what}"
                )
            time.sleep(0.05)

    def _verify_target(self, element: Element) -> None:
        """Stale-target detection: the element the caller *means* (by the
        descriptor recorded at perception) must still be the element at
        that number on the current page. Raises a "target mismatch" /
        "target missing" HandsError the Runner can heal from."""
        # Read the raw current page (not snapshot_html()) so a pending
        # mutation hook cannot make this check see a stale page.
        raw = self._overrides.get(self._url, self.pages.get(self._url, _EMPTY_PAGE))
        fresh = ElementMap.from_html(raw, url=self._url)
        current = fresh.get(element.number)
        if current is None:
            raise HandsError(
                f"target missing: [{element.number}] <{element.tag}> "
                f'"{element.name}" is no longer on the page'
            )
        if current.key() != element.key():
            raise HandsError(
                f"target mismatch: [{element.number}] was <{element.tag}> "
                f'"{element.name}", now <{current.tag}> "{current.name}"'
            )

    def _store_field(self, element: Element, value: str) -> None:
        for key in (element.id, element.attr_name, str(element.number)):
            if key:
                self.fields[key] = value

    def _page_text(self) -> str:
        raw = self.snapshot_html()
        raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
        text = re.sub(r"(?s)<[^>]+>", " ", raw)
        return " ".join(_html.unescape(text).split())[:500]

    # -- actions ----------------------------------------------------------
    def act(self, action: Action, element: Optional[Element]) -> str:
        kind = action.kind
        if kind == "navigate":
            dest = self._navigate(action.url or "")
            return f"navigated to {dest}"
        if kind == "wait":
            if action.text or action.target or action.url:
                return self._wait_condition(action)
            time.sleep(min(float(action.seconds or 0), 5.0))
            return f"waited {action.seconds}s"
        if kind == "scroll":
            return f"scrolled {action.direction or 'down'} {action.amount or 500}"
        if kind == "screenshot":
            return (
                "screenshot captured (fake driver: no image; "
                f"would save to {action.path or './ghost-hands-shot.png'})"
            )
        if kind == "pdf":
            return (
                "pdf captured (fake driver: no file; "
                f"would save to {action.path or './ghost-hands-page.pdf'})"
            )
        if kind == "set_viewport":
            preset = action.value or f"{action.width}x{action.height}"
            self.viewport = preset
            return f"viewport set to {preset} (fake driver)"
        if kind == "fill_form":
            return self._fill_form(action)
        if kind == "download":
            url = action.url
            if url is None and element is not None:
                url = element.href
            if not url:
                raise HandsError("download needs a target link or a URL")
            dest = urljoin(self._url, url)
            content = self.pages.get(dest, self._overrides.get(dest, ""))
            name = dest.rstrip("/").rsplit("/", 1)[-1] or "download"
            size = len(content.encode("utf-8"))
            self._sink(
                "download", filename=name, bytes=size, url=dest, state="completed"
            )
            return f"downloaded {name} ({size} bytes) (fake driver: nothing written)"
        if kind == "click_at":
            self.interactions.append(f"click_at {action.x},{action.y}")
            return f"clicked at ({action.x}, {action.y}) — raw coordinates (fake driver)"
        if kind in ("click", "type", "select", "hover", "double_click", "right_click", "set_file") or (
            kind == "extract" and element is not None
        ) or (kind == "drag"):
            if element is not None:
                self._verify_target(element)
        if kind == "extract":
            return self._extract(action, element)
        if kind == "press":
            self.interactions.append(f"press {action.key}")
            return f"pressed {action.key}"

        if element is None:
            raise HandsError(f"{kind} requires a target element")
        if kind == "type":
            self._store_field(element, action.text or "")
            return f"typed into [{element.number}] \"{element.name}\""
        if kind == "select":
            self._store_field(element, action.value or "")
            return f"selected \"{action.value}\" in [{element.number}]"
        if kind == "click":
            return self._click(element)
        if kind in ("hover", "double_click", "right_click"):
            self.interactions.append(f"{kind} [{element.number}]")
            verb = {"hover": "hovered", "double_click": "double-clicked", "right_click": "right-clicked"}[kind]
            return f"{verb} [{element.number}] \"{element.name}\" (fake driver)"
        if kind == "drag":
            dest_el = self._current_map().get(action.to_target)
            if dest_el is None:
                raise HandsError(
                    f"drag destination [{action.to_target}] is not on the page"
                )
            self.interactions.append(f"drag [{element.number}]->[{action.to_target}]")
            return (
                f"dragged [{element.number}] \"{element.name}\" to "
                f"[{dest_el.number}] \"{dest_el.name}\" (fake driver)"
            )
        if kind == "set_file":
            path = _Path(action.path or "")
            if not path.is_file():
                raise HandsError(
                    f"set_file path is not a file: {action.path!r} "
                    "(missing, or a directory — directories are refused)"
                )
            note = f"file:{path.name} ({path.stat().st_size} bytes)"
            self._store_field(element, note)
            return f"set file on [{element.number}] \"{element.name}\" to {note}"
        raise HandsError(f"FakeDriver cannot perform {kind!r}")

    def _extract(self, action: Action, element: Optional[Element]) -> str:
        mode = action.mode or "text"
        raw = self._overrides.get(self._url, self.pages.get(self._url, _EMPTY_PAGE))
        if mode == "network":
            return json.dumps(self.network_log)
        if mode == "list":
            return json.dumps(extract_links(raw))
        if mode == "table":
            return json.dumps(extract_table(raw))
        # text mode
        if element is None:
            return self._page_text()
        self._verify_target(element)
        stored = self.fields.get(element.id or "", None)
        if stored is None:
            stored = self.fields.get(str(element.number))
        if stored is not None:
            return stored
        return element.value if element.value is not None else element.name

    def _click(self, element: Element) -> str:
        attrs = element.attrs
        if attrs.get("data-nav"):
            dest = self._navigate(attrs["data-nav"])
            return f"clicked [{element.number}] -> navigated to {dest}"
        if "data-append-to" in attrs:
            container = attrs["data-append-to"]
            field_key = attrs.get("data-from-field", "")
            value = self.fields.get(field_key, "")
            if value:
                self._append_item(container, value)
            return f"clicked [{element.number}] -> appended \"{value}\" to #{container}"
        if element.tag == "a" and element.href:
            dest = self._navigate(element.href)
            return f"clicked [{element.number}] -> navigated to {dest}"
        if element.type == "submit" or element.form_action:
            target = element.form_action or self._url
            self.submissions.append(
                {
                    "url": self._url,
                    "action": element.form_action,
                    "fields": dict(self.fields),
                }
            )
            if element.form_action:
                dest = urljoin(self._url, element.form_action)
                if dest in self.pages or dest in self._overrides:
                    self._navigate(element.form_action)
            return f"submitted form (action={target})"
        return f"clicked [{element.number}] \"{element.name}\" (no effect)"

    def _append_item(self, container_id: str, value: str) -> None:
        current = self.snapshot_html()
        pattern = re.compile(
            r'(<[a-zA-Z][^>]*\bid=["\']' + re.escape(container_id) + r'["\'][^>]*>)'
        )
        item = f"<li>{_html.escape(value)}</li>"
        new, n = pattern.subn(lambda m: m.group(1) + item, current, count=1)
        if n:
            self._overrides[self._url] = new


# ---------------------------------------------------------------------------
# Demo mini-web (used by `ghost-hands demo` and the MCP default session)
# ---------------------------------------------------------------------------

DEMO_HOME = "https://demo.local/"
DEMO_PAGES: dict[str, str] = {
    DEMO_HOME: """<!doctype html><html><head><title>Ghost Demo Home</title></head>
<body>
<h1>Ghost Demo Web</h1>
<p>A tiny fake web that ships with Ghost Hands.</p>
<input id="q" name="q" type="text" placeholder="Search the demo web">
<button data-nav="https://demo.local/results">Search</button>
<a href="https://demo.local/todo">Open the todo list</a>
</body></html>""",
    "https://demo.local/results": """<!doctype html><html><head><title>Results</title></head>
<body>
<h1>Results for "ghost"</h1>
<a href="https://demo.local/article">Ghost Hands — our own hands</a>
<a href="https://demo.local/">Back home</a>
</body></html>""",
    "https://demo.local/article": """<!doctype html><html><head><title>Ghost Hands</title></head>
<body>
<h1>Ghost Hands</h1>
<p>Playwright drives. Stagehand thinks. Ghost Hands answers for every move.</p>
<a href="https://demo.local/todo">Try the todo list</a>
</body></html>""",
    "https://demo.local/todo": """<!doctype html><html><head><title>Demo Todo</title></head>
<body>
<h1>Demo Todo</h1>
<input id="new-item" name="new-item" type="text" placeholder="New item">
<button data-append-to="items" data-from-field="new-item">Add item</button>
<ul id="items"><li>Existing item</li></ul>
</body></html>""",
}


def demo_steps() -> list[dict]:
    """Scripted steps for `ghost-hands demo` (element numbers per DEMO_PAGES)."""
    return [
        {"kind": "type", "target": 1, "text": "ghost"},
        {"kind": "click", "target": 2},
        {"kind": "click", "target": 1},
        {"kind": "extract"},
        {"kind": "navigate", "url": "https://demo.local/todo"},
        {"kind": "type", "target": 1, "text": "Ship Ghost Hands"},
        {"kind": "click", "target": 2},
        {"kind": "extract"},
        {"kind": "done", "summary": "demo complete: searched, opened a result, added a todo"},
    ]


# ---------------------------------------------------------------------------
# Our own CDP client (stdlib only) — pipe transport
# ---------------------------------------------------------------------------
# Chrome is launched with --remote-debugging-pipe: CDP messages are
# NUL-delimited JSON written to the child's fd 4 and read from its fd 3.
# This module implements framing, id matching, and a small session layer.
# No WebSocket, no third-party code.

import base64
import json
import os
import queue as queue_module
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import unquote

from .eyes import INTERACTIVE_SELECTOR


def encode_cdp_message(message: dict) -> bytes:
    """Frame one CDP message for the pipe transport."""
    return json.dumps(message).encode("utf-8") + b"\x00"


class CDPMessageBuffer:
    """Incremental parser for NUL-delimited CDP frames (unit-testable)."""

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, chunk: bytes) -> list[dict]:
        self._buf += chunk
        out: list[dict] = []
        while b"\x00" in self._buf:
            raw, self._buf = self._buf.split(b"\x00", 1)
            raw = raw.strip()
            if raw:
                out.append(json.loads(raw))
        return out


class CDPClient:
    """Minimal CDP client over the pipe transport.

    A reader thread demultiplexes responses (matched by id to waiting
    callers) from events (buffered for inspection). Events are also handed
    to ``on_event`` — but on a separate dispatcher thread, never the
    reader: handlers legitimately *send* CDP commands of their own (a JS
    dialog must be answered with Page.handleJavaScriptDialog), and a
    handler running on the reader thread would deadlock waiting for a
    response only the reader can deliver.
    """

    def __init__(self, proc: subprocess.Popen, write_fd, read_fd) -> None:
        self._proc = proc
        self._w = write_fd
        self._r = read_fd
        self._next_id = 0
        self._lock = threading.Lock()
        self._pending: dict[int, tuple[threading.Event, dict]] = {}
        self._pending_lock = threading.Lock()
        self.events: list[dict] = []
        self._closed = False
        self.on_event: Optional[Callable[[dict], None]] = None
        self._event_queue: "queue_module.Queue[dict]" = queue_module.Queue()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._dispatcher = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatcher.start()

    def _dispatch_loop(self) -> None:
        while not self._closed:
            try:
                msg = self._event_queue.get(timeout=0.25)
            except queue_module.Empty:
                continue
            handler = self.on_event
            if handler is not None:
                try:
                    handler(msg)
                except Exception:
                    pass  # a broken handler must never kill the transport

    def _read_loop(self) -> None:
        buf = CDPMessageBuffer()
        while not self._closed:
            try:
                chunk = self._r.read(65536)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            for msg in buf.feed(chunk):
                mid = msg.get("id")
                if mid is not None:
                    with self._pending_lock:
                        slot = self._pending.pop(mid, None)
                    if slot is not None:
                        event, holder = slot
                        holder.update(msg)
                        event.set()
                        continue
                self.events.append(msg)
                if self.on_event is not None:
                    self._event_queue.put(msg)

    def send(
        self,
        method: str,
        params: Optional[dict] = None,
        session_id: Optional[str] = None,
        timeout: float = 15.0,
    ) -> dict:
        with self._lock:
            self._next_id += 1
            mid = self._next_id
        message: dict = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            message["sessionId"] = session_id
        event = threading.Event()
        holder: dict = {}
        with self._pending_lock:
            self._pending[mid] = (event, holder)
        try:
            self._w.write(encode_cdp_message(message))
        except (OSError, ValueError) as exc:
            raise HandsError(f"CDP write failed for {method}: {exc}") from exc
        if not event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(mid, None)
            raise HandsError(f"CDP timeout waiting for {method}")
        if "error" in holder:
            err = holder["error"]
            raise HandsError(f"CDP {method} failed: {err.get('message', err)}")
        return holder.get("result", {})

    def close(self) -> None:
        self._closed = True
        for fh in (self._w, self._r):
            try:
                fh.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Authenticated-proxy support (v0.2)
# ---------------------------------------------------------------------------
# Chrome cannot take credentials in --proxy-server (it answers
# ERR_NO_SUPPORTED_PROXIES), and an unauthenticated CONNECT to an
# authenticated proxy fails the tunnel. So when the environment's proxy
# URL carries userinfo, ChromiumDriver runs this tiny stdlib relay on
# 127.0.0.1 instead: Chrome talks to the relay; the relay injects
# Proxy-Authorization upstream. Credentials live only in process memory,
# come from the environment, and are never logged or written anywhere.
# Loopback targets are always connected directly, never proxied.


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip("[]").lower()
    return h == "localhost" or h == "::1" or h.startswith("127.")


def _relay_pipe(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _read_until(sock_file, marker: bytes, limit: int = 65536) -> bytes:
    buf = b""
    while marker not in buf and len(buf) < limit:
        chunk = sock_file.read(1)
        if not chunk:
            break
        buf += chunk
    return buf


class _AuthProxyRelay:
    """A one-process HTTP proxy relay that adds Basic proxy auth."""

    def __init__(self, upstream_url: str) -> None:
        u = urlparse(upstream_url)
        self._scheme = u.scheme or "http"
        self._host = u.hostname or ""
        self._port = u.port or (443 if self._scheme == "https" else 80)
        self._auth: Optional[str] = None
        if u.username:
            raw = f"{unquote(u.username)}:{unquote(u.password or '')}"
            self._auth = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        self._srv: Optional[socket.socket] = None
        self.port = 0

    # -- lifecycle ------------------------------------------------------
    def start(self) -> str:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(64)
        self._srv = srv
        self.port = int(srv.getsockname()[1])
        threading.Thread(target=self._serve, daemon=True).start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        if self._srv is not None:
            try:
                self._srv.close()
            except OSError:
                pass
            self._srv = None

    # -- plumbing ---------------------------------------------------------
    def _serve(self) -> None:
        while self._srv is not None:
            try:
                client, _ = self._srv.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _connect_upstream(self) -> socket.socket:
        up = socket.create_connection((self._host, self._port), timeout=20)
        if self._scheme == "https":
            ctx = ssl.create_default_context()
            up = ctx.wrap_socket(up, server_hostname=self._host)
        return up

    def _handle(self, client: socket.socket) -> None:
        try:
            self._handle_inner(client)
        except Exception:
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass

    def _handle_inner(self, client: socket.socket) -> None:
        stream = client.makefile("rb", buffering=0)
        request_line = stream.readline(65536).decode("latin1").strip()
        if not request_line:
            return
        headers: list[tuple[str, str]] = []
        while True:
            line = stream.readline(65536).decode("latin1")
            if line in ("\r\n", "\n", ""):
                break
            if ":" in line:
                k, v = line.split(":", 1)
                headers.append((k.strip(), v.strip()))
        parts = request_line.split(" ")
        if len(parts) < 2:
            return
        method, target = parts[0].upper(), parts[1]

        if method == "CONNECT":
            hostport = target
            host = hostport.rsplit(":", 1)[0] if ":" in hostport else hostport
            if _is_loopback_host(host):
                port = int(hostport.rsplit(":", 1)[1]) if ":" in hostport else 443
                up = socket.create_connection((host.strip("[]"), port), timeout=20)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                up = self._connect_upstream()
                req = f"CONNECT {hostport} HTTP/1.1\r\nHost: {hostport}\r\n"
                if self._auth:
                    req += f"Proxy-Authorization: Basic {self._auth}\r\n"
                req += "Proxy-Connection: Keep-Alive\r\n\r\n"
                up.sendall(req.encode("latin1"))
                up_stream = up.makefile("rb", buffering=0)
                response = _read_until(up_stream, b"\r\n\r\n")
                client.sendall(response)
                status = response.split(b"\r\n", 1)[0]
                if b" 200" not in status:
                    up.close()
                    return
            t = threading.Thread(target=_relay_pipe, args=(client, up), daemon=True)
            t.start()
            _relay_pipe(up, client)
            t.join(timeout=5)
            up.close()
            return

        # Plain HTTP proxy request (absolute URI) or direct origin request.
        u = urlparse(target)
        if u.hostname and _is_loopback_host(u.hostname):
            port = u.port or 80
            up = socket.create_connection((u.hostname, port), timeout=20)
            path = u.path or "/"
            if u.query:
                path += "?" + u.query
            out = [f"{method} {path} HTTP/1.1"]
            out += [
                f"{k}: {v}"
                for k, v in headers
                if k.lower() not in ("proxy-authorization", "proxy-connection")
            ]
            up.sendall(("\r\n".join(out) + "\r\n\r\n").encode("latin1"))
        else:
            up = self._connect_upstream()
            out = [request_line]
            out += [
                f"{k}: {v}"
                for k, v in headers
                if k.lower() not in ("proxy-authorization", "proxy-connection", "connection")
            ]
            if self._auth:
                out.append(f"Proxy-Authorization: Basic {self._auth}")
            out.append("Proxy-Connection: close")
            out.append("Connection: close")
            up.sendall(("\r\n".join(out) + "\r\n\r\n").encode("latin1"))
        t = threading.Thread(target=_relay_pipe, args=(client, up), daemon=True)
        t.start()
        _relay_pipe(up, client)
        t.join(timeout=5)
        up.close()


# Process shim for launching Chrome with the CDP pipe on fds 3/4.
# subprocess' preexec_fn + dup2 is not safe when the parent process has
# threads blocked in syscalls (verified: an in-process accept() thread
# makes Popen fail with EBADF), so the fd mapping happens in this tiny
# fresh interpreter instead, which then execs Chrome.
_LAUNCH_SHIM = (
    "import os,sys;"
    "r=int(sys.argv[1]);w=int(sys.argv[2]);"
    "os.dup2(r,3);os.dup2(w,4);"
    "os.execv(sys.argv[3],sys.argv[3:])"
)


_CHROME_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "chrome",
)


def find_chrome(explicit: Optional[str] = None) -> Optional[str]:
    """Locate a Chromium/Chrome binary: explicit path, then
    $GHOST_HANDS_CHROME, then the shop's Chrome for Testing install, then
    PATH lookups. Returns None when no binary exists."""
    if explicit:
        return explicit if Path(explicit).is_file() else None
    env = os.environ.get("GHOST_HANDS_CHROME")
    if env and Path(env).is_file():
        return env
    local = Path.home() / "workspace" / "tools" / "chrome" / "chrome-linux64" / "chrome"
    if local.is_file():
        return str(local)
    for name in _CHROME_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    return None


_KEY_MAP = {
    "Enter": ("Enter", "Enter"),
    "Tab": ("Tab", "Tab"),
    "Escape": ("Escape", "Escape"),
    "Backspace": ("Backspace", "Backspace"),
    "Delete": ("Delete", "Delete"),
    "ArrowUp": ("ArrowUp", "ArrowUp"),
    "ArrowDown": ("ArrowDown", "ArrowDown"),
    "ArrowLeft": ("ArrowLeft", "ArrowLeft"),
    "ArrowRight": ("ArrowRight", "ArrowRight"),
    " ": (" ", "Space"),
    "Space": (" ", "Space"),
    "Home": ("Home", "Home"),
    "End": ("End", "End"),
    "PageUp": ("PageUp", "PageUp"),
    "PageDown": ("PageDown", "PageDown"),
}

_HANDLES_JS = (
    "(() => {"
    f"const els = document.querySelectorAll({json.dumps(INTERACTIVE_SELECTOR)});"
    "const out = [];"
    "els.forEach((el, i) => {"
    "  const r = el.getBoundingClientRect();"
    "  out.push({i: i, x: r.x + r.width / 2, y: r.y + r.height / 2,"
    "            w: r.width, h: r.height});"
    "});"
    "return out;})()"
)


# ---------------------------------------------------------------------------
# v0.3 in-page library: one composed-tree walk for perception AND action
# resolution, so the numbers a decider sees are the numbers the driver acts
# on. The walk pierces open shadow roots and same-origin iframes (elements
# carry their frame's name); cross-origin frames are skipped — their
# documents are not readable, and pretending otherwise would be a lie.
# Hidden elements (display:none / visibility:hidden / no box / type=hidden)
# are not numbered: a control nobody can see is not a control.
# ---------------------------------------------------------------------------

_GH_PRELUDE_JS = r"""
function __ghVisible(el) {
  if (el.tagName === 'INPUT' && (el.getAttribute('type') || 'text').toLowerCase() === 'hidden') return false;
  if (!el.getClientRects || el.getClientRects().length === 0) return false;
  const view = el.ownerDocument ? el.ownerDocument.defaultView : null;
  if (view) {
    const cs = view.getComputedStyle(el);
    if (cs && (cs.display === 'none' || cs.visibility === 'hidden')) return false;
  }
  return true;
}
function __ghText(el) { return ((el.innerText || el.textContent || '') + '').replace(/\s+/g, ' ').trim(); }
function __ghName(el) {
  const al = el.getAttribute('aria-label');
  if (al && al.trim()) return al.trim();
  const t = __ghText(el);
  if (t) return t;
  const ph = el.getAttribute('placeholder');
  if (ph) return ph;
  if (el.tagName === 'INPUT' && el.value) return el.value;
  const nm = el.getAttribute('name');
  if (nm) return nm;
  if (el.id) return el.id;
  return '(unnamed)';
}
function __ghLabel(el) {
  try {
    if (el.labels && el.labels.length) { const t = __ghText(el.labels[0]); if (t) return t; }
  } catch (e) {}
  const wrap = el.closest ? el.closest('label') : null;
  if (wrap) { const t = __ghText(wrap); if (t) return t; }
  return null;
}
const __GH_SEL = "a, button, input, select, textarea, [role='button'], [onclick]";
function __ghFrameName(iframe, idx) {
  return iframe.id || iframe.getAttribute('name') || iframe.getAttribute('title') || ('iframe[' + idx + ']');
}
function __ghWalk(root, frameName, offX, offY, visit) {
  const nodes = root.querySelectorAll('*');
  let frameIdx = 0;
  for (const el of nodes) {
    if (el.matches(__GH_SEL)) visit(el, frameName, offX, offY);
    if (el.shadowRoot) __ghWalk(el.shadowRoot, frameName, offX, offY, visit);
    if (el.tagName === 'IFRAME') {
      let doc = null;
      try { doc = el.contentDocument; } catch (e) {}
      if (doc) {
        const r = el.getBoundingClientRect();
        __ghWalk(doc, __ghFrameName(el, frameIdx), offX + r.x + (el.clientLeft || 0), offY + r.y + (el.clientTop || 0), visit);
      }
      frameIdx += 1;
    }
  }
}
function __ghCollect() {
  const out = [];
  __ghWalk(document, null, 0, 0, (el) => { if (__ghVisible(el)) out.push(el); });
  return out;
}
function __ghDescribe(el, frameName) {
  const attrs = {};
  for (const a of el.attributes) attrs[a.name] = a.value;
  const form = el.closest ? el.closest('form') : null;
  return {
    tag: el.tagName.toLowerCase(), attrs: attrs, name: __ghName(el),
    type: (el.tagName === 'INPUT' || el.tagName === 'BUTTON') ? (attrs['type'] || null) : null,
    href: el.tagName === 'A' ? (attrs['href'] || null) : null,
    id: el.id || null, attr_name: attrs['name'] || null,
    placeholder: attrs['placeholder'] || null,
    value: (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT') ? String(el.value) : (attrs['value'] || null),
    form_action: form ? form.getAttribute('action') : null,
    label: __ghLabel(el), frame: frameName
  };
}
function __ghDescriptors() {
  const out = [];
  __ghWalk(document, null, 0, 0, (el, fr) => { if (__ghVisible(el)) out.push(__ghDescribe(el, fr)); });
  return out;
}
function __ghCoords() {
  const out = [];
  __ghWalk(document, null, 0, 0, (el, fr, ox, oy) => {
    if (__ghVisible(el)) {
      const r = el.getBoundingClientRect();
      out.push({x: ox + r.x + r.width / 2, y: oy + r.y + r.height / 2, w: r.width, h: r.height});
    }
  });
  return out;
}
function __ghAnchors(scopeEl) {
  const out = [];
  const walkRoot = (r) => {
    r.querySelectorAll('a').forEach(a => out.push({text: __ghText(a), href: a.getAttribute('href')}));
    r.querySelectorAll('*').forEach(el => {
      if (el.shadowRoot) walkRoot(el.shadowRoot);
      if (el.tagName === 'IFRAME') { try { if (el.contentDocument) walkRoot(el.contentDocument); } catch (e) {} }
    });
  };
  walkRoot(scopeEl || document);
  return out;
}
function __ghTable(scopeEl) {
  const table = (scopeEl && scopeEl.tagName === 'TABLE') ? scopeEl : document.querySelector('table');
  if (!table) return [];
  const trs = Array.from(table.querySelectorAll('tr'));
  const rows = trs.map(tr => Array.from(tr.querySelectorAll('th,td')).map(c => __ghText(c)));
  const flags = trs.map(tr => Array.from(tr.querySelectorAll('th,td')).map(c => c.tagName === 'TH'));
  if (!rows.length) return [];
  let headers, data;
  if (flags[0] && flags[0].some(Boolean)) { headers = rows[0]; data = rows.slice(1); }
  else { headers = rows[0].map((_, i) => 'col' + (i + 1)); data = rows; }
  return data.map(r => { const o = {}; headers.forEach((h, i) => { o[h] = (r[i] !== undefined ? r[i] : ''); }); return o; });
}
function __ghMatchField(els, key) {
  const k = key.toLowerCase();
  const score = (el) => {
    const cands = [];
    const lab = __ghLabel(el); if (lab) cands.push(lab.toLowerCase());
    cands.push((el.getAttribute('name') || '').toLowerCase());
    cands.push((el.getAttribute('placeholder') || '').toLowerCase());
    cands.push((el.getAttribute('aria-label') || '').toLowerCase());
    cands.push((el.id || '').toLowerCase());
    cands.push(__ghName(el).toLowerCase());
    if (cands.some(c => c && c === k)) return 2;
    if (cands.some(c => c && c.includes(k))) return 1;
    return 0;
  };
  let best = -1, bestScore = 0;
  els.forEach((el, i) => {
    if (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA' && el.tagName !== 'SELECT') return;
    const s = score(el);
    if (s > bestScore) { bestScore = s; best = i; }
  });
  return best;
}
function __ghFillForm(pairs) {
  const els = __ghCollect();
  const results = [];
  let form = null;
  for (const [key, rawVal] of Object.entries(pairs)) {
    const val = String(rawVal);
    const idx = __ghMatchField(els, key);
    if (idx < 0) { results.push({field: key, ok: false, error: 'no matching field'}); continue; }
    const el = els[idx];
    try {
      el.focus();
      const t = (el.getAttribute('type') || '').toLowerCase();
      if (el.tagName === 'SELECT') { el.value = val; }
      else if (t === 'checkbox' || t === 'radio') {
        el.checked = ['true', 'yes', 'on', '1', 'checked'].includes(val.toLowerCase());
      } else { el.value = val; }
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
      if (!form && el.closest) form = el.closest('form');
      results.push({field: key, ok: true, target: idx + 1, value: String(el.value)});
    } catch (e) {
      results.push({field: key, ok: false, error: String(e)});
    }
  }
  return results;
}
function __ghSubmitForm() {
  const form = document.querySelector('form');
  if (!form) return null;
  const ctl = form.querySelector("button[type='submit'], input[type='submit'], button:not([type]), input[type='image']");
  if (!ctl) return null;
  const desc = __ghDescribe(ctl, null);
  ctl.click();
  return desc;
}
"""

# action.type -> CDP key name (for chords: the final segment wins the key,
# the leading segments become the modifiers bitmask).
_MODIFIERS = {"control": 2, "ctrl": 2, "alt": 1, "meta": 4, "cmd": 4, "shift": 8}

# Windows virtual key codes: Chrome's editing shortcuts (select-all,
# copy, paste…) key off these, not off the key name — a chord dispatched
# without them is a plain keypress wearing a modifier costume.
_VK_MAP = {
    "Enter": 13,
    "Tab": 9,
    "Escape": 27,
    "Backspace": 8,
    "Delete": 46,
    "ArrowLeft": 37,
    "ArrowUp": 38,
    "ArrowRight": 39,
    "ArrowDown": 40,
    "Home": 36,
    "End": 35,
    "PageUp": 33,
    "PageDown": 34,
    " ": 32,
    "Space": 32,
}


def _vk_for(key_part: str) -> Optional[int]:
    if key_part in _VK_MAP:
        return _VK_MAP[key_part]
    if len(key_part) == 1 and key_part.isalnum():
        return ord(key_part.upper())
    return None

# AX role -> (Element tag, Element type) for the AX-eyes map.
_AX_TAG_TYPE = {
    "link": ("a", None),
    "button": ("button", None),
    "textbox": ("input", "text"),
    "searchbox": ("input", "search"),
    "checkbox": ("input", "checkbox"),
    "radio": ("input", "radio"),
    "combobox": ("select", None),
    "listbox": ("select", None),
    "option": ("option", None),
    "slider": ("input", "range"),
    "spinbutton": ("input", "number"),
    "switch": ("button", None),
    "tab": ("button", None),
    "menuitem": ("button", None),
    "menuitemcheckbox": ("input", "checkbox"),
    "menuitemradio": ("input", "radio"),
    "treeitem": ("button", None),
}

# Viewport presets: name -> (width, height, mobile/touch).
_VIEWPORT_PRESETS = {
    "desktop": (1280, 800, False),
    "mobile": (390, 844, True),
}

NET_EVENT_CAP = 200


class ChromiumDriver:
    """A real Chromium driven by our own CDP client. Stdlib only.

    Auto-wait: every targeted action first polls the page (up to
    ``auto_wait`` seconds) for its target to exist with a non-zero box —
    our own equivalent of the auto-waiting other drivers advertise.

    Proxy: by default the driver honors ``HTTPS_PROXY`` / ``https_proxy``
    from the environment (``proxy=""`` disables; an explicit ``proxy=``
    URL wins). If that URL carries credentials, a local
    credential-injecting relay (:class:`_AuthProxyRelay`) is started and
    Chrome points at it, because Chrome itself rejects credentials in
    ``--proxy-server``. ``ignore_cert_errors=True`` adds
    ``--ignore-certificate-errors`` — needed behind TLS-intercepting
    proxies whose CA the browser does not trust; off by default because
    it weakens TLS verification for the session.

    v0.3 knobs: ``eyes`` selects the perception mode ("dom" — the
    composed-tree walk that pierces open shadow roots and same-origin
    iframes; "ax" — the CDP Accessibility tree), ``dialog_policy`` is
    "dismiss" (default) or "accept" for JS dialogs, ``download_dir``
    receives browser downloads, and ``viewport`` applies a named
    emulation preset ("desktop" / "mobile") at launch. Asynchronous
    happenings (dialogs, downloads, network requests) are reported
    through ``self.event_sink`` when a Runner/MCP session wires one in.
    """

    def __init__(
        self,
        chrome_path: Optional[str] = None,
        headless: bool = True,
        auto_wait: float = 5.0,
        proxy: Optional[str] = None,
        ignore_cert_errors: bool = False,
        eyes: str = "dom",
        dialog_policy: str = "dismiss",
        download_dir: Optional[str] = None,
        viewport: Optional[str] = None,
    ) -> None:
        if eyes not in ("dom", "ax"):
            raise HandsError(f"unknown eyes mode: {eyes!r} (expected 'dom' or 'ax')")
        if dialog_policy not in ("accept", "dismiss"):
            raise HandsError(
                f"unknown dialog_policy: {dialog_policy!r} (expected 'accept' or 'dismiss')"
            )
        self._chrome_path = chrome_path
        self._headless = headless
        self.auto_wait = float(auto_wait)
        self._proxy = proxy
        self._ignore_cert_errors = ignore_cert_errors
        # v0.3 state
        self.eyes = eyes
        self.dialog_policy = dialog_policy
        self._download_dir = download_dir
        self._downloads_configured = False
        self._viewport_preset = viewport
        self.event_sink: Optional[Callable[[str, dict], None]] = None
        self.network_log: list[dict] = []
        self.net_truncated = False
        self._net_pending: dict[str, dict] = {}
        self._downloads: dict[str, dict] = {}
        self._download_sink_fired: set[str] = set()
        self._download_event_log: list[dict] = []
        self._last_ax_map: Optional[ElementMap] = None
        self._proc: Optional[subprocess.Popen] = None
        self._client: Optional[CDPClient] = None
        self._session: Optional[str] = None
        self._url = "about:blank"
        self._relay: Optional[_AuthProxyRelay] = None
        self._tabs: list[dict] = []
        self._tab_index = -1
        self.transport = "pipe"

    # -- proxy --------------------------------------------------------------
    def _proxy_launch_arg(self) -> Optional[str]:
        """Resolve the --proxy-server value, starting the auth relay when
        the configured proxy carries credentials. Returns None when no
        proxy applies."""
        raw = self._proxy
        if raw is None:
            raw = (
                os.environ.get("HTTPS_PROXY")
                or os.environ.get("https_proxy")
                or ""
            )
        raw = (raw or "").strip()
        if not raw:
            return None
        u = urlparse(raw)
        if not u.hostname:
            return None
        if u.username:
            self._relay = _AuthProxyRelay(raw)
            return self._relay.start()
        port = u.port or (443 if u.scheme == "https" else 80)
        return f"{u.scheme or 'http'}://{u.hostname}:{port}"

    # -- lifecycle ---------------------------------------------------------
    def _launch(self) -> None:
        if self._client is not None:
            return
        chrome = find_chrome(self._chrome_path)
        if not chrome:
            raise HandsError(
                "no Chromium/Chrome binary found; pass chrome_path, set "
                "GHOST_HANDS_CHROME, or install Chromium"
            )
        r_in, w_in = os.pipe()
        r_out, w_out = os.pipe()

        args = [
            chrome,
            "--remote-debugging-pipe",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--window-size=1280,900",
        ]
        proxy_arg = self._proxy_launch_arg()
        if proxy_arg:
            args.append(f"--proxy-server={proxy_arg}")
            args.append("--proxy-bypass-list=localhost;127.0.0.1;[::1]")
        if self._ignore_cert_errors:
            args.append("--ignore-certificate-errors")
        if self._headless:
            args.append("--headless=new")
        args.append("about:blank")
        # Spawn through a one-line shim interpreter that maps the pipe fds
        # onto 3/4 and execs Chrome (preexec_fn is unsafe with threads).
        argv = [sys.executable, "-c", _LAUNCH_SHIM, str(r_in), str(w_out), *args]
        try:
            proc = subprocess.Popen(
                argv,
                pass_fds=(r_in, w_out),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except OSError as exc:
            raise HandsError(f"failed to launch Chromium at {chrome}: {exc}") from exc
        os.close(r_in)
        os.close(w_out)
        wfd = os.fdopen(w_in, "wb", buffering=0)
        rfd = os.fdopen(r_out, "rb", buffering=0)
        self._proc = proc
        self._client = CDPClient(proc, wfd, rfd)
        self._client.on_event = self._on_cdp_event
        target = self._client.send("Target.createTarget", {"url": "about:blank"})
        attached = self._client.send(
            "Target.attachToTarget",
            {"targetId": target["targetId"], "flatten": True},
        )
        self._session = attached["sessionId"]
        self._client.send("Page.enable", {}, session_id=self._session)
        self._client.send("Runtime.enable", {}, session_id=self._session)
        # v0.3: Network capture for every session; DOM for file inputs.
        self._client.send("Network.enable", {}, session_id=self._session)
        self._client.send("DOM.enable", {}, session_id=self._session)
        self._tabs = [
            {
                "target_id": target["targetId"],
                "session_id": self._session,
                "url": "about:blank",
            }
        ]
        self._tab_index = 0
        if self._viewport_preset:
            self._apply_viewport(self._viewport_preset, None, None)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.send("Browser.close", {}, timeout=3)
            except HandsError:
                pass
            time.sleep(0.2)
            self._client.close()
            self._client = None
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None
        if self._relay is not None:
            self._relay.stop()
            self._relay = None
        self._tabs = []
        self._tab_index = -1

    # -- CDP helpers --------------------------------------------------------
    def _send(
        self,
        method: str,
        params: Optional[dict] = None,
        session_id: Optional[str] = None,
        timeout: float = 15.0,
    ) -> dict:
        self._launch()
        assert self._client is not None
        return self._client.send(
            method,
            params or {},
            session_id=session_id or self._session,
            timeout=timeout,
        )

    def _eval(self, expression: str, session_id: Optional[str] = None):
        result = self._send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
            session_id=session_id,
        )
        payload = result.get("result", {})
        if "exceptionDetails" in result or payload.get("subtype") == "error":
            details = result.get("exceptionDetails") or {}
            text = (
                (details.get("exception") or {}).get("description")
                or details.get("text")
                or "unknown evaluation error"
            )
            raise HandsError(
                f"page evaluation failed: {expression[:60]}… — {str(text)[:160]}"
            )
        return payload.get("value")

    def _wait_load(self, timeout: float = 5.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                state = self._eval("document.readyState")
            except HandsError:
                state = None
            if state in ("complete", "interactive"):
                return
            time.sleep(0.05)

    def _handles(self) -> list[dict]:
        return self._eval(_HANDLES_JS) or []

    # -- v0.3 resolution: one composed walk serves perception and action ---
    def _gh_eval(self, body: str):
        """Evaluate JS with the Ghost Hands in-page library loaded."""
        return self._eval("(() => { " + _GH_PRELUDE_JS + " " + body + " })()")

    def _live_coords(self) -> list[dict]:
        return self._gh_eval("return __ghCoords();") or []

    def _ax_center(self, element: Element) -> Optional[dict]:
        """Resolve an AX-eyes element to viewport coordinates via its
        backend node id (DOM.getBoxModel)."""
        if element.backend_id is None:
            return None
        try:
            model = self._send(
                "DOM.getBoxModel", {"backendNodeId": element.backend_id}
            )
        except HandsError:
            return None
        quad = ((model.get("model") or {}).get("content")) or []
        if len(quad) < 8:
            return None
        xs = quad[0::2]
        ys = quad[1::2]
        return {
            "x": sum(xs) / len(xs),
            "y": sum(ys) / len(ys),
            "w": max(xs) - min(xs),
            "h": max(ys) - min(ys),
        }

    def _resolve(self, element: Element) -> Optional[dict]:
        """Auto-wait for the target: poll until the element exists with a
        real box, or the auto-wait budget runs out. DOM eyes resolve by
        number through the composed walk (shadow + frames); AX eyes
        resolve by backend node id."""
        deadline = time.time() + self.auto_wait
        while True:
            if element.backend_id is not None:
                center = self._ax_center(element)
                if center is not None:
                    return center
            else:
                coords = self._live_coords()
                idx = element.number - 1
                if 0 <= idx < len(coords) and coords[idx]["w"] > 0 and coords[idx]["h"] > 0:
                    return coords[idx]
            if time.time() >= deadline:
                return None
            time.sleep(0.1)

    def _check_fresh(self, element: Element) -> None:
        """Stale-target check (DOM eyes): the node now sitting at this
        number must be the same kind of element the decider saw. AX eyes
        resolve by node identity, so no positional check applies."""
        if element.backend_id is not None:
            return
        descriptors = self._gh_eval("return __ghDescriptors();") or []
        idx = element.number - 1
        if not (0 <= idx < len(descriptors)):
            return  # _resolve already reported the missing target
        actual_tag = descriptors[idx].get("tag")
        if actual_tag and element.tag and actual_tag != element.tag:
            raise HandsError(
                f"target mismatch: [{element.number}] was <{element.tag}> "
                f'"{element.name}", page now has <{actual_tag}> there'
            )

    def _element_js(self, element: Element, body: str) -> str:
        """JS acting on the live element at the perceived number, through
        the same composed walk perception used (shadow + frame aware)."""
        return (
            "(() => { "
            + _GH_PRELUDE_JS
            + f" const el = __ghCollect()[{element.number - 1}];"
            + " if (!el) return null; "
            + body
            + " })()"
        )

    def _mouse(
        self,
        x: float,
        y: float,
        mtype: str,
        button: str = "left",
        click_count: int = 1,
    ) -> None:
        params: dict = {"type": mtype, "x": x, "y": y}
        if mtype in ("mousePressed", "mouseReleased"):
            params["button"] = button
            params["clickCount"] = click_count
        self._send("Input.dispatchMouseEvent", params)

    # -- protocol -----------------------------------------------------------
    def open(self, url: str) -> None:
        self._launch()
        result: dict = {}
        try:
            result = self._send("Page.navigate", {"url": url}, timeout=30.0)
        except HandsError as exc:
            # A slow proxy can delay the navigate ACK past the timeout
            # while the load itself still completes; _wait_load decides.
            if "timeout" not in str(exc):
                raise
        if isinstance(result, dict) and result.get("errorText"):
            raise HandsError(
                f"navigation to {url} failed: {result['errorText']}"
            )
        self._url = url
        self._wait_load(15.0)
        self._sync_tab_url()

    def snapshot_html(self) -> str:
        self._launch()
        # Pages that redirect or re-render client-side can briefly destroy
        # the JS execution context mid-load; one failed evaluation is
        # transient, so perception retries for a few seconds before
        # reporting a real failure.
        last_exc: Optional[Exception] = None
        for _ in range(12):
            try:
                return self._eval("document.documentElement.outerHTML") or ""
            except HandsError as exc:
                last_exc = exc
                time.sleep(0.25)
        raise HandsError(f"snapshot failed after retries: {last_exc}")

    def current_url(self) -> str:
        if self._client is None:
            return self._url
        try:
            url = self._eval("location.href")
            if url:
                self._url = url
                self._sync_tab_url()
        except HandsError:
            pass
        return self._url

    def _sync_tab_url(self) -> None:
        if 0 <= self._tab_index < len(self._tabs):
            self._tabs[self._tab_index]["url"] = self._url

    # -- v0.3: driver-originated events (dialogs / downloads / network) -----
    def _sink(self, event_type: str, **fields) -> None:
        if self.event_sink is not None:
            try:
                self.event_sink(event_type, fields)
            except Exception:
                pass  # reporting must never break the body

    def _on_cdp_event(self, msg: dict) -> None:
        """Runs on the CDP dispatch thread. Dialogs MUST be answered from
        here (the page's JS is blocked until they are)."""
        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "Page.javascriptDialogOpening":
            accept = self.dialog_policy == "accept"
            session = msg.get("sessionId") or self._session
            if self._client is not None:
                try:
                    self._client.send(
                        "Page.handleJavaScriptDialog",
                        {"accept": accept},
                        session_id=session,
                        timeout=5,
                    )
                except HandsError:
                    pass
            self._sink(
                "dialog",
                dialog_type=params.get("type", ""),
                message=(params.get("message") or "")[:300],
                decision="accepted" if accept else "dismissed",
                policy=self.dialog_policy,
                url=params.get("url", ""),
            )
        elif method == "Network.requestWillBeSent":
            req = params.get("request") or {}
            self._net_pending[params.get("requestId", "")] = {
                "method": req.get("method", ""),
                "url": req.get("url", ""),
                "type": params.get("type", ""),
            }
        elif method == "Network.responseReceived":
            entry = self._net_pending.pop(params.get("requestId", ""), None)
            resp = params.get("response") or {}
            if entry is None:
                entry = {
                    "method": "",
                    "url": resp.get("url", ""),
                    "type": params.get("type", ""),
                }
            entry["status"] = resp.get("status")
            if len(self.network_log) < NET_EVENT_CAP:
                self.network_log.append(entry)
                self._sink("net", **entry)
            elif not self.net_truncated:
                self.net_truncated = True
                self._sink(
                    "net",
                    truncated=True,
                    note=f"network capture capped at {NET_EVENT_CAP} requests",
                )
        elif method == "Network.loadingFailed":
            self._net_pending.pop(params.get("requestId", ""), None)
        elif method == "Browser.downloadWillBegin":
            self._downloads[params.get("guid", "")] = {
                "filename": params.get("suggestedFilename", ""),
                "url": params.get("url", ""),
                "state": "inProgress",
                "bytes": 0,
            }
        elif method == "Browser.downloadProgress":
            guid = params.get("guid", "")
            entry = self._downloads.setdefault(
                guid, {"filename": "", "url": "", "state": "inProgress", "bytes": 0}
            )
            entry["state"] = params.get("state", entry["state"])
            entry["bytes"] = int(params.get("receivedBytes") or 0)
            if entry["state"] == "completed" and guid not in self._download_sink_fired:
                self._download_sink_fired.add(guid)
                self._download_event_log.append(
                    {"filename": entry["filename"], "_ts": time.time()}
                )
                self._sink(
                    "download",
                    filename=entry["filename"],
                    bytes=entry["bytes"],
                    url=entry["url"],
                    state="completed",
                )

    # -- v0.3: perception (two eyes) -----------------------------------------
    def perceive(self) -> ElementMap:
        """The Runner's preferred perception. DOM eyes: the composed-tree
        JS walk (shadow-piercing, frame-aware). AX eyes: the CDP
        accessibility tree."""
        self._launch()
        if self.eyes == "ax":
            return self._perceive_ax()
        items = None
        last_exc: Optional[Exception] = None
        for _ in range(12):
            try:
                items = self._eval(
                    "(() => { " + _GH_PRELUDE_JS + " return __ghDescriptors(); })()"
                )
                break
            except HandsError as exc:
                # Same transient-context retry as snapshot_html().
                last_exc = exc
                time.sleep(0.25)
        if items is None:
            raise HandsError(f"perceive failed after retries: {last_exc}")
        items = items or []
        # Settle: subframes (srcdoc) and web components can finish a beat
        # after the top document reports complete. If a second look a
        # moment later sees a different page, believe the later one.
        for _ in range(3):
            time.sleep(0.2)
            try:
                later = self._eval(
                    "(() => { " + _GH_PRELUDE_JS + " return __ghDescriptors(); })()"
                ) or []
            except HandsError:
                break
            if len(later) == len(items):
                break
            items = later
        url = self.current_url()
        element_map = ElementMap.from_elements(items, url=url)
        try:
            element_map.title = self._eval("document.title") or ""
        except HandsError:
            element_map.title = ""
        return element_map

    def _perceive_ax(self) -> ElementMap:
        self._send("Accessibility.enable", {})
        tree = self._send("Accessibility.getFullAXTree", {})
        items: list[dict] = []
        for node in tree.get("nodes", []):
            if node.get("ignored"):
                continue
            role = ((node.get("role") or {}).get("value")) or ""
            if role not in _AX_TAG_TYPE:
                continue
            tag, itype = _AX_TAG_TYPE[role]
            name = ((node.get("name") or {}).get("value")) or ""
            value = (node.get("value") or {}).get("value")
            attrs: dict[str, str] = {}
            for prop in node.get("properties", []) or []:
                pval = (prop.get("value") or {}).get("value")
                if pval is not None:
                    attrs[f"ax-{prop.get('name', '')}"] = str(pval)
            items.append(
                {
                    "tag": tag,
                    "role": role,
                    "name": name if name else "(unnamed)",
                    "type": itype,
                    "value": None if value is None else str(value),
                    "attrs": attrs,
                    "backend_id": node.get("backendDOMNodeId"),
                }
            )
        url = self.current_url()
        element_map = ElementMap.from_elements(items, url=url)
        try:
            element_map.title = self._eval("document.title") or ""
        except HandsError:
            element_map.title = ""
        self._last_ax_map = element_map
        return element_map

    # -- v0.3: viewport emulation ---------------------------------------------
    def _apply_viewport(
        self, preset: Optional[str], width: Optional[int], height: Optional[int]
    ) -> str:
        if preset:
            if preset not in _VIEWPORT_PRESETS:
                raise HandsError(
                    f"unknown viewport preset: {preset!r} "
                    f"(have: {', '.join(sorted(_VIEWPORT_PRESETS))})"
                )
            w, h, mobile = _VIEWPORT_PRESETS[preset]
            label = preset
        elif width and height:
            w, h, mobile = int(width), int(height), False
            label = f"{w}x{h}"
        else:
            raise HandsError("set_viewport needs a preset or width+height")
        self._send(
            "Emulation.setDeviceMetricsOverride",
            {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": mobile},
        )
        try:
            self._send(
                "Emulation.setTouchEmulationEnabled",
                {"enabled": mobile, "maxTouchPoints": 5 if mobile else 1},
            )
        except HandsError:
            pass
        self._viewport_preset = label
        return label

    # -- v0.3: downloads ---------------------------------------------------------
    def download_path(self) -> Path:
        if self._download_dir is None:
            self._download_dir = tempfile.mkdtemp(prefix="ghost-hands-downloads-")
        return Path(self._download_dir)

    def _ensure_download_behavior(self) -> None:
        if self._downloads_configured:
            return
        self._launch()
        assert self._client is not None
        directory = self.download_path()
        directory.mkdir(parents=True, exist_ok=True)
        # Browser-level command (no session): downloads for the profile.
        self._client.send(
            "Browser.setDownloadBehavior",
            {
                "behavior": "allow",
                "downloadPath": str(directory),
                "eventsEnabled": True,
            },
        )
        self._downloads_configured = True

    def _await_download(self, before: set, timeout: float = 30.0) -> tuple[str, Path, int]:
        """Wait for a download started just now to complete. Ground truth
        is the file on disk reaching a stable size; the CDP completion
        event, when it arrives, confirms it."""
        directory = self.download_path()
        deadline = time.time() + timeout
        last_sizes: dict[str, int] = {}
        stable_since: dict[str, float] = {}
        while time.time() < deadline:
            # CDP says a download completed: find its file.
            for guid, entry in self._downloads.items():
                if entry.get("state") == "completed" and entry.get("filename"):
                    candidate = directory / entry["filename"]
                    if candidate.is_file():
                        return entry["filename"], candidate, candidate.stat().st_size
            # Disk fallback: a new file whose size stopped changing.
            try:
                names = {p.name for p in directory.iterdir() if p.is_file()}
            except OSError:
                names = set()
            for name in names - before:
                if name.endswith(".crdownload"):
                    continue
                path = directory / name
                size = path.stat().st_size
                if last_sizes.get(name) == size:
                    if time.time() - stable_since.get(name, time.time()) >= 0.5:
                        return name, path, size
                else:
                    last_sizes[name] = size
                    stable_since[name] = time.time()
            time.sleep(0.15)
        raise HandsError(f"download did not complete within {timeout:.0f}s")

    # -- tabs (v0.2) ---------------------------------------------------------
    def open_tab(self, url: str = "about:blank") -> int:
        """Open a new tab (its own CDP target + session) and switch to
        it. Returns the new tab's index."""
        self._launch()
        assert self._client is not None
        target = self._client.send("Target.createTarget", {"url": url})
        attached = self._client.send(
            "Target.attachToTarget",
            {"targetId": target["targetId"], "flatten": True},
        )
        session_id = attached["sessionId"]
        self._client.send("Page.enable", {}, session_id=session_id)
        self._client.send("Runtime.enable", {}, session_id=session_id)
        self._client.send("Network.enable", {}, session_id=session_id)
        self._client.send("DOM.enable", {}, session_id=session_id)
        self._tabs.append(
            {"target_id": target["targetId"], "session_id": session_id, "url": url}
        )
        self.switch_tab(len(self._tabs) - 1)
        if url != "about:blank":
            self._wait_load(15.0)
            try:
                live = self._eval("location.href")
                if live:
                    self._url = live
                    self._sync_tab_url()
            except HandsError:
                pass
        return self._tab_index

    def switch_tab(self, target) -> dict:
        """Switch the active tab by index (int) or target id (str).
        Perception and actions always apply to the active tab."""
        self._launch()
        if isinstance(target, int):
            idx = target
        else:
            idx = next(
                (i for i, t in enumerate(self._tabs) if t["target_id"] == target),
                -1,
            )
        if not (0 <= idx < len(self._tabs)):
            raise HandsError(f"no such tab: {target!r}")
        self._tab_index = idx
        self._session = self._tabs[idx]["session_id"]
        self._url = self._tabs[idx]["url"]
        return self.list_tabs()[idx]

    def list_tabs(self) -> list[dict]:
        """All tabs as {index, target_id, url, active} dicts."""
        self._launch()
        out: list[dict] = []
        for i, tab in enumerate(self._tabs):
            try:
                url = self._eval("location.href", session_id=tab["session_id"])
                if url:
                    tab["url"] = url
            except HandsError:
                pass
            out.append(
                {
                    "index": i,
                    "target_id": tab["target_id"],
                    "url": tab["url"],
                    "active": i == self._tab_index,
                }
            )
        return out

    # -- session state (v0.2) ---------------------------------------------
    def get_cookies(self) -> list[dict]:
        """All cookies in the browser profile (Network.getAllCookies)."""
        self._launch()
        assert self._client is not None
        try:
            result = self._client.send(
                "Network.getAllCookies", {}, session_id=self._session
            )
        except HandsError:
            result = self._client.send("Network.getAllCookies", {})
        return result.get("cookies", [])

    def set_cookie(self, name: str, value: str, url: Optional[str] = None) -> None:
        self._launch()
        self._send("Network.enable", {})
        result = self._send(
            "Network.setCookie",
            {"name": name, "value": value, "url": url or self.current_url()},
        )
        if not result.get("success", True):
            raise HandsError(f"could not set cookie {name!r}")

    def local_storage(self) -> dict:
        """The current origin's localStorage as a dict."""
        raw = self._eval(
            "JSON.stringify(Object.fromEntries(Object.entries(localStorage)))"
        )
        return json.loads(raw) if raw else {}

    def set_local_storage(self, items: dict) -> None:
        if not items:
            return
        self._eval(
            "(() => { const items = "
            + json.dumps(items)
            + "; for (const [k, v] of Object.entries(items))"
            " localStorage.setItem(k, v); return true; })()"
        )

    def save_session(self, path) -> str:
        """Save cookies (whole profile) + the current origin's
        localStorage to a JSON file:
        ``{"tool": "ghost-hands", "version": 1, "cookies": [...],
        "origins": {origin: {"localStorage": {...}}}}``."""
        parts = urlparse(self.current_url())
        origin = (
            f"{parts.scheme}://{parts.netloc}" if parts.netloc else self.current_url()
        )
        data = {
            "tool": "ghost-hands",
            "version": 1,
            "cookies": self.get_cookies(),
            "origins": {origin: {"localStorage": self.local_storage()}},
        }
        Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
        return str(path)

    def load_session(self, path) -> dict:
        """Restore a file written by :meth:`save_session`: cookies via
        Network.setCookie, then each origin is visited so its
        localStorage can be written back."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("tool") != "ghost-hands":
            raise HandsError(f"{path} is not a ghost-hands session file")
        self._launch()
        self._send("Network.enable", {})
        for cookie in data.get("cookies", []):
            params = {
                k: cookie[k]
                for k in (
                    "name",
                    "value",
                    "domain",
                    "path",
                    "secure",
                    "httpOnly",
                    "sameSite",
                )
                if k in cookie
            }
            expires = cookie.get("expires")
            if isinstance(expires, (int, float)) and expires > 0:
                params["expires"] = expires
            result = self._send("Network.setCookie", params)
            if not result.get("success", True):
                raise HandsError(
                    f"could not restore cookie {cookie.get('name')!r}"
                )
        for origin, state in (data.get("origins") or {}).items():
            items = ((state or {}).get("localStorage")) or {}
            if items:
                self.open(origin)
                self.set_local_storage(items)
        return data

    # -- actions --------------------------------------------------------------
    def act(self, action: Action, element: Optional[Element]) -> str:
        self._launch()
        kind = action.kind
        if kind == "navigate":
            self.open(action.url or "about:blank")
            return f"navigated to {self.current_url()}"
        if kind == "wait":
            return self._act_wait(action)
        if kind == "scroll":
            amount = int(action.amount or 500)
            if (action.direction or "down") == "up":
                amount = -amount
            self._send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseWheel",
                    "x": 640,
                    "y": 450,
                    "deltaX": 0,
                    "deltaY": amount,
                },
            )
            return f"scrolled {action.direction or 'down'} {abs(amount)}"
        if kind == "screenshot":
            shot = self._send("Page.captureScreenshot", {"format": "png"})
            data = shot.get("data", "")
            raw = base64.b64decode(data) if data else b""
            path = action.path or "./ghost-hands-shot.png"
            Path(path).write_bytes(raw)
            return f"screenshot saved to {path} ({len(raw)} bytes)"
        if kind == "pdf":
            doc = self._send(
                "Page.printToPDF", {"printBackground": True, "landscape": False}
            )
            data = doc.get("data", "")
            raw = base64.b64decode(data) if data else b""
            path = action.path or "./ghost-hands-page.pdf"
            Path(path).write_bytes(raw)
            return f"pdf saved to {path} ({len(raw)} bytes)"
        if kind == "set_viewport":
            label = self._apply_viewport(action.value, action.width, action.height)
            return f"viewport set to {label}"
        if kind == "press":
            return self._act_press(action)
        if kind == "extract":
            return self._act_extract(action, element)
        if kind == "fill_form":
            return self._act_fill_form(action)
        if kind == "download":
            return self._act_download(action, element)
        if kind == "click_at":
            x, y = float(action.x or 0), float(action.y or 0)
            self._mouse(x, y, "mousePressed", "left", 1)
            self._mouse(x, y, "mouseReleased", "left", 1)
            return f"clicked at ({x:g}, {y:g}) — raw coordinates"

        if element is None:
            raise HandsError(f"{kind} requires a target element")
        handle = self._resolve(element)
        if handle is None:
            raise HandsError(
                f"target missing: [{element.number}] \"{element.name}\" did "
                f"not appear within {self.auto_wait}s (auto-wait)"
            )
        self._check_fresh(element)
        x, y = handle["x"], handle["y"]

        if kind == "click":
            return self._click_at_handle(element, handle)
        if kind == "hover":
            self._mouse(x, y, "mouseMoved")
            time.sleep(0.15)
            return f"hovered [{element.number}] \"{element.name}\" at ({x:.0f},{y:.0f})"
        if kind == "double_click":
            self._mouse(x, y, "mousePressed", "left", 1)
            self._mouse(x, y, "mouseReleased", "left", 1)
            self._mouse(x, y, "mousePressed", "left", 2)
            self._mouse(x, y, "mouseReleased", "left", 2)
            time.sleep(0.05)
            return f"double-clicked [{element.number}] \"{element.name}\" at ({x:.0f},{y:.0f})"
        if kind == "right_click":
            self._mouse(x, y, "mousePressed", "right", 1)
            self._mouse(x, y, "mouseReleased", "right", 1)
            time.sleep(0.05)
            return f"right-clicked [{element.number}] \"{element.name}\" at ({x:.0f},{y:.0f})"
        if kind == "drag":
            dest = self._resolve_drag_dest(action)
            self._mouse(x, y, "mouseMoved")
            self._mouse(x, y, "mousePressed", "left", 1)
            steps = 8
            for i in range(1, steps + 1):
                mx = x + (dest["x"] - x) * i / steps
                my = y + (dest["y"] - y) * i / steps
                self._mouse(mx, my, "mouseMoved")
                time.sleep(0.02)
            self._mouse(dest["x"], dest["y"], "mouseReleased", "left", 1)
            return (
                f"dragged [{element.number}] \"{element.name}\" to "
                f"[{action.to_target}] at ({dest['x']:.0f},{dest['y']:.0f})"
            )
        if kind == "type":
            self._focus_element(element)
            self._send("Input.insertText", {"text": action.text or ""})
            return f"typed into [{element.number}] \"{element.name}\""
        if kind == "select":
            if element.backend_id is not None:
                raise HandsError("select is not supported with AX eyes")
            expr = self._element_js(
                element,
                f"el.value = {json.dumps(action.value or '')};"
                "el.dispatchEvent(new Event('change', {bubbles: true}));"
                "return el.value;",
            )
            self._eval(expr)
            return f"selected \"{action.value}\" in [{element.number}]"
        if kind == "set_file":
            return self._act_set_file(action, element)
        raise HandsError(f"ChromiumDriver cannot perform {kind!r}")

    def _click_at_handle(self, element: Element, handle: dict) -> str:
        if element.backend_id is None:
            self._eval(
                self._element_js(
                    element, "el.scrollIntoView({block: 'center'}); return true;"
                )
            )
            handle = self._resolve(element) or handle
        x, y = handle["x"], handle["y"]
        self._mouse(x, y, "mousePressed", "left", 1)
        self._mouse(x, y, "mouseReleased", "left", 1)
        time.sleep(0.05)
        return f"clicked [{element.number}] \"{element.name}\" at ({x:.0f},{y:.0f})"

    def _focus_element(self, element: Element) -> None:
        if element.backend_id is not None:
            self._send("DOM.focus", {"backendNodeId": element.backend_id})
            return
        self._eval(self._element_js(element, "el.focus(); return true;"))

    def _resolve_drag_dest(self, action: Action) -> dict:
        """Coordinates for the drag destination element number. AX eyes
        resolve through the last perceived map's backend ids; DOM eyes
        through a fresh composed-walk coordinate poll."""
        if action.to_target is None:
            raise HandsError("drag needs a to_target element number")
        if self.eyes == "ax":
            last = getattr(self, "_last_ax_map", None)
            dest_el = last.get(action.to_target) if last is not None else None
            if dest_el is None or dest_el.backend_id is None:
                raise HandsError(
                    f"drag destination [{action.to_target}] is not in the last AX map"
                )
            center = self._ax_center(dest_el)
            if center is None:
                raise HandsError(
                    f"drag destination [{action.to_target}] has no box on the page"
                )
            return center
        deadline = time.time() + self.auto_wait
        idx = action.to_target - 1
        while True:
            coords = self._live_coords()
            if 0 <= idx < len(coords) and coords[idx]["w"] > 0 and coords[idx]["h"] > 0:
                return coords[idx]
            if time.time() >= deadline:
                raise HandsError(
                    f"drag destination [{action.to_target}] did not appear "
                    f"within {self.auto_wait}s (auto-wait)"
                )
            time.sleep(0.1)

    def _act_press(self, action: Action) -> str:
        raw = (action.key or "").strip()
        parts = [p.strip() for p in raw.split("+") if p.strip()]
        if not parts:
            raise HandsError("press needs a key")
        modifiers = 0
        for mod in parts[:-1]:
            modifiers |= _MODIFIERS.get(mod.lower(), 0)
        key_part = parts[-1]
        key, code = _KEY_MAP.get(
            key_part,
            (key_part, f"Key{key_part.upper()}" if len(key_part) == 1 else key_part),
        )
        params: dict = {"type": "keyDown", "key": key, "code": code}
        if modifiers:
            params["modifiers"] = modifiers
        vk = _vk_for(key_part)
        if vk is not None:
            params["windowsVirtualKeyCode"] = vk
            params["nativeVirtualKeyCode"] = vk
        if len(parts) == 1 and len(key) == 1:
            params["text"] = key
        self._send("Input.dispatchKeyEvent", params)
        up = dict(params)
        up["type"] = "keyUp"
        up.pop("text", None)
        self._send("Input.dispatchKeyEvent", up)
        return f"pressed {raw}"

    def _act_wait(self, action: Action) -> str:
        conditioned = bool(action.text or action.target or action.url)
        if not conditioned:
            deadline = time.time() + float(action.seconds or 0)
            while time.time() < deadline:
                self._eval("document.readyState")
                time.sleep(min(0.25, max(0.0, deadline - time.time())))
            return f"waited {action.seconds}s"
        timeout = float(action.seconds if action.seconds is not None else 10.0)
        what = (
            f"text {action.text!r}"
            if action.text
            else f"element [{action.target}]"
            if action.target
            else f"URL containing {action.url!r}"
        )
        deadline = time.time() + timeout
        started = time.time()
        while True:
            if action.text:
                text = self._eval(
                    "document.body ? document.body.innerText : ''"
                ) or ""
                if action.text in str(text):
                    return f"wait satisfied: {what} (after {time.time() - started:.1f}s)"
            elif action.target:
                count = len(self._live_coords()) if self.eyes == "dom" else len(
                    self._perceive_ax()
                )
                if count >= action.target:
                    return f"wait satisfied: {what} (after {time.time() - started:.1f}s)"
            elif action.url:
                if action.url in (self._eval("location.href") or ""):
                    return f"wait satisfied: {what} (after {time.time() - started:.1f}s)"
            if time.time() >= deadline:
                raise HandsError(
                    f"wait timed out after {timeout:g}s waiting for {what}"
                )
            time.sleep(0.2)

    def _act_extract(self, action: Action, element: Optional[Element]) -> str:
        mode = action.mode or "text"
        if mode == "network":
            return json.dumps(self.network_log)
        if mode in ("list", "table"):
            if element is not None and element.backend_id is not None:
                raise HandsError(
                    f"extract mode {mode!r} needs DOM eyes (AX maps carry no subtree)"
                )
            scope = (
                f"__ghCollect()[{element.number - 1}]"
                if element is not None
                else "null"
            )
            fn = "__ghAnchors" if mode == "list" else "__ghTable"
            data = self._gh_eval(f"return {fn}({scope});")
            return json.dumps(data or [])
        # text mode
        if element is None:
            text = self._eval("document.body ? document.body.innerText : ''") or ""
            return " ".join(str(text).split())[:500]
        if element.backend_id is not None:
            # Re-read the node's LIVE value (the perceived snapshot of an
            # AX element goes stale the moment anything types into it).
            resolved = self._send(
                "DOM.resolveNode", {"backendNodeId": element.backend_id}
            )
            object_id = (resolved.get("object") or {}).get("objectId")
            if not object_id:
                return element.value if element.value is not None else element.name
            called = self._send(
                "Runtime.callFunctionOn",
                {
                    "objectId": object_id,
                    "functionDeclaration": (
                        "function() { return (this.value !== undefined && "
                        "this.tagName !== 'A' && this.tagName !== 'BUTTON') "
                        "? this.value : this.textContent; }"
                    ),
                    "returnByValue": True,
                },
            )
            payload = (called.get("result") or {}).get("value")
            return "" if payload is None else str(payload)
        value = self._eval(
            self._element_js(
                element,
                "return el.value !== undefined && el.tagName !== 'A' && el.tagName !== 'BUTTON' ? el.value : el.textContent;",
            )
        )
        return "" if value is None else str(value)

    def _act_fill_form(self, action: Action) -> str:
        pairs = action.fields or {}
        results = self._gh_eval(
            f"return __ghFillForm({json.dumps(pairs)});"
        ) or []
        submitted = False
        note = ""
        if action.submit:
            desc = self._gh_eval("return __ghSubmitForm();")
            if desc:
                submitted = True
                self._wait_load(10.0)
            else:
                note = "submit requested but no submit control found"
        return _format_fill_results(results, submitted, note)

    def _act_set_file(self, action: Action, element: Element) -> str:
        path = Path(action.path or "")
        if not path.is_file():
            raise HandsError(
                f"set_file path is not a file: {action.path!r} "
                "(missing, or a directory — directories are refused)"
            )
        # The DOM agent only maps nodes it has been asked about:
        # requestNode returns id 0 until the document tree is pulled once.
        self._send("DOM.getDocument", {"depth": -1, "pierce": True})
        if element.backend_id is not None:
            resolved = self._send(
                "DOM.resolveNode", {"backendNodeId": element.backend_id}
            )
            object_id = (resolved.get("object") or {}).get("objectId")
            if not object_id:
                raise HandsError(
                    f"set_file target [{element.number}] could not be resolved in the page"
                )
            node = self._send("DOM.requestNode", {"objectId": object_id})
            node_id = node.get("nodeId")
        else:
            evaluated = self._send(
                "Runtime.evaluate",
                {
                    "expression": self._element_js(element, "return el;"),
                    "returnByValue": False,
                },
            )
            payload = evaluated.get("result", {})
            if "exceptionDetails" in evaluated or not payload.get("objectId"):
                raise HandsError(
                    f"set_file target [{element.number}] could not be resolved in the page"
                )
            node = self._send(
                "DOM.requestNode", {"objectId": payload["objectId"]}
            )
            node_id = node.get("nodeId")
        if not node_id:
            raise HandsError(
                f"set_file target [{element.number}] resolved to node id 0"
            )
        self._send(
            "DOM.setFileInputFiles",
            {"files": [str(path.resolve())], "nodeId": node_id},
        )
        return (
            f"set file on [{element.number}] \"{element.name}\" to "
            f"{path.name} ({path.stat().st_size} bytes)"
        )

    def _act_download(self, action: Action, element: Optional[Element]) -> str:
        self._ensure_download_behavior()
        directory = self.download_path()
        try:
            before = {p.name for p in directory.iterdir() if p.is_file()}
        except OSError:
            before = set()
        started = time.time()
        if element is not None:
            handle = self._resolve(element)
            if handle is None:
                raise HandsError(
                    f"target missing: [{element.number}] \"{element.name}\" did "
                    f"not appear within {self.auto_wait}s (auto-wait)"
                )
            self._check_fresh(element)
            self._click_at_handle(element, handle)
        elif action.url:
            self.open(action.url)
        else:
            raise HandsError("download needs a target link or a URL")
        filename, path, size = self._await_download(before)
        # If the CDP completion event already sank the trail event, good;
        # if completion was proven by disk alone, sink it here — exactly
        # once, keyed by filename+start so a repeat download re-reports.
        fired = getattr(self, "_download_manual_fired", set())
        key = f"{filename}:{int(started)}"
        already = any(
            entry.get("filename") == filename and entry.get("_ts", 0) >= started - 1
            for entry in getattr(self, "_download_event_log", [])
        )
        if not already and key not in fired:
            fired.add(key)
            self._download_manual_fired = fired
            self._sink(
                "download", filename=filename, bytes=size, url="", state="completed"
            )
        return f"downloaded {filename} ({size} bytes) to {path}"
