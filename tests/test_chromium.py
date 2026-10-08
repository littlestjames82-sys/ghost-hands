import json
import urllib.parse

import pytest

from ghost_hands import actions as A
from ghost_hands.drivers import (
    CDPMessageBuffer,
    ChromiumDriver,
    encode_cdp_message,
    find_chrome,
)
from ghost_hands.eyes import ElementMap

CHROME = find_chrome()
needs_chrome = pytest.mark.skipif(CHROME is None, reason="no Chromium binary on this machine")


def test_encode_framing():
    msg = {"id": 3, "method": "Runtime.enable", "params": {}}
    framed = encode_cdp_message(msg)
    assert framed.endswith(b"\x00")
    assert json.loads(framed[:-1].decode()) == msg


def test_buffer_reassembles_split_frames():
    buf = CDPMessageBuffer()
    a = encode_cdp_message({"id": 1, "result": {}})
    b = encode_cdp_message({"id": 2, "result": {}})
    assert buf.feed(a[:4]) == []
    out = buf.feed(a[4:] + b)
    assert [m["id"] for m in out] == [1, 2]


def test_buffer_ignores_empty_frames():
    buf = CDPMessageBuffer()
    assert buf.feed(b"\x00\x00") == []


@needs_chrome
def test_find_chrome_local():
    assert CHROME is not None
    assert find_chrome(CHROME) == CHROME
    assert find_chrome("/no/such/browser") is None


@needs_chrome
def test_live_chrome_smoke():
    fixture = (
        "<!doctype html><html><head><title>Fixture</title></head><body>"
        '<input id="q" type="text" placeholder="Search the fixture">'
        '<button id="go" onclick="document.body.setAttribute('
        "'data-clicked','yes')\">Go now</button>"
        "</body></html>"
    )
    url = "data:text/html," + urllib.parse.quote(fixture)
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        assert driver.transport == "pipe"
        driver.open(url)
        m = ElementMap.from_html(driver.snapshot_html(), url=driver.current_url())
        names = [e.name for e in m.elements]
        assert any("Search the fixture" in n for n in names)
        box = m.find_by_text("Search the fixture")
        btn = m.find_by_text("Go now")
        driver.act(A.type(box.number, "ghost hands"), box)
        assert driver.act(A.extract(box.number), box) == "ghost hands"
        driver.act(A.click(btn.number), btn)
        assert 'data-clicked="yes"' in driver.snapshot_html()
    finally:
        driver.close()
