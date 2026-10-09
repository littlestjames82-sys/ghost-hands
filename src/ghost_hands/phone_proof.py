"""phone-proof — the guided on-device proof (v0.9).

``ghost-hands phone-approval-test`` proves one thing in isolation.
``ghost-hands phone-proof`` walks the whole first-run path with the
human, checking each step instead of just printing it:

1. **Configured?** The pairing token must be set. If not, the exact
   setup is printed and the command stops — exit 2 (needs-setup),
   which is neither success nor failure.
2. **Forward.** When adb and an attached device are present, the
   bridge port is forwarded (`adb forward tcp:8378 tcp:8378` — the
   one mutation this command exists to perform, printed before it
   runs). Without adb/a device the step is skipped; the bridge may
   already be reachable another way.
3. **Status.** The bridge must answer like `android-status`: app,
   version, protocol, accessibility service connected.
4. **Approval.** The harmless test approval travels to the phone;
   the human taps Approve on the MrGhosty notification (or the
   Device-tab card). Denied / expired / timed-out all end the proof
   gracefully with instructions — exit 3 (not-approved).
5. **Summary.** What is now proven on this phone, and the exact next
   commands.

Exit codes: 0 = proven · 1 = error (bridge/config failure) ·
2 = needs-setup · 3 = not approved (denied, expired, or timed out).

The pairing token is read from the argument or
``GHOST_HANDS_ANDROID_TOKEN`` and, as everywhere in Ghost Hands,
never printed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Callable, Optional
from urllib.parse import urlparse

from .android_driver import DEFAULT_BRIDGE_URL, AndroidDriver
from .doctor import adb_attached_devices
from .errors import HandsError
from .phone_approver import PhoneApprover

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NEEDS_SETUP = 2
EXIT_NOT_APPROVED = 3

_SETUP_TEMPLATE = """\
phone-proof needs the phone bridge configured first — do this, then re-run:

  1. Install MrGhosty (v1.7+) on the phone and enable its accessibility
     service (Settings → Accessibility → MrGhosty).
  2. On the phone, open MrGhosty → Device tab → Ghost Hands bridge and
     copy the pairing code.
  3. On this machine:

       export GHOST_HANDS_ANDROID_TOKEN=<the pairing code>
       export GHOST_HANDS_ANDROID_BRIDGE={bridge_url}   # optional — that is the default

  4. Plug the phone in by USB (or forward the bridge port yourself),
     then re-run:  ghost-hands phone-proof"""


def _pause(
    out: Callable[[str], None],
    input_fn: Optional[Callable[[str], str]],
    assume_yes: bool,
) -> None:
    """Pause between steps in a TTY. --yes (or no input function)
    runs straight through; an EOF (piped stdin) never hangs."""
    if assume_yes or input_fn is None:
        return
    try:
        input_fn("  … press Enter to continue ")
    except (EOFError, KeyboardInterrupt):
        pass


def run_phone_proof(
    *,
    bridge_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout: float = 600.0,
    poll_interval: float = 1.0,
    assume_yes: bool = False,
    input_fn: Optional[Callable[[str], str]] = None,
    out: Callable[[str], None] = print,
    which: Callable = shutil.which,
    http_timeout: float = 10.0,
) -> int:
    """Run the five proof steps, printing as it goes. Returns one of
    the EXIT_* codes above. Injectable ``input_fn`` / ``which`` /
    ``out`` keep the flow testable against the fake bridge."""
    say = out
    say("Ghost Hands phone proof — five steps between this machine and your phone.")

    # -- step 1: configured? -------------------------------------------------
    resolved_url = (
        bridge_url
        or os.environ.get("GHOST_HANDS_ANDROID_BRIDGE")
        or DEFAULT_BRIDGE_URL
    )
    resolved_token = token or os.environ.get("GHOST_HANDS_ANDROID_TOKEN")
    if not resolved_token:
        say(_SETUP_TEMPLATE.format(bridge_url=resolved_url))
        return EXIT_NEEDS_SETUP
    try:
        approver = PhoneApprover(
            bridge_url=resolved_url,
            token=resolved_token,
            timeout=timeout,
            poll_interval=poll_interval,
            http_timeout=http_timeout,
        )
    except HandsError as exc:
        say(f"step 1/5 ✗ bridge configuration refused: {exc}")
        return EXIT_ERROR
    say(
        f"step 1/5 ✓ bridge configured: {approver.bridge_url} "
        "(pairing token set — never printed)"
    )
    _pause(say, input_fn, assume_yes)

    # -- step 2: forward the bridge port --------------------------------------
    port = urlparse(approver.bridge_url).port or 8378
    adb = which("adb")
    if not adb:
        say(
            "step 2/5 adb not on PATH — skipping the port forward; "
            "the bridge must already be reachable"
        )
    elif not adb_attached_devices(adb):
        say(
            "step 2/5 no adb device attached — skipping the port "
            "forward; the bridge must already be reachable (e.g. "
            "forwarded by hand)"
        )
    else:
        say(f"step 2/5 running: adb forward tcp:{port} tcp:{port}")
        try:
            proc = subprocess.run(
                [adb, "forward", f"tcp:{port}", f"tcp:{port}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if proc.returncode == 0:
                say("  forwarded ✓")
            else:
                detail = (proc.stderr or "").strip() or f"exit {proc.returncode}"
                say(
                    f"  adb forward failed ({detail}) — continuing; "
                    "the bridge may already be forwarded"
                )
        except Exception as exc:
            say(f"  adb forward failed ({exc}) — continuing")
    _pause(say, input_fn, assume_yes)

    # -- step 3: the bridge answers (android-status equivalent) ----------------
    driver = AndroidDriver(
        bridge_url=approver.bridge_url,
        token=resolved_token,
        timeout=http_timeout,
    )
    try:
        status = driver.status()
    except HandsError as exc:
        say(f"step 3/5 ✗ the bridge did not answer cleanly: {exc}")
        say(
            "fix: phone on, MrGhosty open, accessibility enabled, "
            "bridge forwarded (step 2) — then re-run phone-proof"
        )
        return EXIT_ERROR
    finally:
        driver.close()
    say(
        f"step 3/5 ✓ bridge answers: {status.get('app')} "
        f"{status.get('version')} (protocol {status.get('protocol')}), "
        "accessibility service connected, foreground "
        f"{status.get('foreground_package')}"
    )
    _pause(say, input_fn, assume_yes)

    # -- step 4: the test approval, decided by a finger on the phone -----------
    say("step 4/5 sending the harmless test approval to the phone…")
    say(
        "LOOK AT YOUR PHONE NOW — tap Approve on the MrGhosty "
        "notification (or the card on MrGhosty's Device tab). "
        "Approving runs nothing."
    )
    result = approver.test_approval()
    if approver.last_error:
        say(f"step 4/5 ✗ the approval could not travel: {approver.last_error}")
        return EXIT_ERROR
    if result == "denied":
        say(
            "step 4/5 ✗ denied on the phone — the proof needs an "
            "approval. Nothing ran, exactly as designed."
        )
        return EXIT_NOT_APPROVED
    if result != "approved":
        say(
            f"step 4/5 ✗ no decision arrived ({result}) within "
            f"{timeout:g}s — the request is closed and nothing ran."
        )
        say(
            "fix: keep the phone awake and unlocked, watch for the "
            "MrGhosty notification, then re-run phone-proof"
        )
        return EXIT_NOT_APPROVED
    say("step 4/5 ✓ approved on the phone")
    _pause(say, input_fn, assume_yes)

    # -- step 5: what is now proven ---------------------------------------------
    say("step 5/5 PHONE PROOF COMPLETE — proven on this phone:")
    say(
        f"  • the MrGhosty bridge answers over the pairing token "
        f"(MrGhosty {status.get('version')}, protocol "
        f"{status.get('protocol')})"
    )
    say(
        "  • the accessibility service is connected — the hands can "
        "perceive and act on this phone"
    )
    say(
        "  • a test approval travelled to the phone and was approved "
        "by a finger on the phone — the bridge API cannot decide, "
        "by design"
    )
    say("next:")
    say(
        '  ghost-hands run --driver android --approver phone '
        '--script steps.json --goal "your goal"'
    )
    say("  ghost-hands bus-agent --driver android --approval-channel phone --once")
    return EXIT_OK
