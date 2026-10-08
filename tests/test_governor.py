import pytest

from ghost_hands import actions as A
from ghost_hands.eyes import ElementMap
from ghost_hands.governor import Governor, Policy, classify


def el(html, text):
    m = ElementMap.from_html(html)
    found = m.find_by_text(text)
    assert found is not None
    return found


def test_click_plain_link_readonly():
    e = el('<a href="https://x.local/about">About</a>', "About")
    assert classify(A.click(e.number), e) == "readonly"


def test_click_submit_consequential():
    e = el('<form action="/go"><button type="submit">Sign in</button></form>', "Sign in")
    assert classify(A.click(e.number), e) == "consequential"


def test_click_in_form_consequential():
    e = el('<form action="/go"><button>Whatever</button></form>', "Whatever")
    assert classify(A.click(e.number), e) == "consequential"


@pytest.mark.parametrize(
    "label",
    [
        "Pay now",
        "Buy now",
        "Place order",
        "Delete account",
        "Remove account",
        "Publish post",
        "Send message",
        "Transfer funds",
        "Confirm payment",
        "Complete purchase",
        "Checkout",
    ],
)
def test_consequential_keywords_on_click(label):
    e = el(f"<button>{label}</button>", label)
    assert classify(A.click(e.number), e) == "consequential"


def test_click_plain_button_write():
    e = el("<button>Add item</button>", "Add item")
    assert classify(A.click(e.number), e) == "write"


def test_click_without_element_is_write():
    assert classify(A.click(5)) == "write"


def test_type_write_and_consequential():
    plain = el('<input placeholder="Username">', "Username")
    assert classify(A.type(plain.number, "ryan"), plain) == "write"
    payment = el('<input placeholder="Payment card">', "Payment")
    assert classify(A.type(payment.number, "4242"), payment) == "consequential"


def test_link_with_pay_href_consequential():
    e = el('<a href="https://x.local/pay">Details</a>', "Details")
    assert classify(A.click(e.number), e) == "consequential"


def test_navigate_classes():
    assert classify(A.navigate("https://x.local/docs")) == "readonly"
    assert classify(A.navigate("https://x.local/account/delete")) == "consequential"
    assert classify(A.navigate("https://x.local/checkout")) == "consequential"


def test_readonly_kinds():
    for action in (A.extract(), A.screenshot(), A.scroll(), A.wait(1), A.press("Enter"), A.done()):
        assert classify(action) == "readonly"


def test_select_is_write():
    m = ElementMap.from_html('<select name="color"><option>Red</option></select>')
    e = m.elements[0]
    assert e.attr_name == "color" and e.role == "combobox"
    assert classify(A.select(e.number, "Red"), e) == "write"


def test_default_policy_outcomes():
    g = Governor()
    assert g.evaluate(A.navigate("https://x.local/")).outcome == "allow"
    e = el("<button>Publish post</button>", "Publish")
    d = g.evaluate(A.click(e.number), e)
    assert d.outcome == "ask" and d.classification == "consequential"


def test_policy_deny_consequential():
    g = Governor(Policy(consequential="deny"))
    e = el("<button>Delete account</button>", "Delete")
    assert g.evaluate(A.click(e.number), e).outcome == "deny"


def test_policy_deny_write():
    g = Governor(Policy(write="deny"))
    e = el('<input placeholder="Name">', "Name")
    d = g.evaluate(A.type(e.number, "x"), e)
    assert d.outcome == "deny"


def test_policy_allow_consequential():
    g = Governor(Policy(consequential="allow"))
    e = el("<button>Send message</button>", "Send")
    assert g.evaluate(A.click(e.number), e).outcome == "allow"


def test_blocked_domain():
    g = Governor(Policy(blocked_domains=("evil.local",)))
    d = g.evaluate(A.navigate("https://evil.local/x"))
    assert d.outcome == "deny" and "blocked" in d.reason


def test_allowlist():
    g = Governor(Policy(allowed_domains=("good.local",)))
    assert g.evaluate(A.navigate("https://good.local/")).outcome == "allow"
    d = g.evaluate(A.navigate("https://other.local/"))
    assert d.outcome == "deny" and "allowlist" in d.reason


def test_policy_from_dict_roundtrip():
    p = Policy.from_dict(
        {"readonly": "allow", "write": "ask", "consequential": "deny",
         "allowed_domains": ["a.local"], "blocked_domains": ["b.local"]}
    )
    assert p.write == "ask"
    assert p.to_dict()["allowed_domains"] == ["a.local"]


def test_policy_rejects_bad_outcome():
    with pytest.raises(ValueError):
        Policy(write="maybe")
