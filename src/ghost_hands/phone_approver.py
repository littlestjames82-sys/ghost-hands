"""PhoneApprover — the Governor's approver, speaking to Ryan's phone.

v0.6 adds the third approval channel (docs/APPROVALS.md): when a run
on ANY body hits an "ask", the question can be routed to the phone in
Ryan's pocket. The approver POSTs a *safe summary* of the action to
the Ghost Hands bridge inside MrGhosty (``POST /v1/approvals``);
MrGhosty raises a high-importance notification and an in-app card,
and Ryan taps Approve or Deny there. The approver polls
``GET /v1/approvals/<id>`` until the phone decides, the approval
expires, or the local timeout runs out.

Two rules are structural, not policy:

- **The bridge API cannot decide.** There is deliberately NO decision
  endpoint — a request is created over the wire, but the only way to
  approve or deny it is a finger on the phone's own UI (notification
  actions or the in-app card). Holding the pairing token lets you
  *ask*; it never lets you *answer*.
- **Summaries are redacted.** The payload carries the action kind,
  its classification, the target descriptor (tag / role / name), the
  current URL/package, field NAMES for fill_form — and for ``type``
  actions only the target plus a character count. Typed text and form
  VALUES never leave this process; they may be passwords.

Approved -> True. Denied, expired, timed out, unreachable, or a 401 ->
False: silence and error are never consent, exactly like every other
Ghost Hands channel. The pairing token is sent as
``X-Ghost-Hands-Token`` and never appears in summaries, trail events,
errors, or logs.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Optional

from .actions import Action
from .android_driver import DEFAULT_BRIDGE_URL, _UNREACHABLE_HINT, _is_loopback_host
from .errors import HandsError
from .eyes import Element, ElementMap
from .governor import Governor
from .trail import Trail

#: The test summary Ryan sees when he runs `phone-approval-test`.
TEST_SUMMARY = "Test approval from Ghost Hands — approving this runs nothing"

_SUMMARY_CAP = 280


def build_summary(
    action: Action,
    classification: str,
    element: Optional[Element] = None,
    url: str = "",
) -> str:
    """The SAFE one-line summary shown on the phone.

    Redaction is the point: ``type`` contributes a character count,
    never its text; ``fill_form`` contributes field names, never
    values. Everything else is the action kind, its classification,
    and the target descriptor."""
    kind = action.kind
    parts = [f"{kind} ({classification})"]
    target_desc = None
    if element is not None:
        target_desc = f'<{element.tag}> {element.role} "{element.name}"'
    if kind == "type":
        count = len(action.text or "")
        if target_desc:
            parts.append(f"{count} characters into {target_desc}")
        elif action.target is not None:
            parts.append(f"{count} characters into element [{action.target}]")
        else:
            parts.append(f"{count} characters")
    elif kind == "fill_form":
        names = sorted(str(k) for k in (action.fields or {}).keys())
        segment = "fields: " + (", ".join(names) if names else "(none)")
        if action.submit:
            segment += " — then SUBMIT"
        parts.append(segment)
    elif kind == "navigate":
        parts.append(f"to {action.url or url or '(unknown destination)'}")
    elif kind == "click_at":
        parts.append(f"at ({action.x:g}, {action.y:g})")
    else:
        if target_desc:
            parts.append(f"on {target_desc}")
        elif action.target is not None:
            parts.append(f"on element [{action.target}]")
        if action.url:
            parts.append(f"url {action.url}")
    if url and kind != "navigate":
        parts.append(f"at {url}")
    return " ".join(parts)[:_SUMMARY_CAP]


def target_descriptor(
    action: Action, element: Optional[Element] = None
) -> Optional[dict]:
    """The payload's ``target``: the element's descriptor when known,
    else just the action's target number, else None."""
    if element is not None:
        return {
            "number": element.number,
            "tag": element.tag,
            "role": element.role,
            "name": element.name,
        }
    if action.target is not None:
        return {"number": action.target}
    return None


class ObservingDecider:
    """Decider wrapper that shows each fresh element map to a
    PhoneApprover, so approval summaries can name the target the
    decider was looking at (tag / role / name) instead of just its
    number. The Runner and the decider chain are unchanged."""

    def __init__(self, inner, approver: "PhoneApprover") -> None:
        self.inner = inner
        self.approver = approver

    def decide(self, goal, element_map, history):
        self.approver.observe(element_map)
        return self.inner.decide(goal, element_map, history)


class PhoneApprover:
    """Callable approver: ``approver(action) -> bool``, Runner-ready.

    Bridge URL + pairing token resolve exactly like AndroidDriver's
    (constructor argument, then GHOST_HANDS_ANDROID_BRIDGE /
    GHOST_HANDS_ANDROID_TOKEN, then the loopback default). Non-loopback
    bridge hosts are refused unless ``allow_nonlocal=True`` is passed
    explicitly — the CLI never passes it.
    """

    def __init__(
        self,
        bridge_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 600.0,
        poll_interval: float = 1.0,
        trail: Optional[Trail] = None,
        event_sink: Optional[Callable[[str, int, dict], None]] = None,
        allow_nonlocal: bool = False,
        http_timeout: float = 10.0,
    ) -> None:
        url = (
            bridge_url
            or os.environ.get("GHOST_HANDS_ANDROID_BRIDGE")
            or DEFAULT_BRIDGE_URL
        )
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise HandsError(
                f"invalid Android bridge URL {url!r} — expected "
                "http://127.0.0.1:8378 (an adb-forwarded loopback port)"
            )
        if not allow_nonlocal and not _is_loopback_host(parsed.hostname):
            raise HandsError(
                f"refusing non-loopback Android bridge host "
                f"{parsed.hostname!r}: phone approvals travel over the "
                "loopback-only, token-paired MrGhosty bridge. Forward "
                "it with `adb forward tcp:8378 tcp:8378`, or pass "
                "allow_nonlocal=True if you really mean a LAN bridge."
            )
        self.bridge_url = url.rstrip("/")
        # Token: explicit argument beats the environment. Never logged.
        self._token = token or os.environ.get("GHOST_HANDS_ANDROID_TOKEN")
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)
        self.trail = trail
        self.event_sink = event_sink
        self.http_timeout = float(http_timeout)
        self.last_error: Optional[str] = None
        self.last_status: Optional[str] = None
        #: The SAFE summary of the most recent request (redacted by
        #: construction) — safe to quote in logs and bus comments.
        self.last_summary: Optional[str] = None
        self._last_map: Optional[ElementMap] = None

    # -- context ---------------------------------------------------------

    def observe(self, element_map: ElementMap) -> None:
        """Remember the freshest element map (see ObservingDecider) —
        approval summaries resolve their target descriptor from it."""
        self._last_map = element_map

    def _context(self, action: Action) -> tuple[Optional[Element], str]:
        element = None
        url = ""
        if self._last_map is not None:
            url = self._last_map.url or ""
            if action.target is not None:
                element = self._last_map.get(action.target)
        if not url and self.trail is not None:
            for ev in reversed(self.trail.events):
                if ev.get("type") == "perceive" and ev.get("url"):
                    url = str(ev["url"])
                    break
        return element, url

    def _govern_context(self, action: Action) -> tuple[str, int]:
        """Classification + step, the BusApprover way: the Runner
        records the govern event *before* it calls the approver."""
        if self.trail is not None:
            for ev in reversed(self.trail.events):
                if ev.get("type") == "govern":
                    return str(ev.get("classification", "consequential")), int(
                        ev.get("step", 0)
                    )
        return Governor().classify(action), 0

    # -- trail -------------------------------------------------------------

    def _record(self, step: int, **fields: Any) -> None:
        if self.trail is not None:
            self.trail.record("approval", step, **fields)
        if self.event_sink is not None:
            self.event_sink("approval", step, fields)

    # -- HTTP ----------------------------------------------------------------

    def _request(
        self, method: str, path: str, payload: Optional[dict] = None
    ) -> tuple[int, dict]:
        """One bridge call. Returns (status, json). Transport failures
        and 401s raise HandsError (their text never carries the token);
        other statuses come back for the caller to judge."""
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
            with urllib.request.urlopen(req, timeout=self.http_timeout) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp is not None else b""
            status = exc.code
            if status == 401:
                raise HandsError(
                    "Android bridge rejected the pairing token "
                    "(HTTP 401): token mismatch — the code in "
                    "GHOST_HANDS_ANDROID_TOKEN must match the pairing "
                    "code in MrGhosty (Device tab → Ghost Hands bridge)"
                ) from None
        except (urllib.error.URLError, OSError) as exc:
            cause = getattr(exc, "reason", exc)
            raise HandsError(
                _UNREACHABLE_HINT.format(url=self.bridge_url, cause=cause)
            ) from None
        try:
            out = json.loads(raw.decode("utf-8")) if raw else {}
        except Exception:
            raise HandsError(
                f"Android bridge at {self.bridge_url} returned a "
                "non-JSON response — is something else on that port?"
            ) from None
        return status, out if isinstance(out, dict) else {}

    # -- the core request/poll cycle ------------------------------------------

    def request_decision(
        self,
        kind: str,
        classification: str,
        summary: str,
        target: Optional[dict] = None,
        url: str = "",
        test: bool = False,
        step: int = 0,
    ) -> bool:
        """Create one approval on the phone and wait for its decision.

        Returns True only when the phone says *approved*. Every other
        outcome — denied, expired, local timeout, transport error —
        returns False and is written to the trail (when one is bound)
        as an ``approval`` decision event with channel ``phone``."""
        request_id = uuid.uuid4().hex
        self.last_error = None
        self.last_status = None
        self.last_summary = summary
        self._record(
            step,
            channel="phone",
            phase="request",
            request_id=request_id,
            classification=classification,
            kind=kind,
            summary=summary,
            timeout_s=self.timeout,
        )
        payload = {
            "id": request_id,
            "kind": kind,
            "classification": classification,
            "summary": summary,
            "target": target,
            "url": url,
            # The bridge requires a positive window; a 0s local
            # timeout still posts (BusApprover semantics) and then
            # decides "timeout" immediately below.
            "timeout_seconds": max(1, int(round(self.timeout))),
            "test": bool(test),
        }
        try:
            status, body = self._request("POST", "/v1/approvals", payload)
        except HandsError as exc:
            return self._fail(step, request_id, f"phone approval failed: {exc}", exc)
        if status == 409:
            err = HandsError(
                f"Android bridge refused the approval (HTTP 409): "
                f"duplicate approval id {request_id}"
            )
            return self._fail(step, request_id, str(err), err)
        if status != 201 or body.get("ok") is not True:
            detail = body.get("error") or f"HTTP {status}"
            err = HandsError(
                f"Android bridge refused the approval: {detail}"
            )
            return self._fail(step, request_id, str(err), err)
        if self.timeout <= 0:
            return self._decide(
                step, request_id, False, "timeout",
                f"phone approval timeout ({self.timeout:g}s)",
            )

        deadline = time.time() + self.timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return self._decide(
                    step, request_id, False, "timeout",
                    f"phone approval timeout ({self.timeout:g}s)",
                )
            try:
                status, body = self._request(
                    "GET", f"/v1/approvals/{request_id}"
                )
            except HandsError as exc:
                # A poll that fails mid-wait (401 = token rotated on
                # the phone) ends the wait: it can never be consent.
                return self._fail(
                    step, request_id, f"phone approval failed: {exc}", exc
                )
            if status == 404:
                err = HandsError(
                    "Android bridge lost the approval (HTTP 404 on "
                    "poll) — the request is gone, so it cannot be consent"
                )
                return self._fail(
                    step, request_id, f"phone approval failed: {err}", err
                )
            if status == 200 and body.get("ok") is True:
                state = str(body.get("status") or "")
                if state == "approved":
                    return self._decide(
                        step, request_id, True, "approved",
                        "approved on the phone",
                    )
                if state == "denied":
                    return self._decide(
                        step, request_id, False, "denied",
                        "denied on the phone",
                    )
                if state == "expired":
                    return self._decide(
                        step, request_id, False, "expired",
                        "approval expired on the phone",
                    )
                # "pending" (or anything unrecognized): keep waiting.
            time.sleep(min(self.poll_interval, max(0.0, deadline - time.time())))

    def _decide(
        self, step: int, request_id: str, approved: bool, status: str, reason: str
    ) -> bool:
        self.last_status = status
        self._record(
            step,
            channel="phone",
            phase="decision",
            request_id=request_id,
            approved=approved,
            # The phone produced approved/denied/expired outcomes; a
            # local timeout or a transport error decided nothing.
            decided_by="phone" if status in ("approved", "denied", "expired") else None,
            status=status,
            reason=reason,
        )
        return approved

    def _fail(
        self, step: int, request_id: str, reason: str, exc: HandsError
    ) -> bool:
        self.last_error = str(exc)
        return self._decide(step, request_id, False, "error", reason)

    # -- the Runner-facing callable -------------------------------------------

    def __call__(self, action: Action) -> bool:
        classification, step = self._govern_context(action)
        element, url = self._context(action)
        summary = build_summary(action, classification, element, url)
        return self.request_decision(
            kind=action.kind,
            classification=classification,
            summary=summary,
            target=target_descriptor(action, element),
            url=url,
            step=step,
        )

    # -- the one-command proof ---------------------------------------------------

    def test_approval(self) -> str:
        """Send the harmless test approval and wait. Returns the final
        status word: approved / denied / expired / timeout / error."""
        self.request_decision(
            kind="test",
            classification="test",
            summary=TEST_SUMMARY,
            test=True,
        )
        return self.last_status or "error"
