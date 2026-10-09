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
"""

from .actions import Action
from .android_driver import AndroidDriver
from .busagent import BusAgent
from .busclient import BusClient, BusError
from .deciders import OpenAICompatibleDecider, RuleDecider, ScriptedDecider
from .drivers import ChromiumDriver, FakeDriver
from .errors import HandsError
from .export import export_ghost_hands_script
from .eyes import Element, ElementMap
from .governor import Governor, Policy, classify
from .phone_approver import ObservingDecider, PhoneApprover, build_summary
from .policies import PolicyPack, load_pack
from .runner import RunReport, Runner
from .simphone import SimPhoneDriver
from .trail import Trail, read_trail

__version__ = "0.6.0"

__all__ = [
    "Action",
    "AndroidDriver",
    "BusAgent",
    "BusClient",
    "BusError",
    "ChromiumDriver",
    "Element",
    "ElementMap",
    "FakeDriver",
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
    "load_pack",
    "read_trail",
    "__version__",
]
