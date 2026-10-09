"""Trail -> HTML audit report (v0.8): structure, safety, tolerance."""

import json

import pytest

from ghost_hands.report import (
    parse_trail_lines,
    read_trail_tolerant,
    render_report,
    summarize,
    write_report,
)

T0 = 1_700_000_000.0  # 2023-11-14T22:13:20Z


def ev(type_, step, ts, **fields):
    return {"type": type_, "step": step, "ts": ts, **fields}


def full_featured_events():
    """A synthetic trail exercising every renderer branch: phone +
    bus approval pairs, a heal, a dialog, a download, network events,
    a password type, and a denied consequential ending."""
    return [
        ev("run", 0, T0, goal="Buy the blue mug", run_id="r1"),
        ev("perceive", 1, T0 + 1, url="https://shop.test/", title="Shop",
           elements=6, map_chars=1200, signature="sig1"),
        ev("decide", 1, T0 + 1, action={"kind": "click", "target": 2}),
        ev("govern", 1, T0 + 1, action={"kind": "click", "target": 2},
           classification="readonly", outcome="allow",
           reason="readonly actions are allowed"),
        ev("execute", 1, T0 + 1, action={"kind": "click", "target": 2},
           classification="readonly",
           element={"number": 2, "tag": "a", "role": "link", "name": "Mugs"}),
        ev("result", 1, T0 + 2, ok=True, result="navigated to https://shop.test/mugs"),
        # A network event in its REAL trail shape: the Chromium driver
        # sinks the CDP resource type in the "type" field, clobbering
        # the event type (Trail.record lets fields win).
        ev("Document", 1, T0 + 2, method="GET", url="https://shop.test/mugs",
           status=200),
        # Step 2: login with a password (masked in the report) + a heal.
        ev("perceive", 2, T0 + 3, url="https://shop.test/login",
           title="Sign in", elements=4, map_chars=640, signature="sig2"),
        ev("decide", 2, T0 + 3,
           action={"kind": "type", "target": 9, "text": "hunter2secret"}),
        ev("govern", 2, T0 + 3,
           action={"kind": "type", "target": 9, "text": "hunter2secret"},
           classification="write", outcome="allow", reason="write allowed"),
        ev("heal", 2, T0 + 3, action={"kind": "type", "target": 9},
           descriptor={"tag": "input", "role": "textbox", "name": "Password"},
           from_target=9, to_target=3, found=True, trigger="target missing"),
        ev("execute", 2, T0 + 4,
           action={"kind": "type", "target": 3, "text": "hunter2secret"},
           classification="write", healed=True,
           element={"number": 3, "tag": "input", "role": "textbox",
                    "name": "Password", "type": "password"}),
        ev("result", 2, T0 + 4, ok=True, result="typed 13 chars into [3]"),
        # Step 3: consequential submit, approved on the phone.
        ev("perceive", 3, T0 + 5, url="https://shop.test/cart",
           title="Cart", elements=5, map_chars=800, signature="sig3"),
        ev("decide", 3, T0 + 5, action={"kind": "click", "target": 4}),
        ev("govern", 3, T0 + 5, action={"kind": "click", "target": 4},
           classification="consequential", outcome="ask",
           reason="consequential actions require approval"),
        ev("approval", 3, T0 + 5, channel="phone", phase="request",
           request_id="appr-1", classification="consequential",
           summary="click [4] 'Place order' — Cart (shop.test)"),
        ev("approval", 3, T0 + 9, channel="phone", phase="decision",
           request_id="appr-1", approved=True, decided_by="phone"),
        ev("execute", 3, T0 + 9, action={"kind": "click", "target": 4},
           classification="consequential",
           element={"number": 4, "tag": "button", "role": "button",
                    "name": "Place order"}),
        ev("result", 3, T0 + 10, ok=True, result="order placed"),
        ev("dialog", 3, T0 + 10, dialog_type="alert",
           message="Thanks for your order", decision="accepted",
           policy="accept", url="https://shop.test/cart"),
        ev("download", 3, T0 + 11, filename="receipt.pdf", bytes=2048,
           url="https://shop.test/receipt.pdf", state="completed"),
        # Step 4: consequential delete, bus approval DENIED -> run denied.
        ev("perceive", 4, T0 + 12, url="https://shop.test/account",
           title="Account", elements=3, map_chars=420, signature="sig4"),
        ev("decide", 4, T0 + 12, action={"kind": "click", "target": 1}),
        ev("govern", 4, T0 + 12, action={"kind": "click", "target": 1},
           classification="consequential", outcome="ask",
           reason="consequential actions require approval"),
        ev("approval", 4, T0 + 12, channel="bus", phase="request",
           run_id="run-9", classification="consequential",
           action={"kind": "click", "target": 1}),
        ev("approval", 4, T0 + 20, channel="bus", phase="decision",
           run_id="run-9", approved=False, decided_by="operator"),
        ev("stop", 4, T0 + 20, reason="denied",
           summary="approval denied by operator"),
    ]


def test_render_full_featured_structure():
    html = render_report(full_featured_events(), skipped=0)
    # Header facts.
    assert "Buy the blue mug" in html
    assert "web (Chromium / fake web)" in html  # body from perceive URLs
    assert "stopped: denied" in html
    assert "approval denied by operator" in html
    # Stat cards.
    for marker in ("steps executed", "perception chars", "est. tokens",
                   "approvals asked: 2", "self-heals",
                   "downloads / dialogs / net"):
        assert marker in html, marker
    # Timeline content.
    assert "https://shop.test/login" in html
    assert "click [4]" in html and "Place order" in html
    assert "consequential" in html
    assert "ask → approved" in html and "via phone" in html
    assert "ask → denied" in html and "via bus" in html
    assert "decided by operator" in html
    # Special events inline.
    assert "re-found as [3]" in html and "trigger: target missing" in html
    assert "Thanks for your order" in html
    assert "receipt.pdf" in html and "2048 bytes" in html
    assert "GET https://shop.test/mugs → 200 (Document)" in html
    # Footer contract.
    assert "Generated by Ghost Hands report" in html
    assert "the trail is the record" in html
    # Self-contained: no scripts, no external assets of any kind (the
    # URLs in the output are escaped text content, never attributes).
    assert "<script" not in html
    assert "<link" not in html and "<img" not in html
    assert 'src="http' not in html and 'href="http' not in html


def test_summary_counts():
    summary = summarize(full_featured_events())
    assert summary["steps"] == 3  # accounting counts execute events
    assert summary["goal"] == "Buy the blue mug"
    assert summary["body"] == "web (Chromium / fake web)"
    assert summary["stop_reason"] == "denied"
    assert summary["approvals_requested"] == 2
    assert summary["approvals_granted"] == 1
    assert summary["approvals_denied"] == 1
    assert summary["approval_channels"] == ["bus", "phone"]
    assert summary["heals"] == 1
    assert summary["downloads"] == 1
    assert summary["dialogs"] == 1
    assert summary["net_events"] == 1  # the clobbered-type shape counts
    assert summary["actions_by_class"] == {
        "readonly": 1, "write": 1, "consequential": 2}
    assert summary["duration_s"] == pytest.approx(20.0)


def test_password_value_masked():
    html = render_report(full_featured_events())
    # The password itself never appears; the mask shows its length.
    assert "hunter2secret" not in html
    assert "•••• (13 chars)" in html


def test_fill_form_password_field_masked():
    events = [
        ev("perceive", 1, T0, url="https://x.test/", title="t", elements=2,
           map_chars=100, signature="s"),
        ev("execute", 1, T0,
           action={"kind": "fill_form",
                   "fields": {"user": "ryan", "password": "p4ssw0rd!"}},
           classification="write",
           element={"number": 1, "tag": "form", "role": "form", "name": "f"}),
        ev("result", 1, T0, ok=True, result="filled"),
    ]
    html = render_report(events)
    assert "p4ssw0rd!" not in html
    assert "•••• (9 chars)" in html
    assert "ryan" in html  # non-password values render (truncated only)


def test_long_text_truncated_at_80():
    long_text = "x" * 200
    events = [
        ev("execute", 1, T0,
           action={"kind": "type", "target": 1, "text": long_text},
           classification="write",
           element={"number": 1, "tag": "input", "role": "textbox",
                    "name": "Bio", "type": "text"}),
    ]
    html = render_report(events)
    assert "x" * 80 in html
    assert "x" * 81 not in html


def test_xss_shaped_strings_render_escaped():
    evil = "<script>alert('pwned')</script>"
    events = [
        ev("perceive", 1, T0, url="https://x.test/", title=evil,
           elements=1, map_chars=10, signature="s"),
        ev("decide", 1, T0, action={"kind": "click", "target": 1}),
        ev("execute", 1, T0, action={"kind": "click", "target": 1},
           classification="readonly",
           element={"number": 1, "tag": "button", "role": "button",
                    "name": evil}),
        ev("result", 1, T0, ok=True, result=f"clicked {evil}"),
    ]
    html = render_report(events)
    assert "<script>alert" not in html
    assert "&lt;script&gt;alert" in html
    # The only script-free guarantee: no script tags anywhere.
    assert "<script" not in html


def test_corrupt_lines_skipped_and_counted():
    good = json.dumps(ev("perceive", 1, T0, url="https://x.test/",
                         title="t", elements=1, map_chars=5, signature="s"))
    lines = [good, "{not json", "", "[1,2,3]", '{"no": "type"}', good]
    events, skipped = parse_trail_lines(lines)
    assert len(events) == 2
    assert skipped == 3
    html = render_report(events, skipped=skipped)
    assert "3 unreadable line(s) were skipped" in html


def test_empty_trail_renders():
    html = render_report([])
    assert "Ghost Hands" in html
    assert "No stop event" in html


def test_android_body_inferred():
    events = [
        ev("perceive", 1, T0, url="android://com.example.notes/home",
           title="Notes", elements=3, map_chars=50, signature="s"),
    ]
    assert summarize(events)["body"] == "Android (MrGhosty bridge)"
    assert "Android (MrGhosty bridge)" in render_report(events)


def test_write_report_default_suffix_and_roundtrip(tmp_path):
    trail_path = tmp_path / "run.jsonl"
    lines = [json.dumps(e) for e in full_featured_events()]
    lines.insert(3, "garbage{")
    trail_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = write_report(trail_path)
    assert out == tmp_path / "run.html"
    html = out.read_text(encoding="utf-8")
    assert "Buy the blue mug" in html
    assert "1 unreadable line(s) were skipped" in html
    # Explicit -o path honored.
    other = tmp_path / "custom.html"
    assert write_report(trail_path, other) == other
    assert other.exists()


def test_missing_trail_file_is_a_loud_error(tmp_path):
    # Tolerance covers corrupt LINES; a missing file is a caller error
    # and must not silently render an empty report.
    with pytest.raises(FileNotFoundError):
        read_trail_tolerant(tmp_path / "nope.jsonl")
