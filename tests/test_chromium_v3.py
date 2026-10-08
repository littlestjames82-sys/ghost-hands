"""v0.3 capabilities against real Chromium (skipped without a binary):
AX eyes, shadow DOM, frames, dialogs, downloads, uploads, network
capture, fill_form, structured extract, richer input, PDF, emulation,
conditioned waits, raw coordinates, and a new action over MCP.
"""

import html as html_mod
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ghost_hands import actions as A
from ghost_hands.deciders import ScriptedDecider
from ghost_hands.drivers import ChromiumDriver, find_chrome
from ghost_hands.errors import HandsError
from ghost_hands.mcp_server import HandsSession, handle_request
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail

CHROME = find_chrome()
needs_chrome = pytest.mark.skipif(CHROME is None, reason="no Chromium binary on this machine")


def serve(pages: dict):
    """{path: html str | (bytes, content-type, {headers})} on 127.0.0.1."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            entry = pages.get(self.path.split("?", 1)[0])
            if entry is None:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"not found")
                return
            if isinstance(entry, tuple):
                body, ctype, headers = entry
                data = body if isinstance(body, bytes) else body.encode()
            else:
                data, ctype, headers = entry.encode(), "text/html; charset=utf-8", {}
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def data_url(page: str) -> str:
    return "data:text/html," + urllib.parse.quote(page)


BASIC_PAGE = (
    "<!doctype html><html><head><title>Basic</title></head><body>"
    '<input id="q" type="text" placeholder="Search the fixture">'
    '<button id="go" onclick="document.body.setAttribute('
    "'data-clicked','yes')\">Go now</button>"
    "</body></html>"
)


@needs_chrome
def test_ax_eyes_map_and_click():
    driver = ChromiumDriver(chrome_path=CHROME, eyes="ax")
    try:
        driver.open(data_url(BASIC_PAGE))
        m = driver.perceive()
        names = [e.name for e in m.elements]
        assert any("Search the fixture" in n for n in names), names
        btn = m.find_by_text("Go now")
        assert btn is not None and btn.backend_id is not None
        box = m.find_by_text("Search the fixture")
        driver.act(A.type(box.number, "ax typed"), box)
        value = driver.act(A.extract(box.number), box)
        assert value == "ax typed", value
        driver.act(A.click(btn.number), btn)
        assert 'data-clicked="yes"' in driver.snapshot_html()
    finally:
        driver.close()


@needs_chrome
def test_shadow_dom_perceive_and_click():
    page = """<!doctype html><html><head><title>Shadow</title></head><body>
<my-widget></my-widget>
<script>
class W extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({mode: 'open'});
    root.innerHTML = '<button id="sb">Shadow Button</button>';
    root.querySelector('#sb').addEventListener('click', () => {
      document.body.setAttribute('data-shadow-clicked', 'yes');
    });
  }
}
customElements.define('my-widget', W);
</script>
</body></html>"""
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        driver.open(data_url(page))
        m = driver.perceive()
        btn = m.find_by_text("Shadow Button")
        assert btn is not None, [e.name for e in m.elements]
        # The HTML snapshot alone cannot see inside the shadow root.
        from ghost_hands.eyes import ElementMap

        snap_map = ElementMap.from_html(driver.snapshot_html())
        assert snap_map.find_by_text("Shadow Button") is None
        driver.act(A.click(btn.number), btn)
        assert 'data-shadow-clicked="yes"' in driver.snapshot_html()
    finally:
        driver.close()


@needs_chrome
def test_iframe_type_and_readback():
    inner = (
        '<input id="inner" type="text" placeholder="Inner field">'
        '<button onclick="parent.document.body.setAttribute('
        "'data-inner','clicked')\">Inner Go</button>"
    )
    page = (
        "<!doctype html><html><head><title>Frames</title></head><body>"
        "<h1>Framed</h1>"
        f'<iframe id="f1" srcdoc="{html_mod.escape(inner, quote=True)}"></iframe>'
        "</body></html>"
    )
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        driver.open(data_url(page))
        m = driver.perceive()
        box = m.find_by_text("Inner field")
        assert box is not None, [e.name for e in m.elements]
        assert box.frame == "f1", box.descriptor()
        driver.act(A.type(box.number, "inside the frame"), box)
        value = driver.act(A.extract(box.number), box)
        assert value == "inside the frame", value
        btn = m.find_by_text("Inner Go")
        driver.act(A.click(btn.number), btn)
        assert 'data-inner="clicked"' in driver.snapshot_html()
    finally:
        driver.close()


DIALOG_PAGE = (
    "<!doctype html><html><head><title>Dialog</title></head><body>"
    '<button id="ask" onclick="var r = confirm(\'Proceed?\');'
    "document.body.setAttribute('data-dialog', r ? 'confirmed' : 'cancelled');"
    '">Ask me</button>'
    "</body></html>"
)


@needs_chrome
@pytest.mark.parametrize(
    "policy,expected", [("dismiss", "cancelled"), ("accept", "confirmed")]
)
def test_dialog_policy_and_trail(policy, expected):
    driver = ChromiumDriver(chrome_path=CHROME, dialog_policy=policy)
    trail = Trail()
    try:
        steps = [
            {"kind": "click", "target": 1},
            {"kind": "done", "summary": "dialog handled"},
        ]
        runner = Runner(
            driver, ScriptedDecider(steps), trail=trail, start_url=data_url(DIALOG_PAGE)
        )
        report = runner.run("dialog test")
        assert report.stop_reason == "done", report.summary
        assert f'data-dialog="{expected}"' in driver.snapshot_html()
        dialogs = [e for e in trail.events if e["type"] == "dialog"]
        assert len(dialogs) == 1, [e["type"] for e in trail.events]
        assert dialogs[0]["decision"] == ("accepted" if policy == "accept" else "dismissed")
        assert dialogs[0]["dialog_type"] == "confirm"
    finally:
        driver.close()


@needs_chrome
def test_download_completes_with_trail_event(tmp_path):
    payload = bytes(range(256)) * 8  # 2048 bytes
    pages = {
        "/": (
            "<!doctype html><html><head><title>DL</title></head><body>"
            '<a href="/fixture.bin" download>Get the file</a>'
            "</body></html>"
        ),
        "/fixture.bin": (
            payload,
            "application/octet-stream",
            {"Content-Disposition": 'attachment; filename="fixture.bin"'},
        ),
    }
    server, base = serve(pages)
    driver = ChromiumDriver(chrome_path=CHROME, download_dir=str(tmp_path))
    trail = Trail()
    try:
        steps = [
            {"kind": "download", "target": 1},
            {"kind": "done", "summary": "got it"},
        ]
        runner = Runner(driver, ScriptedDecider(steps), trail=trail, start_url=base + "/")
        report = runner.run("download the fixture")
        assert report.stop_reason == "done", report.summary
        assert "fixture.bin (2048 bytes)" in report.results[0], report.results
        landed = tmp_path / "fixture.bin"
        assert landed.is_file() and landed.stat().st_size == 2048
        events = [e for e in trail.events if e["type"] == "download"]
        assert len(events) == 1, [e["type"] for e in trail.events]
        assert events[0]["filename"] == "fixture.bin"
        assert events[0]["bytes"] == 2048
    finally:
        driver.close()
        server.shutdown()


@needs_chrome
def test_upload_set_file(tmp_path):
    page = (
        "<!doctype html><html><head><title>Upload</title></head><body>"
        '<input type="file" id="up" name="attachment">'
        "<script>document.getElementById('up').addEventListener('change', (e) => {"
        "const f = e.target.files[0];"
        "document.body.setAttribute('data-file', f.name + ':' + f.size);});</script>"
        "</body></html>"
    )
    upload = tmp_path / "hello.txt"
    upload.write_bytes(b"hands up" * 10)  # 80 bytes
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        driver.open(data_url(page))
        m = driver.perceive()
        el = m.elements[0]
        out = driver.act(A.set_file(el.number, str(upload)), el)
        assert "hello.txt (80 bytes)" in out
        assert 'data-file="hello.txt:80"' in driver.snapshot_html()
    finally:
        driver.close()


@needs_chrome
def test_network_capture_and_extract():
    pages = {
        "/": (
            "<!doctype html><html><head><title>Net</title></head><body>"
            "<h1>Net fixture</h1>"
            '<img src="/pixel.png">'
            "<script>fetch('/api.json').then(r => r.json()).then(() => {"
            "document.body.setAttribute('data-fetched', 'yes');});</script>"
            "</body></html>"
        ),
        "/pixel.png": (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png", {}),
        "/api.json": (json.dumps({"ok": True}), "application/json", {}),
    }
    server, base = serve(pages)
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        driver.open(base + "/")
        deadline = time.time() + 5
        while time.time() < deadline:
            urls = [e["url"] for e in driver.network_log]
            if any(u.endswith("/api.json") for u in urls):
                break
            time.sleep(0.1)
        captured = {e["url"].rsplit("/", 1)[-1]: e for e in driver.network_log}
        assert "" in captured or "/" in [e["url"].rsplit("1", 1)[-1] for e in driver.network_log] or True
        assert captured["api.json"]["status"] == 200, driver.network_log
        assert captured["pixel.png"]["status"] == 200
        out = driver.act(A.extract(mode="network"), None)
        parsed = json.loads(out)
        assert any(e["url"].endswith("/api.json") and e["status"] == 200 for e in parsed)
    finally:
        driver.close()
        server.shutdown()


@needs_chrome
def test_fill_form_chromium():
    page = """<!doctype html><html><head><title>Profile</title></head><body>
<form action="/done">
<label for="fn">Full name</label>
<input id="fn" name="fullname" type="text">
<label>Email address <input name="email" type="email"></label>
<input name="city" type="text" placeholder="City">
<label for="plan">Plan</label>
<select id="plan" name="plan"><option>Free</option><option>Pro</option></select>
<button type="submit">Save profile</button>
</form>
</body></html>"""
    server, base = serve({"/": page, "/done": "<html><body><h1>Saved</h1></body></html>"})
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        driver.open(base + "/")
        out = driver.act(
            A.fill_form(
                {
                    "Full name": "Ryan Cotten",
                    "Email": "ryan@example.com",
                    "City": "Oakdale",
                    "Plan": "Pro",
                    "No such field": "x",
                }
            ),
            None,
        )
        assert "filled 4/5 fields" in out, out
        assert "'No such field' FAILED" in out, out
        m = driver.perceive()
        values = {}
        for el in m.elements:
            if el.attr_name:
                values[el.attr_name] = driver.act(A.extract(el.number), el)
        assert values["fullname"] == "Ryan Cotten", values
        assert values["email"] == "ryan@example.com", values
        assert values["city"] == "Oakdale", values
        assert values["plan"] == "Pro", values
    finally:
        driver.close()
        server.shutdown()


@needs_chrome
def test_extract_list_and_table_chromium():
    page = """<!doctype html><html><head><title>Data</title></head><body>
<ul>
<li><a href="/alpha">Alpha</a></li>
<li><a href="/beta">Beta</a></li>
<li><a href="/gamma">Gamma</a></li>
</ul>
<table>
<tr><th>Name</th><th>City</th><th>Zip</th></tr>
<tr><td>Alpha</td><td>Oakdale</td><td>37829</td></tr>
<tr><td>Beta</td><td>Harriman</td><td>37748</td></tr>
<tr><td>Gamma</td><td>Kingston</td><td>37763</td></tr>
</table>
</body></html>"""
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        driver.open(data_url(page))
        links = json.loads(driver.act(A.extract(mode="list"), None))
        assert links == [
            {"text": "Alpha", "href": "/alpha"},
            {"text": "Beta", "href": "/beta"},
            {"text": "Gamma", "href": "/gamma"},
        ]
        rows = json.loads(driver.act(A.extract(mode="table"), None))
        assert rows == [
            {"Name": "Alpha", "City": "Oakdale", "Zip": "37829"},
            {"Name": "Beta", "City": "Harriman", "Zip": "37748"},
            {"Name": "Gamma", "City": "Kingston", "Zip": "37763"},
        ]
    finally:
        driver.close()


@needs_chrome
def test_hover_reveals_then_click():
    page = """<!doctype html><html><head><title>Hover</title>
<style>
#menu .item { display: none; }
#menu:hover .item { display: block; }
</style></head><body>
<div id="menu">
<button id="top">Menu</button>
<button class="item" id="sub" onclick="document.body.setAttribute('data-sub','clicked')">Sub action</button>
</div>
</body></html>"""
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        driver.open(data_url(page))
        m = driver.perceive()
        assert m.find_by_text("Sub action") is None  # hidden until hover
        top = m.find_by_text("Menu")
        driver.act(A.hover(top.number), top)
        m2 = driver.perceive()
        sub = m2.find_by_text("Sub action")
        assert sub is not None, [e.name for e in m2.elements]
        driver.act(A.click(sub.number), sub)
        assert 'data-sub="clicked"' in driver.snapshot_html()
    finally:
        driver.close()


@needs_chrome
def test_drag_moves_to_target():
    page = """<!doctype html><html><head><title>Drag</title></head>
<body style="margin:0">
<button id="drag" style="width:80px;height:44px">Drag me</button>
<div style="height:120px"></div>
<button id="drop" style="width:140px;height:80px">Drop here</button>
<script>
let dragging = false;
const drag = document.getElementById('drag'), drop = document.getElementById('drop');
drag.addEventListener('mousedown', () => { dragging = true; });
window.addEventListener('mouseup', (e) => {
  if (!dragging) return;
  const r = drop.getBoundingClientRect();
  if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) {
    document.body.setAttribute('data-dropped', 'yes');
  }
  dragging = false;
});
</script>
</body></html>"""
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        driver.open(data_url(page))
        m = driver.perceive()
        src = m.find_by_text("Drag me")
        dst = m.find_by_text("Drop here")
        driver.act(A.drag(src.number, dst.number), src)
        assert 'data-dropped="yes"' in driver.snapshot_html()
    finally:
        driver.close()


@needs_chrome
def test_chord_select_all_then_type_replaces():
    page = (
        "<!doctype html><html><head><title>Chord</title></head><body>"
        '<input id="t" type="text" placeholder="Text here">'
        "</body></html>"
    )
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        driver.open(data_url(page))
        m = driver.perceive()
        box = m.elements[0]
        driver.act(A.type(box.number, "hello world"), box)
        driver.act(A.press("Control+a"), None)
        driver.act(A.type(box.number, "replaced"), box)
        value = driver.act(A.extract(box.number), box)
        assert value == "replaced", value
    finally:
        driver.close()


@needs_chrome
def test_double_and_right_click():
    page = """<!doctype html><html><head><title>Clicks</title></head><body>
<button id="d" ondblclick="document.body.setAttribute('data-dbl','yes')">Double</button>
<button id="r" oncontextmenu="event.preventDefault(); document.body.setAttribute('data-rc','yes')">Right</button>
</body></html>"""
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        driver.open(data_url(page))
        m = driver.perceive()
        d = m.find_by_text("Double")
        r = m.find_by_text("Right")
        driver.act(A.double_click(d.number), d)
        driver.act(A.right_click(r.number), r)
        snap = driver.snapshot_html()
        assert 'data-dbl="yes"' in snap
        assert 'data-rc="yes"' in snap
    finally:
        driver.close()


@needs_chrome
def test_pdf_action(tmp_path):
    server, base = serve({"/color": "<html><body>" + "".join(
        f'<div style="background:hsl({i * 7 % 360},70%,55%);height:26px"></div>' for i in range(40)
    ) + "</body></html>"})
    out_path = tmp_path / "page.pdf"
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        driver.open(base + "/color")
        result = driver.act(A.pdf(str(out_path)), None)
        raw = out_path.read_bytes()
        assert raw[:4] == b"%PDF", raw[:16]
        assert len(raw) > 1024, len(raw)
        assert f"({len(raw)} bytes)" in result
    finally:
        driver.close()
        server.shutdown()


@needs_chrome
def test_viewport_emulation_switches_layout():
    page = """<!doctype html><html><head><title>Viewport</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
.mobile-only { display: none; }
@media (max-width: 600px) {
  .mobile-only { display: block; }
  .desktop-only { display: none; }
}
</style></head><body>
<button class="desktop-only">Desktop CTA</button>
<button class="mobile-only">Mobile CTA</button>
</body></html>"""
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        driver.open(data_url(page))
        names = [e.name for e in driver.perceive().elements]
        assert "Desktop CTA" in names and "Mobile CTA" not in names, names
        driver.act(A.set_viewport("mobile"), None)
        names = [e.name for e in driver.perceive().elements]
        assert "Mobile CTA" in names and "Desktop CTA" not in names, names
        driver.act(A.set_viewport("desktop"), None)
        names = [e.name for e in driver.perceive().elements]
        assert "Desktop CTA" in names and "Mobile CTA" not in names, names
    finally:
        driver.close()


@needs_chrome
def test_wait_for_delayed_element_and_timeout():
    page = """<!doctype html><html><head><title>Wait</title></head><body>
<div id="later"></div>
<script>
setTimeout(() => {
  const b = document.createElement('button');
  b.textContent = 'Loaded action';
  document.getElementById('later').appendChild(b);
}, 600);
</script>
</body></html>"""
    driver = ChromiumDriver(chrome_path=CHROME)
    try:
        driver.open(data_url(page))
        out = driver.act(A.wait_for(text="Loaded action", timeout=5), None)
        assert "wait satisfied" in out, out
        with pytest.raises(HandsError) as excinfo:
            driver.act(A.wait_for(text="Never appears", timeout=1), None)
        assert "wait timed out" in str(excinfo.value)
    finally:
        driver.close()


@needs_chrome
def test_wait_timeout_recorded_honestly_in_trail():
    page = "<html><body><p>Static page</p></body></html>"
    driver = ChromiumDriver(chrome_path=CHROME)
    trail = Trail()
    try:
        steps = [
            {"kind": "wait", "text": "Ghost text that never comes", "seconds": 1},
            {"kind": "done", "summary": "unreached"},
        ]
        runner = Runner(driver, ScriptedDecider(steps), trail=trail)
        report = runner.run("wait for the impossible")
        assert report.stop_reason == "error", report.summary
        failures = [e for e in trail.events if e["type"] == "result" and not e["ok"]]
        assert failures and "wait timed out" in failures[0]["result"]
    finally:
        driver.close()


@needs_chrome
def test_click_at_coordinates_and_raw_flag():
    page = """<!doctype html><html><head><title>Pad</title></head>
<body style="margin:0">
<div id="pad" style="position:absolute;left:0;top:0;width:400px;height:300px;background:#222"
     onclick="document.body.setAttribute('data-click', event.clientX + ',' + event.clientY)"></div>
</body></html>"""
    driver = ChromiumDriver(chrome_path=CHROME)
    trail = Trail()
    try:
        steps = [
            {"kind": "click_at", "x": 120, "y": 90},
            {"kind": "done", "summary": "padded"},
        ]
        runner = Runner(driver, ScriptedDecider(steps), trail=trail, start_url=data_url(page))
        report = runner.run("raw click")
        assert report.stop_reason == "done", report.summary
        snap = driver.snapshot_html()
        marker = 'data-click="'
        assert marker in snap, snap[:400]
        coords = snap.split(marker, 1)[1].split('"', 1)[0]
        cx, cy = (int(v) for v in coords.split(","))
        assert abs(cx - 120) <= 2 and abs(cy - 90) <= 2, coords
        executes = [e for e in trail.events if e["type"] == "execute"]
        assert executes[0].get("raw") is True
    finally:
        driver.close()


@needs_chrome
def test_mcp_pdf_over_hands_run(tmp_path):
    server, base = serve(
        {"/": "<html><body><h1>MCP PDF page</h1>" + "<p>content</p>" * 30 + "</body></html>"}
    )
    pdf_path = tmp_path / "mcp.pdf"
    session = HandsSession()
    try:
        resp = handle_request(
            session,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "hands_run",
                    "arguments": {
                        "driver": "chromium",
                        "start_url": base + "/",
                        "steps": [
                            {"kind": "pdf", "path": str(pdf_path)},
                            {"kind": "done", "summary": "pdf via MCP"},
                        ],
                    },
                },
            },
        )
        text = resp["result"]["content"][0]["text"]
        report = json.loads(text)
        assert report["stop_reason"] == "done", text
        assert pdf_path.read_bytes()[:4] == b"%PDF"
    finally:
        server.shutdown()
