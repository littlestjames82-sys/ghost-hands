"""Ghost Hands — governed agent hands for the web.

Playwright drives. Stagehand thinks. Browser Use wanders.
Ghost Hands answers for every move.

Zero-dependency core. Our own stdlib CDP client drives Chromium directly —
no Playwright, no Selenium, nothing else's automation stack under the hood.

v0.4 adds: the GhostBus agent mode (hands as a bus agent, approvals
routed over the bus), the Body Protocol with a simulated phone body
(SimPhoneDriver), and named policy packs (readonly / standard / strict).

v0.5 adds: the real Android body — AndroidDriver speaking HTTP/JSON to
the Ghost Hands bridge inside MrGhosty v1.6+ on the phone (loopback,
pairing-token authenticated), so the same Runner/Governor drive a real
phone through its accessibility service.

v0.6 adds: approvals by phone — PhoneApprover routes any run's "ask"
verdicts to Ryan's phone through the MrGhosty bridge (MrGhosty v1.7+):
a notification + in-app card decide, the bridge API itself can never
decide, and approval summaries are redacted by construction.

v0.7 adds: the model evaluation harness (``ghost-hands eval`` — eight
graded tasks, a deterministic stub model server for harness proof, the
RuleDecider baseline, and real-model runs from the environment);
Android gesture vocabulary on the bridge (double_click / drag /
click_at via dispatchGesture, MrGhosty v1.8.0); and the GhostBus
file-store loop closed (the demo + an agent/poller barrage run
against a real file-backed GhostBus, zero 400s).

v0.8 adds: the trail → HTML audit report (``ghost-hands report`` —
one self-contained, script-free page per run: header, stat cards,
timeline, approval outcomes); MCP parity (the server body and its
approval channel are env-selected: GHOST_HANDS_DRIVER /
GHOST_HANDS_START_URL / GHOST_HANDS_APPROVER=phone); and the
seatbelt policy pack (standard's posture plus outright denial of
prompt-injection-shaped targets, via Policy.deny_patterns).

v0.9 adds: the field kit — ``ghost-hands doctor`` (one-command
diagnostics for the whole stack: runtime, Chromium, policy packs,
model env, GhostBus, the phone bridge, adb — PASS/WARN/FAIL rows
with plain-language fixes, ``--json`` for tooling, no secret ever
printed) and ``ghost-hands phone-proof`` (the guided on-device
proof: configuration, adb forward, bridge status, the harmless
test approval, and a summary of what is now proven — exit codes
0 proven / 1 error / 2 needs-setup / 3 not approved).

v0.9.1 is a hardening patch (see HARDENING_091.md): replay-export
comments can no longer be broken out of by page-derived text; the
Governor's domain lists now cover a target's own destination;
Chromium refuses non-http(s) navigation schemes (file:// and
friends); the MCP server survives hostile message shapes; the bus
client validates its URL/workspace and caps replies; every HTTP
client caps response bodies; and trail + session files are
created owner-only (0600).

v0.9.2 is a metadata patch: every tool the MCP server serves now
declares the full annotation hint set (readOnlyHint /
destructiveHint / idempotentHint / openWorldHint), as flagged by
the M8ven Trust Index listing review, so MCP hosts can warn users
before invoking the state-changing tools. No functional changes.
"""

from .actions import Action
from .android_driver import AndroidDriver
from .busagent import BusAgent
from .busclient import BusClient, BusError
from .deciders import OpenAICompatibleDecider, RuleDecider, ScriptedDecider
from .doctor import Check, run_doctor
from .drivers import ChromiumDriver, FakeDriver
from .errors import HandsError
from .export import export_ghost_hands_script
from .eyes import Element, ElementMap
from .ghostguard import GhostGuardBridgeError, GhostGuardGovernor, governor_from_env
from .governor import Governor, Policy, classify
from .phone_approver import ObservingDecider, PhoneApprover, build_summary
from .phone_proof import run_phone_proof
from .policies import PolicyPack, load_pack
from .runner import RunReport, Runner
from .simphone import SimPhoneDriver
from .trail import Trail, read_trail

__version__ = "0.9.2"

__all__ = [
    "Action",
    "AndroidDriver",
    "BusAgent",
    "BusClient",
    "BusError",
    "Check",
    "ChromiumDriver",
    "Element",
    "ElementMap",
    "FakeDriver",
    "GhostGuardBridgeError",
    "GhostGuardGovernor",
    "Governor",
    "HandsError",
    "ObservingDecider",
    "OpenAICompatibleDecider",
    "PhoneApprover",
    "Policy",
    "PolicyPack",
    "RuleDecider",
    "RunReport",
    "Runner",
    "ScriptedDecider",
    "SimPhoneDriver",
    "Trail",
    "build_summary",
    "classify",
    "export_ghost_hands_script",
    "governor_from_env",
    "load_pack",
    "read_trail",
    "run_doctor",
    "run_phone_proof",
    "__version__",
]
