"""Items 4–6 (v0.2): ChromiumDriver session state, tabs, screenshots —
against local HTTP fixtures on a real Chromium (skipped without one)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ghost_hands import actions as A
from ghost_hands.drivers import ChromiumDriver, find_chrome
from ghost_hands.eyes import ElementMap

CHROME = find_chrome()
needs_chrome = pytest.mark.skipif(CHROME is None, reason="no Chromium binary on this machine")

PAGES = {
    "/": (
        "<!doctype html><html><head><title>State Fixture</title></head><body>"
        "<h1>State fixture</h1><button>State action</button></body></html>"
    ),
    "/one": (
        "<!doctype html><html><head><title>Tab One</title></head><body>"
        "<h1>Tab one</h1><button>Tab One Action</button></body></html>"
    ),
    "/two": (
        "<!doctype html><html><head><title>Tab Two</title></head><body>"
        "<h1>Tab two</h1><button>Tab Two Action</button></body></html>"
    ),
}


@pytest.fixture
def serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGES.get(self.path.split("?", 1)[0])
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@needs_chrome
def test_session_roundtrip(serve, tmp_path):
    path = tmp_path / "session.json"
    d1 = ChromiumDriver(chrome_path=CHROME)
    try:
        d1.open(serve + "/")
        d1.set_cookie("gh_session", "abc123")
        d1.set_local_storage({"gh_key": "gh_value"})
        d1.save_session(path)
    finally:
        d1.close()

    saved = json.loads(Path(path).read_text())
    assert saved["tool"] == "ghost-hands" and saved["version"] == 1
    assert saved["origins"][serve]["localStorage"] == {"gh_key": "gh_value"}
    assert any(c["name"] == "gh_session" for c in saved["cookies"])

    d2 = ChromiumDriver(chrome_path=CHROME)
    try:
        d2.load_session(path)
        assert d2.local_storage().get("gh_key") == "gh_value"
        cookies = {c["name"]: c["value"] for c in d2.get_cookies()}
        assert cookies.get("gh_session") == "abc123"
    finally:
        d2.close()


@needs_chrome
def test_load_session_rejects_foreign_file(tmp_path):
    path = tmp_path / "nope.json"
    path.write_text(json.dumps({"tool": "something-else", "version": 1}))
    d = ChromiumDriver(chrome_path=CHROME)
    try:
        with pytest.raises(Exception, match="not a ghost-hands session file"):
            d.load_session(path)
    finally:
        d.close()


@needs_chrome
def test_tabs(serve):
    d = ChromiumDriver(chrome_path=CHROME)
    try:
        d.open(serve + "/one")
        m = ElementMap.from_html(d.snapshot_html(), url=d.current_url())
        assert m.find_by_text("Tab One Action") is not None
        assert d.open_tab(serve + "/two") == 1
        tabs = d.list_tabs()
        assert len(tabs) == 2 and tabs[1]["active"] is True
        assert "/two" in tabs[1]["url"]
        m = ElementMap.from_html(d.snapshot_html(), url=d.current_url())
        assert m.find_by_text("Tab Two Action") is not None
        d.switch_tab(tabs[0]["target_id"])
        m = ElementMap.from_html(d.snapshot_html(), url=d.current_url())
        assert m.find_by_text("Tab One Action") is not None
        with pytest.raises(Exception, match="no such tab"):
            d.switch_tab(99)
    finally:
        d.close()


@needs_chrome
def test_screenshot_writes_png(serve, tmp_path):
    path = tmp_path / "shot.png"
    d = ChromiumDriver(chrome_path=CHROME)
    try:
        d.open(serve + "/")
        result = d.act(A.screenshot(path=str(path)), None)
        assert "saved" in result
        raw = Path(path).read_bytes()
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(raw) > 500
        assert f"({len(raw)} bytes)" in result
    finally:
        d.close()


@needs_chrome
def test_screenshot_default_path(serve, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = ChromiumDriver(chrome_path=CHROME)
    try:
        d.open(serve + "/")
        d.act(A.screenshot(), None)
        default = tmp_path / "ghost-hands-shot.png"
        assert default.exists() and default.read_bytes()[:4] == b"\x89PNG"
    finally:
        d.close()
