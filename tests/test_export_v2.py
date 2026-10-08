"""Items 3+7 support (v0.2): export parameters — fake-body exports with
embedded pages, chromium exports with a start URL, and the exported fake
script actually executing."""

import ast
import subprocess
import sys

from ghost_hands.drivers import DEMO_PAGES, FakeDriver, demo_steps
from ghost_hands.export import export_ghost_hands_script
from ghost_hands.deciders import ScriptedDecider
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail


def _demo_trail():
    driver = FakeDriver(DEMO_PAGES)
    trail = Trail()
    report = Runner(driver, ScriptedDecider(demo_steps()), trail=trail).run("demo")
    assert report.stop_reason == "done"
    return trail.events


def test_chromium_export_prepends_start_navigation():
    src = export_ghost_hands_script(_demo_trail(), driver="chromium")
    ast.parse(src)
    assert '"kind": "navigate", "url": "https://demo.local/"' in src
    assert "ChromiumDriver" in src


def test_chromium_export_respects_explicit_start_url():
    src = export_ghost_hands_script(
        _demo_trail(), driver="chromium", start_url="https://start.local/x"
    )
    assert '"url": "https://start.local/x"' in src


def test_fake_export_embeds_pages_and_runs(tmp_path):
    src = export_ghost_hands_script(
        _demo_trail(), driver="fake", pages=DEMO_PAGES
    )
    ast.parse(src)
    assert "FakeDriver" in src and "ChromiumDriver" not in src
    script = tmp_path / "replay_fake.py"
    script.write_text(src)
    proc = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    assert "done" in proc.stdout


def test_fake_export_requires_pages():
    import pytest

    with pytest.raises(ValueError, match="pages"):
        export_ghost_hands_script(_demo_trail(), driver="fake")


def test_unknown_driver_rejected():
    import pytest

    with pytest.raises(ValueError, match="unknown export driver"):
        export_ghost_hands_script(_demo_trail(), driver="webkit")
