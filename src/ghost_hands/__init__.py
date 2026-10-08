"""Ghost Hands — governed agent hands for the web.

Playwright drives. Stagehand thinks. Browser Use wanders.
Ghost Hands answers for every move.

Zero-dependency core. Our own stdlib CDP client drives Chromium directly —
no Playwright, no Selenium, nothing else's automation stack under the hood.
"""

from .actions import Action
from .deciders import OpenAICompatibleDecider, RuleDecider, ScriptedDecider
from .drivers import ChromiumDriver, FakeDriver
from .errors import HandsError
from .export import export_ghost_hands_script
from .eyes import Element, ElementMap
from .governor import Governor, Policy, classify
from .runner import RunReport, Runner
from .trail import Trail, read_trail

__version__ = "0.3.0"

__all__ = [
    "Action",
    "ChromiumDriver",
    "Element",
    "ElementMap",
    "FakeDriver",
    "Governor",
    "HandsError",
    "OpenAICompatibleDecider",
    "Policy",
    "RuleDecider",
    "RunReport",
    "Runner",
    "ScriptedDecider",
    "Trail",
    "classify",
    "export_ghost_hands_script",
    "read_trail",
    "__version__",
]
