"""v0.3 vocabulary: action round-trips, governor classification of the new
kinds, structured-extract parsing, labels, and the FakeDriver's v0.3
semantics (fill_form / set_file / conditioned wait / download / click_at).
"""

import json

import pytest

from ghost_hands import actions as A
from ghost_hands.actions import Action
from ghost_hands.cli import main as cli_main
from ghost_hands.drivers import FakeDriver
from ghost_hands.errors import HandsError
from ghost_hands.eyes import ElementMap, extract_links, extract_table
from ghost_hands.governor import Governor, Policy, classify
from ghost_hands.trail import Trail


# ---------------------------------------------------------------------------
# Action vocabulary
# ---------------------------------------------------------------------------


def test_action_roundtrip_new_kinds():
    samples = [
        A.hover(3),
        A.double_click(3),
        A.right_click(3),
        A.drag(2, 5),
        A.click_at(120, 340),
        A.fill_form({"Email": "a@b.c"}, submit=True),
        A.set_file(4, "/tmp/x.pdf"),
        A.download(target=2),
        A.download(url="https://x.local/f.bin"),
        A.pdf("/tmp/page.pdf"),
        A.set_viewport("mobile"),
        A.set_viewport(width=800, height=600),
        A.wait_for(text="Loaded", timeout=3),
        A.wait_for(target=7),
        A.extract(mode="table"),
        A.extract(2, mode="list"),
        A.press("Control+a"),
    ]
    for action in samples:
        assert Action.from_dict(action.to_dict()) == action, action


def test_action_rejects_unknown_extract_mode():
    with pytest.raises(ValueError):
        Action(kind="extract", mode="everything")


def test_fill_form_submit_only_serialized_when_true():
    assert "submit" not in A.fill_form({"A": "1"}).to_dict()
    assert A.fill_form({"A": "1"}, submit=True).to_dict()["submit"] is True


# ---------------------------------------------------------------------------
# Governor classification of the v0.3 kinds
# ---------------------------------------------------------------------------


def _el(html, text):
    m = ElementMap.from_html(html)
    el = m.find_by_text(text)
    assert el is not None
    return el


def test_cls_download_is_write():
    assert classify(A.download(url="https://x.local/f.bin")) == "write"
    link = _el('<a href="https://x.local/f.bin">Get file</a>', "Get file")
    assert classify(A.download(target=link.number), link) == "write"


def test_cls_click_at_is_write_and_raw_is_a_trail_concern():
    assert classify(A.click_at(10, 10)) == "write"


def test_cls_fill_form_write_submit_consequential():
    assert classify(A.fill_form({"Name": "Ryan"})) == "write"
    assert classify(A.fill_form({"Name": "Ryan"}, submit=True)) == "consequential"
    gov = Governor()
    d = gov.evaluate(A.fill_form({"Name": "Ryan"}, submit=True))
    assert d.outcome == "ask" and d.classification == "consequential"


def test_cls_set_file_write():
    el = _el('<input type="file" name="attachment">', "attachment")
    assert classify(A.set_file(el.number, "/tmp/a.txt"), el) == "write"


def test_cls_readonly_new_kinds():
    assert classify(A.pdf()) == "readonly"
    assert classify(A.set_viewport("mobile")) == "readonly"
    el = _el("<button>Menu</button>", "Menu")
    assert classify(A.hover(el.number), el) == "readonly"
    assert classify(A.wait_for(text="Loaded")) == "readonly"


def test_cls_drag_write_double_click_like_click():
    src = _el("<button>Card</button>", "Card")
    assert classify(A.drag(src.number, 9), src) == "write"
    link = _el('<a href="https://x.local/about">About</a>', "About")
    assert classify(A.double_click(link.number), link) == "readonly"
    danger = _el("<button>Delete forever</button>", "Delete forever")
    assert classify(A.double_click(danger.number), danger) == "consequential"


# ---------------------------------------------------------------------------
# Eyes: labels + structured extract helpers
# ---------------------------------------------------------------------------

FORM_HTML = """<!doctype html><html><body>
<form action="/save">
<label for="fn">Full name</label>
<input id="fn" name="fullname" type="text">
<label>Email address <input name="email" type="email"></label>
<input name="city" type="text" placeholder="City">
</form>
</body></html>"""


def test_labels_parsed_for_and_wrapping():
    m = ElementMap.from_html(FORM_HTML)
    by_name = {el.attr_name: el for el in m.elements}
    assert by_name["fullname"].label == "Full name"
    assert by_name["email"].label == "Email address"
    assert by_name["city"].label is None


def test_extract_links_exact():
    html = (
        '<a href="/one">First</a><p>x</p>'
        '<a href="https://x.local/two"> Second <b>link</b> </a>'
        "<a>No href</a>"
    )
    assert extract_links(html) == [
        {"text": "First", "href": "/one"},
        {"text": "Second link", "href": "https://x.local/two"},
        {"text": "No href", "href": None},
    ]


def test_extract_table_exact_with_headers():
    html = """<table>
    <tr><th>Name</th><th>City</th><th>Zip</th></tr>
    <tr><td>Alpha</td><td>Oakdale</td><td>37829</td></tr>
    <tr><td>Beta</td><td>Harriman</td><td>37748</td></tr>
    <tr><td>Gamma</td><td>Kingston</td><td>37763</td></tr>
    </table>"""
    assert extract_table(html) == [
        {"Name": "Alpha", "City": "Oakdale", "Zip": "37829"},
        {"Name": "Beta", "City": "Harriman", "Zip": "37748"},
        {"Name": "Gamma", "City": "Kingston", "Zip": "37763"},
    ]


def test_extract_table_without_headers():
    html = "<table><tr><td>a</td><td>b</td></tr><tr><td>c</td><td>d</td></tr></table>"
    assert extract_table(html) == [
        {"col1": "a", "col2": "b"},
        {"col1": "c", "col2": "d"},
    ]


# ---------------------------------------------------------------------------
# FakeDriver v0.3 semantics
# ---------------------------------------------------------------------------

FILL_PAGES = {
    "https://form.local/": """<!doctype html><html><body>
<form action="https://form.local/done">
<label for="fn">Full name</label>
<input id="fn" name="fullname" type="text">
<label>Email address <input name="email" type="email"></label>
<input name="city" type="text" placeholder="City">
<button type="submit">Save profile</button>
</form>
</body></html>""",
    "https://form.local/done": "<html><body><h1>Saved</h1></body></html>",
}


def test_fake_fill_form_values_and_failures(run_fake):
    steps = [
        {
            "kind": "fill_form",
            "fields": {
                "Full name": "Ryan Cotten",
                "Email": "ryan@example.com",
                "City": "Oakdale",
                "No such field": "x",
            },
        },
        {"kind": "done", "summary": "filled"},
    ]
    driver, _, report = run_fake(FILL_PAGES, steps)
    assert report.stop_reason == "done", report.summary
    result = report.results[0]
    assert "filled 3/4 fields" in result, result
    assert "'No such field' FAILED" in result, result
    assert driver.fields.get("fullname") == "Ryan Cotten"
    assert driver.fields.get("email") == "ryan@example.com"
    assert driver.fields.get("city") == "Oakdale"


def test_fake_fill_form_submit_goes_through_governor(run_fake):
    steps = [
        {"kind": "fill_form", "fields": {"City": "Oakdale"}, "submit": True},
        {"kind": "done", "summary": "saved"},
    ]
    # No approver: the consequential submit must be denied before acting.
    driver, _, report = run_fake(FILL_PAGES, steps)
    assert report.stop_reason == "denied", report.summary
    assert driver.submissions == []
    # With an approver it fills and submits.
    driver, _, report = run_fake(FILL_PAGES, steps, approver=lambda a: True)
    assert report.stop_reason == "done", report.summary
    assert len(driver.submissions) == 1
    assert "form submitted" in report.results[0]


def test_fake_set_file_and_refusals(tmp_path):
    driver = FakeDriver(
        {"https://up.local/": '<input type="file" name="attachment">'},
        start_url="https://up.local/",
    )
    real = tmp_path / "notes.txt"
    real.write_bytes(b"hello hands")
    m = ElementMap.from_html(driver.snapshot_html(), url=driver.current_url())
    el = m.elements[0]
    out = driver.act(A.set_file(el.number, str(real)), el)
    assert "notes.txt (11 bytes)" in out
    assert driver.fields.get("attachment") == "file:notes.txt (11 bytes)"
    with pytest.raises(HandsError):
        driver.act(A.set_file(el.number, str(tmp_path)), el)  # a directory
    with pytest.raises(HandsError):
        driver.act(A.set_file(el.number, str(tmp_path / "missing.bin")), el)


def test_fake_wait_for_satisfied_and_timeout():
    driver = FakeDriver(DEMO := {"https://w.local/": "<html><body><p>Ready now</p><button>Go</button></body></html>"}, start_url="https://w.local/")
    out = driver.act(A.wait_for(text="Ready now", timeout=2), None)
    assert "wait satisfied" in out
    with pytest.raises(HandsError) as excinfo:
        driver.act(A.wait_for(text="Never appears", timeout=0.3), None)
    assert "wait timed out" in str(excinfo.value)


def test_fake_download_sinks_event_and_reports():
    pages = {
        "https://dl.local/": '<a href="https://dl.local/report.txt">Download report</a>',
        "https://dl.local/report.txt": "quarterly numbers",
    }
    driver = FakeDriver(pages, start_url="https://dl.local/")
    sunk = []
    driver.event_sink = lambda etype, fields: sunk.append((etype, fields))
    m = ElementMap.from_html(driver.snapshot_html(), url=driver.current_url())
    out = driver.act(A.download(target=1), m.elements[0])
    assert "report.txt (17 bytes)" in out
    assert sunk and sunk[0][0] == "download"
    assert sunk[0][1]["filename"] == "report.txt"
    assert sunk[0][1]["bytes"] == 17


def test_fake_extract_modes():
    page = (
        "<html><body>"
        '<a href="/a">Alpha</a><a href="/b">Beta</a>'
        "<table><tr><th>N</th></tr><tr><td>1</td></tr><tr><td>2</td></tr></table>"
        "</body></html>"
    )
    driver = FakeDriver({"https://x.local/": page}, start_url="https://x.local/")
    links = json.loads(driver.act(A.extract(mode="list"), None))
    assert links == [
        {"text": "Alpha", "href": "/a"},
        {"text": "Beta", "href": "/b"},
    ]
    rows = json.loads(driver.act(A.extract(mode="table"), None))
    assert rows == [{"N": "1"}, {"N": "2"}]
    net = json.loads(driver.act(A.extract(mode="network"), None))
    assert net[0]["url"] == "https://x.local/" and net[0]["status"] == 200


def test_fake_click_at_and_input_extras():
    driver = FakeDriver(
        {"https://c.local/": "<button>Press me</button>"},
        start_url="https://c.local/",
    )
    out = driver.act(A.click_at(33, 44), None)
    assert "raw coordinates" in out
    m = ElementMap.from_html(driver.snapshot_html(), url=driver.current_url())
    el = m.elements[0]
    assert "hovered" in driver.act(A.hover(el.number), el)
    assert "double-clicked" in driver.act(A.double_click(el.number), el)
    assert "right-clicked" in driver.act(A.right_click(el.number), el)


def test_runner_marks_click_at_raw(run_fake):
    pages = {"https://c.local/": "<button>Press me</button>"}
    _, trail, report = run_fake(
        pages,
        [{"kind": "click_at", "x": 10, "y": 20}, {"kind": "done", "summary": "ok"}],
    )
    assert report.stop_reason == "done"
    executes = [e for e in trail.events if e["type"] == "execute"]
    assert executes[0].get("raw") is True


def test_runner_extract_result_carries_parsed_data(run_fake):
    page = "<html><body><table><tr><th>N</th></tr><tr><td>7</td></tr></table></body></html>"
    _, trail, report = run_fake(
        {"https://t.local/": page},
        [{"kind": "extract", "mode": "table"}, {"kind": "done", "summary": "got it"}],
    )
    assert report.stop_reason == "done"
    results = [e for e in trail.events if e["type"] == "result" and e.get("data")]
    assert results and results[0]["data"] == [{"N": "7"}]
    assert results[0]["mode"] == "table"


def test_cli_run_accepts_new_action_kinds(tmp_path):
    page = (
        "<html><body>"
        '<input id="new-item" name="new-item" type="text" placeholder="New item">'
        '<button data-append-to="items" data-from-field="new-item">Add item</button>'
        '<ul id="items"><li>Existing item</li></ul>'
        "</body></html>"
    )
    script = tmp_path / "steps.json"
    script.write_text(
        json.dumps(
            {
                "pages": {"https://c.local/": page},
                "start": "https://c.local/",
                "steps": [
                    {"kind": "set_viewport", "value": "mobile"},
                    {"kind": "type", "target": 1, "text": "from the CLI"},
                    {"kind": "click", "target": 2},
                    {"kind": "extract", "mode": "network"},
                    {"kind": "click_at", "x": 5, "y": 6},
                    {"kind": "done", "summary": "cli ok"},
                ],
            }
        ),
        encoding="utf-8",
    )
    trail_path = tmp_path / "trail.jsonl"
    rc = cli_main(
        [
            "run",
            "--script",
            str(script),
            "--driver",
            "fake",
            "--trail",
            str(trail_path),
        ]
    )
    assert rc == 0
    kinds = [
        json.loads(line)["action"]["kind"]
        for line in trail_path.read_text(encoding="utf-8").splitlines()
        if '"execute"' in line
    ]
    assert kinds == ["set_viewport", "type", "click", "extract", "click_at"]
