from ghost_hands.eyes import ElementMap


def test_button_text_and_render_format():
    m = ElementMap.from_html("<button>Sign in</button>")
    assert len(m) == 1
    assert m.elements[0].tag == "button"
    assert m.elements[0].role == "button"
    assert '[1] <button> "Sign in"' in m.render()


def test_link_role_and_href():
    m = ElementMap.from_html('<a href="https://x.local/cart">Cart</a>')
    el = m.elements[0]
    assert el.role == "link"
    assert el.href == "https://x.local/cart"
    assert "href=https://x.local/cart" in m.render()


def test_input_placeholder_name():
    m = ElementMap.from_html('<input type="text" placeholder="Username">')
    assert m.elements[0].name == "Username"
    assert m.elements[0].type == "text"
    assert m.elements[0].role == "textbox"


def test_input_submit_value_name():
    m = ElementMap.from_html('<input type="submit" value="Send it">')
    el = m.elements[0]
    assert el.role == "button"
    assert el.name == "Send it"


def test_aria_label_precedence():
    m = ElementMap.from_html('<button aria-label="Close dialog">X</button>')
    assert m.elements[0].name == "Close dialog"


def test_name_attr_fallback():
    m = ElementMap.from_html('<input name="email" type="email">')
    assert m.elements[0].name == "email"
    assert m.elements[0].attr_name == "email"


def test_role_button_div():
    m = ElementMap.from_html('<div role="button">Tap me</div>')
    assert len(m) == 1 and m.elements[0].name == "Tap me"


def test_onclick_span():
    m = ElementMap.from_html('<span onclick="go()">Launch</span>')
    assert len(m) == 1 and m.elements[0].name == "Launch"


def test_non_interactive_ignored():
    m = ElementMap.from_html("<p>hello</p><div>world</div>")
    assert len(m) == 0


def test_numbering_is_document_order():
    m = ElementMap.from_html(
        '<a href="/1">One</a><button>Two</button><input placeholder="Three">'
    )
    assert [e.number for e in m.elements] == [1, 2, 3]
    assert [e.name for e in m.elements] == ["One", "Two", "Three"]


def test_form_action_recorded():
    m = ElementMap.from_html(
        '<form action="/submit"><button type="submit">Go</button></form>'
    )
    assert m.elements[0].form_action == "/submit"


def test_budget_truncation_note():
    html = "".join(f"<button>Button number {i} with a long label</button>" for i in range(40))
    m = ElementMap.from_html(html)
    rendered = m.render(budget=200)
    assert "truncated" in rendered
    assert len(rendered) < 400


def test_title_captured():
    m = ElementMap.from_html(
        "<html><head><title>My Page</title></head><body><button>Hi</button></body></html>"
    )
    assert m.title == "My Page"


def test_get_and_find_by_text():
    m = ElementMap.from_html('<button>Alpha</button><button>Beta</button>')
    assert m.get(2).name == "Beta"
    assert m.get(99) is None
    assert m.find_by_text("alp").number == 1
    assert m.find_by_text("zzz") is None


def test_from_elements_driver_supplied():
    m = ElementMap.from_elements(
        [
            {"tag": "button", "role": "button", "name": "Go"},
            {"tag": "a", "role": "link", "name": "Home", "href": "https://x.local/"},
        ],
        url="https://x.local/",
    )
    assert len(m) == 2
    assert m.get(2).href == "https://x.local/"
    assert m.url == "https://x.local/"


def test_signature_changes_with_source():
    a = ElementMap.from_html("<button>Same</button>")
    b = ElementMap.from_html("<button>Same</button><p>extra</p>")
    assert a.signature() != b.signature()
    c = ElementMap.from_html("<button>Same</button>")
    assert a.signature() == c.signature()
