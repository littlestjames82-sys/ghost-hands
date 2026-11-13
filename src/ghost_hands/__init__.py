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

v0.9.3 is a discoverability patch: the README now carries the
mcp-name ownership marker the official MCP Registry requires for
PyPI packages, so Ghost Hands can be listed there under
io.github.littlestjames82-sys/ghost-hands. No functional changes.

v0.9.4 is a directory-quality patch: the MCP server now publishes
server instructions in the initialize result, and the four tool
descriptions are fuller, written for directory tool-definition
scoring. CI dependency bumps ride along. No tool, classification,
or approval behaviour changes.

v1.0 adds the trust-and-cost release: a tamper-evident trail (every
event hash-chained; ``verify_trail`` / ``ghost-hands verify-trail``
proves a record is untampered), secret placeholders (``{{secret:NAME}}``
resolved from the environment only at the execution boundary — the
model, the approver, and the trail never see a credential), the
``verify`` action (a readonly assertion evaluated against the live
page before a run may claim success), and the governed plan cache
(a run that ends ``done`` stores its proven steps; the next run with
the same goal replays them through the full Governor with no decider
calls, falling back to the live decider if the page changed).

v1.1 adds focused perception (``ElementMap.render_focused`` — when a
page's map exceeds the render budget, elements are scored against
the goal and the relevant ones are kept, under their original
numbers; the OpenAI-compatible decider perceives through it), trail
integrity surfaced everywhere (the HTML report header and the MCP
``hands_trail`` tool both carry the hash-chain verdict), the plan
cache over MCP (``hands_run`` honors ``GHOST_HANDS_CACHE``), and
bench proof cases for the v1.0 features.
"""

__version__ = "1.11.0"

from .actions import Action
from .android_driver import AndroidDriver
from .busagent import BusAgent
from .busclient import BusClient, BusError
from .cache import PlanCache, plan_key
from .deciders import (
    AnthropicDecider,
    FallbackDecider,
    OpenAICompatibleDecider,
    RuleDecider,
    ScriptedDecider,
)
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
from .secrets import SecretProvider
from .simphone import SimPhoneDriver
from .trail import Trail, read_trail, verify_trail
from .verify import evaluate_verify

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
    "AnthropicDecider",
    "FallbackDecider",
    "OpenAICompatibleDecider",
    "PhoneApprover",
    "PlanCache",
    "Policy",
    "PolicyPack",
    "RuleDecider",
    "RunReport",
    "Runner",
    "ScriptedDecider",
    "SecretProvider",
    "SimPhoneDriver",
    "Trail",
    "build_summary",
    "classify",
    "evaluate_verify",
    "export_ghost_hands_script",
    "governor_from_env",
    "load_pack",
    "plan_key",
    "read_trail",
    "run_doctor",
    "run_phone_proof",
    "verify_trail",
    "__version__",
]
