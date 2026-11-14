"""v1.14 features (content visibility):

Part 1 — every element map now carries a page-text excerpt: the
page's visible text (whitespace-collapsed, script/style excluded,
capped at PAGE_TEXT_LIMIT with a truncation marker), rendered in a
delimited `--- page text ---` section after the element lines. The
graded multi-hop-fact task exposed the gap: its target page's map
was a single "Back to guides" link while the answer ("founded in
1899") sat in plain text the map never showed, so the model
guessed extract modes blind. Because render() carries the excerpt
and signature() hashes the render, a text-only page change also
moves the map signature.

Part 2 — the Runner's unchanged-map stuck rule now counts *steps
that taught the run nothing*: a step whose action result is a
non-empty string never before seen in this run is progress and
resets the unchanged counter (exploration that pays off — an
extract mode returning content after two empty modes — no longer
races the stuck clock). Identical repeated results and empty
results never reset it, so a genuinely looping run still stops.
The novelty rule applies to every executed action's result, not
just extract: novelty is a property of what the world answered,
and the repeated-(action, target) rule guards true loops on its
own terms.
"""

from __future__ import annotations

from ghost_hands import actions as A
from ghost_hands.deciders import ScriptedDecider
from ghost_hands.drivers import ChromiumDriver, FakeDriver
from ghost_hands.eyes import PAGE_TEXT_HEADER, PAGE_TEXT_LIMIT, ElementMap
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail

# The eval's multi-hop shape, shrunk: home -> guides -> a towns page
# whose only element is a "Back" link and whose answer is plain text.
HOP_PAGES = {
    "https://v114.local/home": (
        "<html><head><title>Home</title></head><body>"
        "<h1>Home</h1>"
        '<a href="/guides">Guides</a> <a href="/about">About us</a>'
        "</body></html>"
    ),
    "https://v114.local/guides": (
        "<html><head><title>Guides</title></head><body>"
        "<h1>Guides</h1>"
        '<a href="/towns">Trail towns</a> <a href="/home">Back home</a>'
        "</body></html>"
    ),
    "https://v114.local/towns": (
        "<html><head><title>Trail towns</title></head><body>"
        "<h1>Trail towns</h1>"
        "<p>Oakdale was founded in 1899 at the junction of two rail lines.</p>"
        '<a href="/guides">Back to guides</a>'
        "</body></html>"
    ),
}


# ---------------------------------------------------------------------------
# Part 1: the excerpt is in the map
# ---------------------------------------------------------------------------


def test_excerpt_carries_the_fact_on_a_content_page():
    driver = FakeDriver(HOP_PAGES, start_url="https://v114.local/home")
    home = driver.perceive()
    guides_link = home.find_by_text("Guides")
    assert guides_link is not None
    driver.act(A.click(guides_link.number), guides_link)
    guides = driver.perceive()
    towns_link = guides.find_by_text("Trail towns")
    assert towns_link is not None
    driver.act(A.click(towns_link.number), towns_link)
    towns = driver.perceive()
    # The whole point: the map's elements are still just the Back
    # link, but the answer is now visible in perception itself.
    assert [el.name for el in towns.elements] == ["Back to guides"]
    assert "founded in 1899" in towns.page_text
    rendered = towns.render()
    assert PAGE_TEXT_HEADER in rendered
    assert "founded in 1899" in rendered
    assert "founded in 1899" in towns.render_focused("Oakdale founding year")


def test_excerpt_absent_when_the_page_has_no_text():
    m = ElementMap.from_html(
        '<button aria-label="Go"></button><input type="text" placeholder="q">'
    )
    assert m.page_text == ""
    assert PAGE_TEXT_HEADER not in m.render()


def test_excerpt_is_capped_with_a_truncation_marker():
    long_text = " ".join(f"word{i}" for i in range(400))  # ~2,600 chars
    m = ElementMap.from_html(f"<p>{long_text}</p><button>Only</button>")
    assert m.page_text.endswith("[truncated]")
    assert len(m.page_text) <= PAGE_TEXT_LIMIT + len(" …[truncated]")
    assert "word399" not in m.page_text
    assert "word0" in m.page_text


def test_script_and_style_text_never_reaches_the_excerpt():
    m = ElementMap.from_html(
        "<html><head><style>.a { color: STYLESECRET; }</style></head><body>"
        "<p>Visible words here.</p>"
        "<script>var x = 'SCRIPTSECRET';</script>"
        "<button>Go</button></body></html>"
    )
    assert "Visible words here." in m.page_text
    assert "STYLESECRET" not in m.page_text
    assert "SCRIPTSECRET" not in m.page_text
    assert "SCRIPTSECRET" not in m.render()


def test_signature_moves_when_only_the_page_text_changes():
    items = [{"tag": "a", "name": "Back", "href": "/guides"}]
    one = ElementMap.from_elements(items, url="u", page_text="founded in 1899")
    two = ElementMap.from_elements(items, url="u", page_text="founded in 1902")
    # from_elements hashes the items for source_hash — identical
    # here — so this isolates the render's contribution: the
    # excerpt alone moves the signature.
    assert one.source_hash == two.source_hash
    assert one.signature() != two.signature()
    a = ElementMap.from_html('<p>Alpha fact</p><a href="/g">Back</a>')
    b = ElementMap.from_html('<p>Beta fact</p><a href="/g">Back</a>')
    assert a.signature() != b.signature()


def test_chromium_dom_payload_carries_page_text():
    # The DOM-eyes evaluate returns {descriptors, pageText} in ONE
    # round trip; perceive unpacks both halves.
    driver = ChromiumDriver.__new__(ChromiumDriver)
    driver.eyes = "dom"
    driver._launch = lambda: None
    driver.current_url = lambda: "https://v114.local/towns"

    def fake_eval(expr):
        if "pageText" in expr:
            return {
                "descriptors": [
                    {"tag": "a", "name": "Back to guides", "href": "/guides"}
                ],
                "pageText": "Trail towns Oakdale was founded in 1899.",
            }
        return "Trail towns"  # document.title

    driver._eval = fake_eval
    m = driver.perceive()
    assert "founded in 1899" in m.page_text
    assert "founded in 1899" in m.render()


def test_chromium_ax_static_text_becomes_the_excerpt():
    driver = ChromiumDriver.__new__(ChromiumDriver)
    tree = {
        "nodes": [
            {"role": {"value": "RootWebArea"}, "name": {"value": "Trail towns"}},
            {"role": {"value": "heading"}, "name": {"value": "Trail towns"}},
            {
                "role": {"value": "StaticText"},
                "name": {
                    "value": "Oakdale was founded in 1899 at the junction "
                    "of two rail lines."
                },
            },
            {"role": {"value": "link"}, "name": {"value": "Back to guides"}},
            {"role": {"value": "StaticText"}, "name": {"value": "  "}},
        ]
    }
    driver._send = lambda method, params: (
        tree if method == "Accessibility.getFullAXTree" else {}
    )
    driver._eval = lambda expr: "Trail towns"
    driver.current_url = lambda: "https://v114.local/towns"
    m = driver._perceive_ax()
    assert "founded in 1899" in m.page_text
    # Heading text is not double-counted via StaticText children.
    assert m.page_text.count("Trail towns") == 0


# ---------------------------------------------------------------------------
# Part 2: novel results are progress; repeats are not
# ---------------------------------------------------------------------------


class _StaticDriver:
    """A driver whose page never changes: one fixed map, and act()
    answers from a callback. For pinning the stuck rule itself."""

    def __init__(self, answer):
        self._answer = answer
        self.calls = 0
        self._map = ElementMap.from_elements(
            [
                {"tag": "a", "name": "Alpha", "href": "/a"},
                {"tag": "a", "name": "Beta", "href": "/b"},
            ],
            url="https://v114.local/static",
            page_text="A quiet page.",
        )

    def perceive(self):
        return self._map

    def act(self, action, element):
        self.calls += 1
        return self._answer(self.calls, action)

    def current_url(self):
        return "https://v114.local/static"

    def close(self):
        pass


def test_novel_extract_results_reset_the_stuck_counter():
    driver = _StaticDriver(lambda n, action: f"finding number {n}")
    # Modes cycle, as the real model's exploration did — six
    # IDENTICAL extracts would trip the separate repeated-action
    # rule at the fourth repeat, which is that rule working as
    # designed, not this one.
    steps = [
        {"kind": "extract", "mode": mode}
        for mode in ("text", "table", "list", "text", "table", "list")
    ] + [{"kind": "done", "summary": "gathered"}]
    # recovery_attempts=0 makes the pin crisp: any stuck verdict is
    # the raw rule, unrescued. Six novel extracts on an unchanging
    # map must NOT stop stuck — each one taught the run something.
    report = Runner(
        driver,
        ScriptedDecider(steps),
        trail=Trail(),
        recovery_attempts=0,
    ).run("explore")
    assert report.stop_reason == "done", report.summary
    assert len(report.results) == 6


def _cycled_extracts(rounds: int = 3) -> list[dict]:
    """Extract steps cycling modes — each (action, target, mode)
    signature stays at `rounds` repeats, under the repeated-action
    rule's threshold, so only the unchanged-map rule can stop
    these runs. That is the rule under test."""
    return [
        {"kind": "extract", "mode": mode}
        for _ in range(rounds)
        for mode in ("text", "table", "list")
    ]


def test_identical_extract_results_still_stop_stuck():
    driver = _StaticDriver(lambda n, action: "the same finding")
    report = Runner(
        driver,
        ScriptedDecider(_cycled_extracts()),
        trail=Trail(),
        recovery_attempts=0,
    ).run("explore")
    assert report.stop_reason == "stuck", report.summary


def test_empty_results_never_reset_the_stuck_counter():
    driver = _StaticDriver(lambda n, action: "")
    report = Runner(
        driver,
        ScriptedDecider(_cycled_extracts()),
        trail=Trail(),
        recovery_attempts=0,
    ).run("explore")
    assert report.stop_reason == "stuck", report.summary


def test_novelty_applies_to_any_action_kind():
    # Alternating clicks between two targets (so the repeated-
    # action rule, which counts per (action, target), stays under
    # its own threshold) whose results are novel every time: the
    # run is learning, so the unchanged-map rule must not fire.
    driver = _StaticDriver(lambda n, action: f"clicked through to page {n}")
    steps = [
        {"kind": "click", "target": 1},
        {"kind": "click", "target": 2},
        {"kind": "click", "target": 1},
        {"kind": "click", "target": 2},
        {"kind": "click", "target": 1},
        {"kind": "click", "target": 2},
        {"kind": "done", "summary": "walked"},
    ]
    report = Runner(
        driver,
        ScriptedDecider(steps),
        trail=Trail(),
        recovery_attempts=0,
    ).run("walk")
    assert report.stop_reason == "done", report.summary
