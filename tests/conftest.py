import pytest

from ghost_hands.drivers import FakeDriver
from ghost_hands.runner import Runner
from ghost_hands.deciders import ScriptedDecider
from ghost_hands.trail import Trail


@pytest.fixture
def run_fake():
    def _run(pages, steps, approver=None, policy=None, start=None, budget=25):
        driver = FakeDriver(pages, start_url=start)
        trail = Trail()
        runner = Runner(
            driver,
            ScriptedDecider(steps),
            approver=approver,
            policy=policy,
            trail=trail,
            step_budget=budget,
        )
        report = runner.run("test goal")
        return driver, trail, report

    return _run
