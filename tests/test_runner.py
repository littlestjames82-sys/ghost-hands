from ghost_hands import actions as A
from ghost_hands.deciders import ScriptedDecider
from ghost_hands.drivers import DEMO_PAGES, FakeDriver, demo_steps
from ghost_hands.governor import Policy
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail

from pages_fixtures import SHOP_PAGES

CHECKOUT_STEPS = [
    {"kind": "click", "target": 1},
    {"kind": "click", "target": 1},
    {"kind": "done", "summary": "ordered"},
]


def test_demo_run_completes():
    driver = FakeDriver(DEMO_PAGES)
    report = Runner(driver, ScriptedDecider(demo_steps()), trail=Trail()).run("demo")
    assert report.stop_reason == "done"
    assert report.steps == 8


def test_record_before_execute_ordering():
    driver = FakeDriver(DEMO_PAGES)
    trail = Trail()
    Runner(driver, ScriptedDecider(demo_steps()), trail=trail).run("demo")
    types = [e["type"] for e in trail.events]
    for i, t in enumerate(types):
        if t == "execute":
            assert types[i - 1] == "govern"  # verdict recorded before the act


def test_checkout_denied_without_approver():
    driver = FakeDriver(SHOP_PAGES)
    report = Runner(driver, ScriptedDecider(CHECKOUT_STEPS), trail=Trail()).run("buy")
    assert report.stop_reason == "denied"
    assert report.summary == "approval required, no approver"
    assert driver.submissions == []


def test_checkout_with_approver():
    driver = FakeDriver(SHOP_PAGES)
    report = Runner(
        driver, ScriptedDecider(CHECKOUT_STEPS), approver=lambda a: True, trail=Trail()
    ).run("buy")
    assert report.stop_reason == "done"
    assert len(driver.submissions) == 1


def test_approver_declines():
    driver = FakeDriver(SHOP_PAGES)
    report = Runner(
        driver, ScriptedDecider(CHECKOUT_STEPS), approver=lambda a: False, trail=Trail()
    ).run("buy")
    assert report.stop_reason == "denied"
    assert "declined" in report.summary


def test_domain_allowlist_denies_navigation(run_fake):
    steps = [
        {"kind": "navigate", "url": "https://other.local/"},
        {"kind": "done", "summary": "x"},
    ]
    _, _, report = run_fake(
        DEMO_PAGES, steps, policy=Policy(allowed_domains=("demo.local",))
    )
    assert report.stop_reason == "denied"


def test_budget_stop():
    steps = [{"kind": "extract"}] * 3 + [{"kind": "done", "summary": "late"}]
    driver = FakeDriver(DEMO_PAGES)
    report = Runner(
        driver, ScriptedDecider(steps), trail=Trail(), step_budget=2
    ).run("slow")
    assert report.stop_reason == "budget"


def test_stuck_repeated_action():
    pages = {
        "https://t.local/a": '<button data-nav="https://t.local/b">Toggle</button>',
        "https://t.local/b": '<p>x</p><button data-nav="https://t.local/a">Toggle</button>',
    }
    driver = FakeDriver(pages, start_url="https://t.local/a")
    steps = [{"kind": "click", "target": 1}] * 10
    report = Runner(driver, ScriptedDecider(steps), trail=Trail()).run("toggle")
    assert report.stop_reason == "stuck"


def test_stuck_unchanged_map():
    pages = {"https://n.local/": "<button>Do nothing</button>"}
    driver = FakeDriver(pages)
    steps = [{"kind": "click", "target": 1}] * 10
    report = Runner(driver, ScriptedDecider(steps), trail=Trail()).run("noop")
    assert report.stop_reason == "stuck"


def test_error_stop_on_bad_target():
    pages = {"https://e.local/": "<button>Only</button>"}
    driver = FakeDriver(pages)
    steps = [{"kind": "click", "target": 99}, {"kind": "done", "summary": "x"}]
    report = Runner(driver, ScriptedDecider(steps), trail=Trail()).run("err")
    # target 99 does not exist: FakeDriver gets element=None and raises
    assert report.stop_reason == "error"


def test_report_accounting_fields():
    driver = FakeDriver(DEMO_PAGES)
    report = Runner(driver, ScriptedDecider(demo_steps()), trail=Trail()).run("demo")
    assert report.map_chars > 0
    assert report.est_tokens == round(report.map_chars / 4)
    assert report.actions_by_class.get("write", 0) >= 1


def test_trail_written_to_file(tmp_path):
    path = tmp_path / "trail.jsonl"
    driver = FakeDriver(DEMO_PAGES)
    Runner(driver, ScriptedDecider(demo_steps()), trail_path=str(path)).run("demo")
    lines = path.read_text().strip().splitlines()
    assert any('"type": "stop"' in line for line in lines)
    assert any('"type": "govern"' in line for line in lines)
