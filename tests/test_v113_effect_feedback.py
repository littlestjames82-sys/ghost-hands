"""v1.13 features (effect feedback):

The rendered element map now carries each field's LIVE value, so a
decider can see that its last action landed — the missing signal
behind the shifting-page-heal eval failure, where the heal worked
but the model, staring at an unchanged map on a page that shows no
visible change after Save, re-verified, flailed, and was stopped as
stuck. Because the map signature hashes the render, a value change
also moves the signature.

Redaction contract: a password input's content NEVER renders — not
from a static ``value=`` attribute, not after typing, not in a
descriptor. Checkbox/radio state renders as a bare ``checked``
marker. ``value`` and ``checked`` are effect signals, not identity:
both stay out of ``identity()`` / heal matching, so a heal still
re-resolves a field whose value changed since perception.
"""

from __future__ import annotations

import json

from ghost_hands import actions as A
from ghost_hands.drivers import FakeDriver
from ghost_hands.eyes import ElementMap

NOTE_PAGE = {
    "https://v113.local/notes": """<!doctype html><html><head><title>Notes</title>
</head><body>
<input id="note" name="note" type="text" placeholder="Note">
<input id="pw" name="pw" type="password" placeholder="Password">
<button>Save note</button>
</body></html>"""
}


def _driver() -> FakeDriver:
    return FakeDriver(NOTE_PAGE, start_url="https://v113.local/notes")


# ---------------------------------------------------------------------------
# The loop closes: typing shows up in the next perception
# ---------------------------------------------------------------------------


def test_value_renders_after_typing_on_fake_driver():
    driver = _driver()
    before = driver.perceive()
    assert 'value="shift proof"' not in before.render()
    note = before.find_by_text("Note")
    assert note is not None
    driver.act(A.type(note.number, "shift proof"), note)
    after = driver.perceive()
    assert 'value="shift proof"' in after.render()


def test_map_signature_changes_when_a_value_changes():
    driver = _driver()
    before = driver.perceive()
    note = before.find_by_text("Note")
    assert note is not None
    driver.act(A.type(note.number, "shift proof"), note)
    after = driver.perceive()
    assert after.signature() != before.signature()


def test_focused_render_shows_values_too():
    driver = _driver()
    first = driver.perceive()
    note = first.find_by_text("Note")
    assert note is not None
    driver.act(A.type(note.number, "shift proof"), note)
    focused = driver.perceive().render_focused("type into the Note field")
    assert 'value="shift proof"' in focused


# ---------------------------------------------------------------------------
# Static pages: prefilled values, selects, textareas, checkboxes
# ---------------------------------------------------------------------------


def test_prefilled_html_value_renders_on_first_perception():
    m = ElementMap.from_html('<input type="text" value="Ada Lovelace">')
    assert 'value="Ada Lovelace"' in m.render()


def test_select_renders_selected_option_text():
    m = ElementMap.from_html(
        '<select name="color">'
        "<option>Red</option><option selected>Blue</option>"
        "</select>"
    )
    assert 'value="Blue"' in m.render()


def test_select_defaults_to_first_option_like_a_browser():
    m = ElementMap.from_html(
        '<select name="size"><option value="s">Small</option>'
        "<option>Large</option></select>"
    )
    assert 'value="Small"' in m.render()


def test_textarea_content_renders_as_value():
    m = ElementMap.from_html("<textarea>draft note</textarea>")
    assert 'value="draft note"' in m.render()


def test_checked_checkbox_renders_marker_unchecked_does_not():
    m = ElementMap.from_html(
        '<input type="checkbox" checked><input type="checkbox">'
    )
    lines = m.render().splitlines()
    assert lines[0].endswith(" checked")
    assert "checked" not in lines[1]


def test_long_values_are_truncated_with_an_ellipsis():
    # The placeholder names the field so the assertion isolates the
    # value suffix (an unnamed input's name falls back to its value).
    m = ElementMap.from_html(
        f'<input type="text" placeholder="Blob" value="{"x" * 100}">'
    )
    rendered = m.render()
    assert "…" in rendered
    assert "x" * 61 not in rendered


def test_submit_button_value_is_not_echoed():
    # A submit's value is its caption: already the element's name.
    m = ElementMap.from_html('<input type="submit" value="Send it">')
    assert m.render() == '[1] <input> "Send it" type=submit'


# ---------------------------------------------------------------------------
# Password redaction — the critical contract
# ---------------------------------------------------------------------------


def test_password_value_attribute_never_renders():
    m = ElementMap.from_html('<input type="password" value="attr-secret">')
    assert "attr-secret" not in m.render()
    assert "attr-secret" not in m.render_focused("sign in")
    el = m.elements[0]
    assert el.value is None
    assert "attr-secret" not in json.dumps(el.descriptor())


def test_typed_password_never_renders_anywhere():
    driver = _driver()
    first = driver.perceive()
    pw = first.find_by_text("Password")
    assert pw is not None
    driver.act(A.type(pw.number, "typed-secret"), pw)
    after = driver.perceive()
    assert "typed-secret" not in after.render()
    assert "typed-secret" not in after.render_focused("sign in")
    el = after.get(pw.number)
    assert el is not None and el.value is None
    assert "typed-secret" not in json.dumps(el.descriptor())
    # The driver still holds the value internally (extract/checkers
    # read the field store) — only perception is redacted.
    assert driver.fields["pw"] == "typed-secret"


# ---------------------------------------------------------------------------
# Identity stability: value changes must not break healing
# ---------------------------------------------------------------------------


def test_heal_reresolves_after_a_value_change():
    driver = _driver()
    before = driver.perceive()
    note = before.find_by_text("Note")
    assert note is not None
    descriptor = note.descriptor()
    driver.act(A.type(note.number, "shift proof"), note)
    after = driver.perceive()
    candidate = after.find_heal_candidate(descriptor)
    assert candidate is not None and candidate.id == "note"
    assert after.find_by_descriptor(descriptor) is not None


def test_value_and_checked_stay_out_of_identity():
    m = ElementMap.from_html(
        '<input id="n" type="text" value="before">'
        '<input id="c" type="checkbox" checked>'
    )
    for el in m.elements:
        identity = el.identity()
        assert "value" not in identity
        assert "checked" not in identity


# ---------------------------------------------------------------------------
# Sensitive-value masking (v1.13): credentials, financial and
# government identifiers, one-time codes, and secret tokens are
# masked AT INGESTION — presence + character count only, following
# the phone approver's redaction standard — so the raw string
# appears nowhere in a render or a descriptor. Ordinary working
# data (names, emails, addresses, note text) still renders in full.
# ---------------------------------------------------------------------------

CARD = "4242 4242 4242 4242"  # Luhn-valid test number, 19 chars
API_TOKEN = "sk-live-Ab3dEf5Gh7Jk9Lm2No4Pq6Rs"

PAY_PAGE = {
    "https://v113.local/pay": """<!doctype html><html><head><title>Pay</title>
</head><body>
<input id="plain" name="plain" type="text" placeholder="Details">
<input id="note2" name="note2" type="text" placeholder="Memo">
<button>Pay now</button>
</body></html>"""
}


def _pay_driver() -> FakeDriver:
    return FakeDriver(PAY_PAGE, start_url="https://v113.local/pay")


def test_typed_card_renders_masked_and_raw_appears_nowhere():
    # A plain, unmarked text field: the Luhn pattern alone must mask.
    driver = _pay_driver()
    first = driver.perceive()
    plain = first.find_by_text("Details")
    assert plain is not None
    driver.act(A.type(plain.number, CARD), plain)
    after = driver.perceive()
    rendered = after.render()
    assert "[masked · 19 chars]" in rendered
    assert "4242" not in rendered
    assert "4242" not in after.render_focused("pay")
    el = after.get(plain.number)
    assert el is not None
    assert "4242" not in json.dumps(el.descriptor())
    # The driver still holds the raw value internally.
    assert driver.fields["plain"] == CARD


def test_static_card_value_masks_in_the_parser():
    m = ElementMap.from_html(
        '<input type="text" name="amount" value="4242424242424242">'
    )
    assert "[masked · 16 chars]" in m.render()
    assert "4242424242424242" not in m.render()
    assert "4242424242424242" not in json.dumps(m.elements[0].descriptor())


def test_ssn_shaped_value_masks():
    m = ElementMap.from_html(
        '<input type="text" name="identifier" value="123-45-6789">'
    )
    assert "[masked · 11 chars]" in m.render()
    assert "123-45-6789" not in m.render()


def test_cc_autocomplete_masks_even_a_non_luhn_value():
    m = ElementMap.from_html(
        '<input type="text" autocomplete="cc-number" value="1234 5678">'
    )
    assert "[masked · 9 chars]" in m.render()
    assert "1234 5678" not in m.render()


def test_one_time_code_masks_by_signal_not_by_pattern():
    m = ElementMap.from_html(
        '<input type="text" autocomplete="one-time-code" value="123456">'
    )
    assert "[masked · 6 chars]" in m.render()
    assert 'value="123456"' not in m.render()
    # A bare 6-digit value in an unmarked field is working data
    # (a quantity, a PIN-shaped note) — pattern alone never masks it.
    plain = ElementMap.from_html('<input type="text" name="code" value="123456">')
    assert 'value="123456"' in plain.render()


def test_api_key_shaped_token_masks():
    m = ElementMap.from_html(
        f'<input type="text" name="credential" value="{API_TOKEN}">'
    )
    rendered = m.render()
    assert "[masked · 32 chars]" in rendered
    assert API_TOKEN not in rendered
    assert API_TOKEN not in json.dumps(m.elements[0].descriptor())


def test_field_purpose_hints_mask_without_any_pattern():
    # Too short and non-Luhn: only the field's own words can mask these.
    by_name = ElementMap.from_html(
        '<input type="text" name="card_number" value="9999">'
    )
    assert "[masked · 4 chars]" in by_name.render()
    assert 'value="9999"' not in by_name.render()
    by_placeholder = ElementMap.from_html(
        '<input type="text" placeholder="CVV" value="123">'
    )
    assert "[masked · 3 chars]" in by_placeholder.render()
    by_label = ElementMap.from_html(
        '<label for="x1">Card number</label>'
        '<input id="x1" type="text" value="9999">'
    )
    assert "[masked · 4 chars]" in by_label.render()


def test_2fa_hint_masks_a_code():
    m = ElementMap.from_html(
        '<input type="text" name="2fa_code" value="123456">'
    )
    assert "[masked · 6 chars]" in m.render()
    assert 'value="123456"' not in m.render()


def test_benign_values_still_render_in_full():
    m = ElementMap.from_html(
        '<input type="text" name="email" value="ada@example.com">'
        '<input type="text" name="memo" '
        'value="the quick brown fox jumps over the lazy dog, again">'
    )
    rendered = m.render()
    assert 'value="ada@example.com"' in rendered
    assert "the quick brown fox jumps over the lazy dog, again" in rendered


def test_url_value_renders_in_full():
    m = ElementMap.from_html(
        '<input type="text" name="link" '
        'value="https://example.com/some/long/path">'
    )
    assert 'value="https://example.com/some/long/path"' in m.render()


def test_masked_signature_changes_when_masked_length_changes():
    driver = _pay_driver()
    first = driver.perceive()
    plain = first.find_by_text("Details")
    assert plain is not None
    driver.act(A.type(plain.number, "123-45-6789"), plain)
    sig_ssn = driver.perceive().signature()
    driver.act(A.type(plain.number, CARD), plain)
    sig_card = driver.perceive().signature()
    assert sig_ssn != sig_card


def test_knob_full_renders_raw_except_passwords(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_FIELD_VALUES", "full")
    driver = _pay_driver()
    first = driver.perceive()
    plain = first.find_by_text("Details")
    assert plain is not None
    driver.act(A.type(plain.number, CARD), plain)
    assert f'value="{CARD}"' in driver.perceive().render()
    # Passwords never render in ANY mode — full included.
    pw_driver = _driver()
    pw_first = pw_driver.perceive()
    pw = pw_first.find_by_text("Password")
    assert pw is not None
    pw_driver.act(A.type(pw.number, "typed-secret"), pw)
    assert "typed-secret" not in pw_driver.perceive().render()


def test_knob_off_renders_no_field_values(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_FIELD_VALUES", "off")
    driver = _driver()
    first = driver.perceive()
    note = first.find_by_text("Note")
    assert note is not None
    driver.act(A.type(note.number, "shift proof"), note)
    rendered = driver.perceive().render()
    assert "shift proof" not in rendered
    assert "[1]" in rendered  # the map itself still renders


def test_heal_reresolves_after_a_masked_value_change():
    driver = _pay_driver()
    before = driver.perceive()
    plain = before.find_by_text("Details")
    assert plain is not None
    descriptor = plain.descriptor()
    driver.act(A.type(plain.number, CARD), plain)
    after = driver.perceive()
    candidate = after.find_heal_candidate(descriptor)
    assert candidate is not None and candidate.id == "plain"
    assert after.find_by_descriptor(descriptor) is not None


def test_extract_reads_the_live_source_not_the_mask():
    driver = _pay_driver()
    first = driver.perceive()
    plain = first.find_by_text("Details")
    assert plain is not None
    driver.act(A.type(plain.number, CARD), plain)
    current = driver.perceive()
    target = current.get(plain.number)
    assert target is not None
    # The perceived copy is masked; the explicit extract is an
    # operator-intended read and returns the raw value.
    assert "[masked" in (target.value or "")
    assert driver.act(A.extract(plain.number), target) == CARD


def test_extract_of_a_prefilled_sensitive_field_reads_the_source():
    pages = {
        "https://v113.local/pre": (
            "<!doctype html><html><body>"
            f'<input id="cc" name="cc" type="text" value="{CARD}">'
            "</body></html>"
        )
    }
    driver = FakeDriver(pages, start_url="https://v113.local/pre")
    perceived = driver.perceive()
    el = perceived.elements[0]
    assert "4242" not in perceived.render()  # perception is masked
    assert driver.act(A.extract(el.number), el) == CARD


def test_masked_value_never_doubles_as_a_name():
    # An unnamed input is named by its value; for a sensitive
    # value the name must be the marker, never the raw string.
    m = ElementMap.from_html(f'<input type="text" value="{CARD}">')
    assert "4242" not in m.render()
    assert m.elements[0].name == "[masked · 19 chars]"
