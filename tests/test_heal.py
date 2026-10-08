"""Item 3 (v0.2): self-healing targets — descriptor re-find + one retry,
with the heal recorded in the trail."""

import pytest

from ghost_hands import HandsError
from ghost_hands.deciders import ScriptedDecider
from ghost_hands.drivers import FakeDriver
from ghost_hands.eyes import ElementMap
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail

URL = "https://heal.local/todo"
V1 = """<!doctype html><html><head><title>Heal Todo</title></head>
<body>
<input id="new-item" name="new-item" type="text" placeholder="New item">
<button data-append-to="items" data-from-field="new-item">Add item</button>
<ul id="items"><li>Existing item</li></ul>
</body></html>"""
V2 = """<!doctype html><html><head><title>Heal Todo</title></head>
<body>
<button>Dismiss banner</button>
<input id="new-item" name="new-item" type="text" placeholder="New item">
<button data-append-to="items" data-from-field="new-item">Add item</button>
<ul id="items"><li>Existing item</li></ul>
</body></html>"""
GONE = """<!doctype html><html><head><title>Gone</title></head>
<body><p>Nothing here.</p></body></html>"""


def _run(driver, steps):
    trail = Trail()
    report = Runner(driver, ScriptedDecider(steps), trail=trail).run("heal test")
    return trail, report


def test_find_by_descriptor():
    m = ElementMap.from_html(V2)
    el = m.find_by_descriptor({"tag": "input", "role": "textbox", "name": "New item"})
    assert el is not None and el.number == 2
    assert m.find_by_descriptor({"tag": "input", "role": "textbox", "name": "Nope"}) is None


def test_driver_raises_on_stale_target():
    driver = FakeDriver({URL: V2}, start_url=URL)
    stale = ElementMap.from_html(V1).get(1)  # the input, as numbered in V1
    from ghost_hands import actions as A

    with pytest.raises(HandsError, match="target mismatch"):
        driver.act(A.type(1, "x"), stale)


def test_heal_type_after_mutation():
    driver = FakeDriver({URL: V1}, start_url=URL)
    driver.mutate_hook = lambda d: d.pages.__setitem__(URL, V2)
    trail, report = _run(
        driver,
        [
            {"kind": "type", "target": 1, "text": "healed task"},
            {"kind": "done", "summary": "typed"},
        ],
    )
    assert report.stop_reason == "done"
    assert driver.fields.get("new-item") == "healed task"
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1
    assert heals[0]["found"] is True
    assert heals[0]["from_target"] == 1 and heals[0]["to_target"] == 2
    assert "target mismatch" in heals[0]["trigger"]


def test_heal_click_after_mutation():
    driver = FakeDriver({URL: V1}, start_url=URL)
    driver.fields["new-item"] = "seeded item"
    driver.mutate_hook = lambda d: d.pages.__setitem__(URL, V2)
    trail, report = _run(
        driver,
        [{"kind": "click", "target": 2}, {"kind": "done", "summary": "clicked"}],
    )
    assert report.stop_reason == "done"
    assert "seeded item" in driver.snapshot_html()
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1 and heals[0]["to_target"] == 3


def test_heal_retries_only_once():
    # Page keeps mutating: every snapshot swaps V1<->V2, so even the healed
    # target is stale by the time it is acted on. The run must stop with an
    # error after exactly one heal — never loop forever.
    driver = FakeDriver({URL: V1}, start_url=URL)

    def flip(d):
        d.pages[URL] = V2 if d.pages[URL] == V1 else V1
        d.mutate_hook = flip

    driver.mutate_hook = flip
    trail, report = _run(
        driver,
        [
            {"kind": "type", "target": 1, "text": "x"},
            {"kind": "done", "summary": "typed"},
        ],
    )
    assert report.stop_reason == "error"
    assert "after heal" in report.summary
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1


def test_heal_gone_element_stops_with_error():
    driver = FakeDriver({URL: V1}, start_url=URL)
    driver.mutate_hook = lambda d: d.pages.__setitem__(URL, GONE)
    trail, report = _run(
        driver,
        [
            {"kind": "type", "target": 1, "text": "x"},
            {"kind": "done", "summary": "typed"},
        ],
    )
    assert report.stop_reason == "error"
    heals = [e for e in trail.events if e["type"] == "heal"]
    assert len(heals) == 1 and heals[0]["found"] is False


def test_no_heal_event_on_a_clean_run():
    driver = FakeDriver({URL: V1}, start_url=URL)
    trail, report = _run(
        driver,
        [
            {"kind": "type", "target": 1, "text": "clean"},
            {"kind": "done", "summary": "ok"},
        ],
    )
    assert report.stop_reason == "done"
    assert [e for e in trail.events if e["type"] == "heal"] == []
