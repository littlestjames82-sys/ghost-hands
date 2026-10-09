"""AndroidDriver — the real Android body for Ghost Hands (v0.5).

This driver speaks HTTP/JSON to the **Ghost Hands bridge** inside the
MrGhosty Android app (v1.6+): a loopback-only HTTP server hosted by
MrGhosty's accessibility service on the phone itself
(``127.0.0.1:8378``). Reach it from a computer with USB forwarding —
``adb forward tcp:8378 tcp:8378`` — or run Ghost Hands on the phone.

The bridge contract (docs/BODY_PROTOCOL.md §6):

- ``GET  /v1/status``   → {ok, protocol: 1, app: "MrGhosty", version,
  service_connected, foreground_package, api_level}
- ``POST /v1/perceive`` → {ok, url: "android://<package>", package,
  elements: [...]} — each element carries an opaque ``ref`` (a
  child-index path from the window root, e.g. ``"0/2/1"``), preserved
  on the Element as ``body_ref`` and sent back with every act.
- ``POST /v1/act``      → {action, target_ref, expected: {tag, role,
  name}}; the bridge re-walks the live tree, resolves the ref, and
  verifies the key before touching anything. A vanished target is
  HTTP 404 "target missing: ...", a changed one HTTP 409 "target
  mismatch: ..." — the exact phrases the Runner's self-healing keys
  on, so healing works on this body exactly like on the web bodies.

Every request carries the pairing token as ``X-Ghost-Hands-Token``.
The token comes from the constructor argument or the
``GHOST_HANDS_ANDROID_TOKEN`` environment variable; it is never
logged, never written to the trail, and never included in error text.

Safety rails: the bridge URL must be loopback unless the caller
explicitly passes ``allow_nonlocal=True`` (the CLI never does), and
web-only actions (select / hover / fill_form / pdf / ...) fail
honestly instead of being faked against a phone.
"""

from __future__ import annotations

import base64
import html as _html
import ipaddress
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from .actions import Action
from .errors import HandsError
from .eyes import Element, ElementMap

DEFAULT_BRIDGE_URL = "http://127.0.0.1:8378"
BRIDGE_PROTOCOL = 1

#: Global actions the bridge performs via the accessibility service.
SUPPORTED_PRESS_KEYS = ("Back", "Home", "Recents", "Notifications", "QuickSettings")

#: Action kinds with no honest Android meaning. They fail locally,
#: before any HTTP, with a HandsError naming the gap.
_UNSUPPORTED_KINDS = (
    "select",
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

_UNREACHABLE_HINT = (
    "could not reach the MrGhosty Ghost Hands bridge at {url} ({cause}). "
    "Check: MrGhosty v1.6+ is installed and open on the phone, its "
    "accessibility service is enabled (MrGhosty → Device tab → Access "
    "checklist), and the bridge is forwarded to this machine — with a "
    "USB cable that is: adb forward tcp:8378 tcp:8378"
)


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class AndroidDriver:
    """Drives a real Android phone through the MrGhosty bridge.

    Implements the Driver protocol (docs/BODY_PROTOCOL.md) plus the
    optional ``perceive()`` / ``capabilities`` members. Stateless HTTP
    per request — the phone holds all the state.
    """

    #: Body Protocol capability flags (docs/BODY_PROTOCOL.md). The
    #: ``screenshot`` flag is refined per instance from /v1/status
    #: (real screenshots need API 30+).
    capabilities = {
        "perceive": True,
        "screenshot": False,
        "tabs": False,
        "session": False,
        "viewport": False,
        "pdf": False,
        "network_capture": False,
        "android": True,
    }

    def __init__(
        self,
        bridge_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 15.0,
        allow_nonlocal: bool = False,
    ) -> None:
        url = (
            bridge_url
            or os.environ.get("GHOST_HANDS_ANDROID_BRIDGE")
            or DEFAULT_BRIDGE_URL
        )
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise HandsError(
                f"invalid Android bridge URL {url!r} — expected "
                "http://127.0.0.1:8378 (an adb-forwarded loopback port)"
            )
        if not allow_nonlocal and not _is_loopback_host(parsed.hostname):
            raise HandsError(
                f"refusing non-loopback Android bridge host "
                f"{parsed.hostname!r}: the MrGhosty bridge is a "
                "loopback-only, token-paired local service. Forward it "
                "with `adb forward tcp:8378 tcp:8378`, or pass "
                "allow_nonlocal=True if you really mean a LAN bridge."
            )
        self.bridge_url = url.rstrip("/")
        # Token: explicit argument beats the environment. Never logged.
        self._token = token or os.environ.get("GHOST_HANDS_ANDROID_TOKEN")
        self.timeout = float(timeout)
        self.capabilities = dict(type(self).capabilities)
        self.event_sink = None  # the bridge emits no async events (v1)
        self.last_screenshot: Optional[bytes] = None
        self._status_cache: Optional[dict] = None
        self._last_map: Optional[ElementMap] = None
        self._last_url: str = ""
        # Any successful act may have changed the foreground app, so
        # the cached locator is dirty until the next perceive/status.
        self._url_dirty = True
        self._closed = False

    # -- HTTP plumbing ------------------------------------------------------

    def _request(
        self, method: str, path: str, payload: Optional[dict] = None
    ) -> dict:
        if not self._token:
            raise HandsError(
                "no Ghost Hands pairing token: pass token= or set "
                "GHOST_HANDS_ANDROID_TOKEN to the pairing code shown "
                "in MrGhosty on the phone (Device tab → Ghost Hands "
                "bridge). The token is never stored by Ghost Hands."
            )
        data = None
        headers = {"X-Ghost-Hands-Token": self._token}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.bridge_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp is not None else b""
            detail = ""
            try:
                detail = str(json.loads(raw.decode("utf-8")).get("error", ""))
            except Exception:
                detail = raw.decode("utf-8", "replace")[:200]
            if exc.code == 401:
                raise HandsError(
                    "Android bridge rejected the pairing token "
                    "(HTTP 401): token mismatch — the code in "
                    "GHOST_HANDS_ANDROID_TOKEN must match the pairing "
                    "code in MrGhosty (Device tab → Ghost Hands bridge)"
                ) from None
            # 404/409 keep the bridge's wording verbatim: "target
            # missing" / "target mismatch" are the Runner's heal cues.
            raise HandsError(
                f"Android bridge error (HTTP {exc.code}): "
                f"{detail or exc.reason}"
            ) from None
        except (urllib.error.URLError, OSError) as exc:
            cause = getattr(exc, "reason", exc)
            raise HandsError(
                _UNREACHABLE_HINT.format(url=self.bridge_url, cause=cause)
            ) from None
        try:
            out = json.loads(raw.decode("utf-8"))
        except Exception:
            raise HandsError(
                f"Android bridge at {self.bridge_url} returned a "
                "non-JSON response — is something else on that port?"
            ) from None
        if not isinstance(out, dict):
            raise HandsError("Android bridge returned a malformed response")
        return out

    # -- status ---------------------------------------------------------------

    def status(self) -> dict:
        """Fetch and validate GET /v1/status (fresh every call)."""
        resp = self._request("GET", "/v1/status")
        if resp.get("ok") is not True:
            raise HandsError(
                "Android bridge status not ok — the endpoint answered "
                "but did not report a healthy bridge"
            )
        if resp.get("protocol") != BRIDGE_PROTOCOL:
            raise HandsError(
                f"Android bridge protocol mismatch: bridge speaks "
                f"protocol {resp.get('protocol')!r}, Ghost Hands "
                f"expects {BRIDGE_PROTOCOL} — update MrGhosty or "
                "ghost-hands so they match"
            )
        app = resp.get("app")
        if app != "MrGhosty":
            raise HandsError(
                f"Android bridge answered as app {app!r}, not "
                "'MrGhosty' — something else is on that port"
            )
        if resp.get("service_connected") is not True:
            raise HandsError(
                "MrGhosty is reachable but its accessibility service "
                "is not connected — enable MrGhosty under Settings → "
                "Accessibility on the phone, then try again"
            )
        self._status_cache = resp
        api_level = resp.get("api_level")
        if isinstance(api_level, int):
            self.capabilities["screenshot"] = api_level >= 30
        return resp

    def _ensure_status(self) -> dict:
        if self._status_cache is None:
            return self.status()
        return self._status_cache

    # -- Driver protocol --------------------------------------------------------

    def open(self, url: str) -> None:
        target = (url or "").strip()
        if not target:
            return
        if target.startswith("android://"):
            # android://<package> is this body's locator for "the app
            # in the foreground"; opening it means launching the app.
            package = target[len("android://"):].split("/", 1)[0]
            if not package:
                return
            target = f"app://{package}"
        self._post_act({"kind": "navigate", "url": target}, None, None)
        self._last_url = self.current_url()

    def current_url(self) -> str:
        if self._last_url and not self._url_dirty:
            return self._last_url
        status = self.status()
        package = status.get("foreground_package")
        self._last_url = f"android://{package}" if package else "android://"
        self._url_dirty = False
        return self._last_url

    def perceive(self) -> ElementMap:
        self._ensure_status()
        resp = self._request("POST", "/v1/perceive", {})
        if resp.get("ok") is not True:
            raise HandsError("Android bridge perceive failed (ok != true)")
        items = [self._element_dict(raw) for raw in resp.get("elements", [])]
        url = str(resp.get("url") or self._last_url or "")
        element_map = ElementMap.from_elements(items, url=url)
        element_map.title = str(resp.get("package") or "")
        self._last_map = element_map
        if url:
            self._last_url = url
            self._url_dirty = False
        return element_map

    @staticmethod
    def _element_dict(raw: dict) -> dict:
        """Translate one bridge element into Element.from_dict input."""
        attrs: dict[str, Any] = {}
        for key, val in (raw.get("attrs") or {}).items():
            attrs[str(key)] = val if isinstance(val, str) else json.dumps(val)
        bounds = raw.get("bounds")
        if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
            attrs["bounds"] = ",".join(str(int(b)) for b in bounds)
        out: dict[str, Any] = {
            "tag": str(raw.get("tag") or ""),
            "role": str(raw.get("role") or ""),
            "name": str(raw.get("name") or ""),
            "attrs": attrs,
            "body_ref": raw.get("ref"),
        }
        for key in ("type", "id", "value", "label"):
            if raw.get(key) is not None:
                out[key] = raw[key]
        return out

    def snapshot_html(self) -> str:
        """A minimal, safely-escaped HTML rendering of the last
        perception — the protocol-compatibility fallback for callers
        that only speak snapshot_html(). Element identity on this
        body is the body_ref, not the HTML; prefer perceive()."""
        element_map = self._last_map or self.perceive()
        parts = [
            "<html><head><title>"
            + _html.escape(element_map.title or "Android")
            + "</title></head><body>"
        ]
        for el in element_map.elements:
            name = _html.escape(el.name or "")
            ref = _html.escape(el.body_ref or "", quote=True)
            if el.role in ("text_field", "textbox"):
                value = _html.escape(el.value or "")
                parts.append(
                    f'<textarea data-ref="{ref}" placeholder="{name}">'
                    f"{value}</textarea>"
                )
            elif el.role in ("checkbox", "toggle", "radio"):
                parts.append(
                    f'<input type="checkbox" data-ref="{ref}" '
                    f'aria-label="{name}">'
                )
            else:
                parts.append(f'<button data-ref="{ref}">{name}</button>')
        parts.append("</body></html>")
        return "".join(parts)

    def close(self) -> None:
        # Stateless HTTP: nothing to release. Safe to call repeatedly.
        self._closed = True

    # -- actions ------------------------------------------------------------------

    def _post_act(
        self,
        action_dict: dict,
        element: Optional[Element],
        expected: Optional[dict],
    ) -> dict:
        payload: dict[str, Any] = {"action": action_dict}
        if element is not None:
            payload["target_ref"] = element.body_ref
        if expected is not None:
            payload["expected"] = expected
        resp = self._request("POST", "/v1/act", payload)
        if resp.get("ok") is not True:
            raise HandsError(
                f"Android bridge refused the action: "
                f"{resp.get('error') or resp}"
            )
        self._url_dirty = True
        return resp

    @staticmethod
    def _expected_for(element: Element) -> dict:
        return {"tag": element.tag, "role": element.role, "name": element.name}

    def act(self, action: Action, element: Optional[Element]) -> str:
        kind = action.kind
        if kind == "done":
            raise HandsError("done is decider-side; it never reaches a body")
        if kind in _UNSUPPORTED_KINDS:
            raise HandsError(
                f"unsupported on Android body: {kind} — the phone body "
                "has no honest equivalent (see docs/BODY_PROTOCOL.md)"
            )
        if kind == "wait":
            return self._act_wait(action)
        if kind == "navigate":
            return self._act_remote(action, element)
        if kind in ("click", "type"):
            if element is None:
                raise HandsError(f"{kind} requires a target element")
            if element.body_ref is None:
                raise HandsError(
                    f"target missing: [{element.number}] "
                    f"<{element.tag}> \"{element.name}\" carries no "
                    "Android body_ref — re-perceive on the Android body"
                )
            return self._act_remote(action, element)
        if kind in ("press", "scroll", "extract", "screenshot"):
            return self._act_remote(action, element)
        raise HandsError(f"unsupported on Android body: {kind}")

    def _act_remote(self, action: Action, element: Optional[Element]) -> str:
        kind = action.kind
        action_dict = action.to_dict()
        if kind == "press":
            action_dict["key"] = self._normalize_press_key(action.key)
        if kind == "extract":
            mode = action.mode or "text"
            if mode != "text":
                raise HandsError(
                    f"unsupported on Android body: extract mode "
                    f"{mode!r} — the phone body extracts visible text "
                    "only (mode 'text')"
                )
            if element is not None:
                # A targeted extract reads the element itself, like
                # the other bodies do; no bridge round-trip needed.
                if element.body_ref is None:
                    raise HandsError(
                        f"target missing: [{element.number}] carries "
                        "no Android body_ref — re-perceive first"
                    )
                return element.value if element.value is not None else element.name
        expected = self._expected_for(element) if element is not None else None
        resp = self._post_act(action_dict, element, expected)
        if kind == "screenshot":
            return self._screenshot_result(action, resp)
        result = resp.get("result")
        if kind == "extract" and resp.get("text") is not None:
            return str(resp["text"])
        return str(result if result is not None else "ok")

    @staticmethod
    def _normalize_press_key(key: Optional[str]) -> str:
        raw = (key or "").strip()
        for supported in SUPPORTED_PRESS_KEYS:
            if raw.lower() == supported.lower():
                return supported
        raise HandsError(
            f"unsupported on Android body: press {raw!r} — the bridge "
            "performs the global actions "
            + ", ".join(SUPPORTED_PRESS_KEYS)
            + " only"
        )

    def _screenshot_result(self, action: Action, resp: dict) -> str:
        encoded = resp.get("png_base64")
        if not encoded:
            raise HandsError(
                "Android bridge screenshot returned no image data"
            )
        png = base64.b64decode(encoded)
        self.last_screenshot = png
        if action.path:
            Path(action.path).write_bytes(png)
            return f"screenshot saved to {action.path} (PNG, {len(png)} bytes)"
        return (
            f"screenshot captured (PNG, {len(png)} bytes; "
            "pass a path to save it)"
        )

    # -- wait ---------------------------------------------------------------------

    def _act_wait(self, action: Action) -> str:
        conditioned = bool(action.text or action.target or action.url)
        if not conditioned:
            time.sleep(float(action.seconds or 0))
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
            if self._wait_condition_met(action):
                return (
                    f"wait satisfied: {what} "
                    f"(after {time.time() - started:.1f}s)"
                )
            if time.time() >= deadline:
                raise HandsError(
                    f"wait timed out after {timeout:g}s waiting for {what}"
                )
            time.sleep(min(0.25, max(0.0, deadline - time.time())))

    def _wait_condition_met(self, action: Action) -> bool:
        element_map = self.perceive()
        if action.text:
            for el in element_map.elements:
                if action.text in (el.name or "") or action.text in (
                    el.value or ""
                ):
                    return True
            return False
        if action.target:
            return element_map.get(action.target) is not None
        if action.url:
            return action.url in (element_map.url or self.current_url())
        return True
