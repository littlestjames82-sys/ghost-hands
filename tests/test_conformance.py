"""Body Protocol conformance (v0.4, docs/BODY_PROTOCOL.md).

ONE abstract scenario — open the notes surface, start a new note, type
"hello ghost", save it, then attempt the destructive action — expressed
as intents resolved by element *name* against each body's live map, run
against all three bodies: FakeDriver (fake web), ChromiumDriver (real
Chromium, the same HTML over local HTTP, with the Save button carrying
a real onclick the fake body ignores), SimPhoneDriver (sim phone).

The contract points asserted identically for every body:
1. perceive() yields a numbered ElementMap (numbers 1..N, gapless);
2. through the Runner + Governor, readonly/write actions execute and
   the consequential action is DENIED with no approver (nothing is
   destroyed) — and executes with one;
3. the trail records the same event shapes in the same order
   (perceive -> decide -> govern -> execute -> result per step, govern
   events carrying classification + outcome; driver-originated events
   like `net` may interleave on real bodies).
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ghost_hands import actions as A
from ghost_hands.deciders import Decider
from ghost_hands.drivers import ChromiumDriver, FakeDriver, find_chrome
from ghost_hands.eyes import ElementMap
from ghost_hands.errors import HandsError
from ghost_hands.governor import Governor
from ghost_hands.runner import Runner
from ghost_hands.simphone import SimPhoneDriver
from ghost_hands.trail import Trail

CHROME = find_chrome()
needs_chrome = pytest.mark.skipif(CHROME is None, reason="no Chromium binary")

# The web fixture mirrors the phone's Notes app: a "Notes" entry, a
# "New note" button, a "Note text" field, a "Save" button, and a
# consequential "Delete all notes" submit. The Save button carries both
# FakeDriver data-* semantics and a real onclick for Chromium.
HOME_HTML = """<!doctype html><html><head><title>Home</title></head>
<body><h1>Home</h1><a href="/notes">Notes</a></body></html>"""

_SAVE_ONCLICK = (
    "var v=document.getElementById('note').value;"
    "var li=document.createElement('li');li.textContent=v;"
    "document.getElementById('items').appendChild(li);"
)

NOTES_HTML = f"""<!doctype html><html><head><title>Notes</title></head>
<body><h1>Notes</h1>
<textarea id="note" placeholder="Note text"></textarea>
<button id="new">New note</button>
<button id="save" data-append-to="items" data-from-field="note" onclick="{_SAVE_ONCLICK}">Save</button>
<ul id="items"></ul>
<form action="/cleared"><button type="submit">Delete all notes</button></form>
</body></html>"""

CLEARED_HTML = """<!doctype html><html><head><title>Cleared</title></head>
<body><h1>All notes deleted</h1></body></html>"""

FAKE_PAGES = {
    "https://conf.local/": HOME_HTML.replace(
        'href="/notes"', 'href="https://conf.local/notes"'
    ),
    "https://conf.local/notes": NOTES_HTML.replace(
        'action="/cleared"', 'action="https://conf.local/cleared"'
    ),
    "https://conf.local/cleared": CLEARED_HTML,
}


def serve(pages_by_path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib handler API
            body = pages_by_path.get(self.path.split("?", 1)[0])
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


# The abstract scenario: (verb, element-name fragment, text?)
SCENARIO = [
    ("click", "Notes", None),
    ("click", "New note", None),
    ("type", "Note text", "hello ghost"),
    ("click", "Save", None),
    ("click", "Delete all", None),
]


class IntentDecider(Decider):
    """Plays the abstract scenario, resolving each intent's target by
    element name from the live map at decision time — the scenario is
    expressed once, the numbers come from each body."""

    def __init__(self, scenario):
        self._steps = list(scenario)
        self._i = 0

    def decide(self, goal, element_map, history):
        if self._i >= len(self._steps):
            return A.done("scenario complete")
        verb, name, text = self._steps[self._i]
        self._i += 1
        el = element_map.find_by_text(name)
        if el is None:
            raise HandsError(f"scenario target {name!r} not on this map")
        if verb == "click":
            return A.click(el.number)
        if verb == "type":
            return A.type(el.number, text or "")
        raise HandsError(f"unknown scenario verb {verb!r}")


def run_scenario(driver, approver, start_url):
    trail = Trail()
    runner = Runner(
        driver,
        IntentDecider(SCENARIO),
        governor=Governor(),
        approver=approver,
        trail=trail,
        start_url=start_url,
    )
    report = runner.run("conformance: notes scenario")
    return report, trail


def perceive_of(driver, start_url):
    if start_url:
        driver.open(start_url)
    perceive = getattr(driver, "perceive", None)
    if callable(perceive):
        m = perceive()
        if m is not None:
            return m
    return ElementMap.from_html(driver.snapshot_html(), url=driver.current_url())


def assert_contract(driver, start_url, saved_check, deleted_check):
    # Contract 1: perception is a gapless numbered map.
    m = perceive_of(driver, start_url)
    assert len(m) >= 1
    assert [el.number for el in m.elements] == list(range(1, len(m) + 1))

    # Contract 2a: without an approver the destructive step is denied
    # and nothing is destroyed — but the earlier write really landed.
    report, trail = run_scenario(driver, approver=None, start_url=start_url)
    assert report.stop_reason == "denied", report.summary
    govern = [ev for ev in trail.events if ev["type"] == "govern"]
    assert govern[-1]["classification"] == "consequential"
    assert govern[-1]["outcome"] == "deny"
    assert saved_check(), "the note typed before the denial must be saved"

    # Contract 3: the trail's core events appear in protocol order.
    types = [ev["type"] for ev in trail.events]
    order = [
        types.index(t)
        for t in ("perceive", "decide", "govern", "execute", "result")
    ]
    assert order == sorted(order), f"trail event order broken: {types[:12]}"
    for ev in govern:
        assert "classification" in ev and "outcome" in ev

    # Contract 2b: with an approver the whole scenario completes and
    # the destructive action really happens (fresh start from home).
    report2, _trail2 = run_scenario(
        driver, approver=lambda a: True, start_url=start_url
    )
    assert report2.stop_reason == "done", report2.summary
    assert deleted_check(), "the approved delete must actually happen"


def test_conformance_fake_driver():
    driver = FakeDriver(FAKE_PAGES, start_url="https://conf.local/")
    assert_contract(
        driver,
        start_url="https://conf.local/",
        saved_check=lambda: "hello ghost" in driver.snapshot_html(),
        deleted_check=lambda: len(driver.submissions) == 1,
    )


def test_conformance_simphone():
    driver = SimPhoneDriver()
    assert_contract(
        driver,
        start_url="phone://home",
        saved_check=lambda: driver.notes == ["hello ghost"],
        deleted_check=lambda: driver.notes == [],
    )


@needs_chrome
def test_conformance_chromium_driver():
    server, base = serve(
        {"/": HOME_HTML, "/notes": NOTES_HTML, "/cleared": CLEARED_HTML}
    )
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        assert_contract(
            driver,
            start_url=base + "/",
            saved_check=lambda: "hello ghost" in driver.snapshot_html(),
            deleted_check=lambda: "/cleared" in driver.current_url(),
        )
    finally:
        driver.close()
        server.shutdown()
