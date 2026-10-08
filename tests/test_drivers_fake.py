import pytest

from ghost_hands import actions as A
from ghost_hands.drivers import DEMO_PAGES, FakeDriver, demo_steps
from ghost_hands.errors import HandsError
from ghost_hands.eyes import ElementMap

from pages_fixtures import LOGIN_PAGES


def map_of(driver):
    return ElementMap.from_html(driver.snapshot_html(), url=driver.current_url())


def test_open_and_snapshot():
    d = FakeDriver(DEMO_PAGES)
    assert d.current_url() == "https://demo.local/"
    assert "Ghost Demo Web" in d.snapshot_html()


def test_link_click_navigates():
    d = FakeDriver(DEMO_PAGES, start_url="https://demo.local/results")
    m = map_of(d)
    a = m.find_by_text("Ghost Hands — our own hands")
    result = d.act(A.click(a.number), a)
    assert "navigated" in result
    assert d.current_url() == "https://demo.local/article"


def test_data_nav_button_navigates():
    d = FakeDriver(DEMO_PAGES)
    m = map_of(d)
    btn = next(e for e in m.elements if e.tag == "button")
    assert btn.name == "Search"
    d.act(A.click(btn.number), btn)
    assert d.current_url() == "https://demo.local/results"


def test_type_stores_field_value():
    d = FakeDriver(DEMO_PAGES, start_url="https://demo.local/todo")
    m = map_of(d)
    box = m.find_by_text("New item")
    d.act(A.type(box.number, "milk"), box)
    assert d.fields["new-item"] == "milk"
    assert d.act(A.extract(box.number), box) == "milk"


def test_todo_append_end_to_end():
    d = FakeDriver(DEMO_PAGES, start_url="https://demo.local/todo")
    m = map_of(d)
    d.act(A.type(m.find_by_text("New item").number, "Write tests"), m.find_by_text("New item"))
    m = map_of(d)
    btn = m.find_by_text("Add item")
    d.act(A.click(btn.number), btn)
    assert "Write tests" in d.snapshot_html()


def test_form_submit_records_and_navigates(run_fake):
    steps = [
        {"kind": "type", "target": 1, "text": "ryan"},
        {"kind": "type", "target": 2, "text": "hunter2"},
        {"kind": "click", "target": 3},
        {"kind": "done", "summary": "in"},
    ]
    driver, _, report = run_fake(LOGIN_PAGES, steps, approver=lambda a: True)
    assert report.stop_reason == "done"
    assert len(driver.submissions) == 1
    assert driver.submissions[0]["fields"]["user"] == "ryan"
    assert driver.current_url() == "https://app.local/home"


def test_select_stores_value():
    pages = {
        "https://f.local/": '<select id="c" name="c"><option>Red</option></select>'
    }
    d = FakeDriver(pages)
    m = map_of(d)
    el = m.elements[0]
    d.act(A.select(el.number, "Red"), el)
    assert d.fields["c"] == "Red"


def test_extract_page_text():
    d = FakeDriver(DEMO_PAGES, start_url="https://demo.local/article")
    text = d.act(A.extract(), None)
    assert "Ghost Hands answers for every move" in text


def test_click_without_element_raises():
    d = FakeDriver(DEMO_PAGES)
    with pytest.raises(HandsError):
        d.act(A.click(1), None)


def test_empty_pages_rejected():
    with pytest.raises(HandsError):
        FakeDriver({})


def test_press_and_scroll_and_wait_results():
    d = FakeDriver(DEMO_PAGES)
    assert d.act(A.press("Enter"), None) == "pressed Enter"
    assert "scrolled" in d.act(A.scroll("down", 300), None)
    assert "waited" in d.act(A.wait(0), None)


def test_demo_steps_shape():
    steps = demo_steps()
    assert steps[0]["kind"] == "type"
    assert steps[-1]["kind"] == "done"
