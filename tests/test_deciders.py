import pytest

from ghost_hands.deciders import OpenAICompatibleDecider, RuleDecider, ScriptedDecider
from ghost_hands.drivers import DEMO_PAGES, FakeDriver
from ghost_hands.errors import HandsError
from ghost_hands.eyes import ElementMap
from ghost_hands.runner import Runner
from ghost_hands.trail import Trail

HOME = ElementMap.from_html(DEMO_PAGES["https://demo.local/"], url="https://demo.local/")


def test_scripted_plays_back_then_done():
    d = ScriptedDecider([{"kind": "extract"}])
    assert d.decide("g", HOME, []).kind == "extract"
    assert d.decide("g", HOME, []).kind == "done"


def test_rule_go_to():
    a = RuleDecider().decide("go to https://demo.local/todo", HOME, [])
    assert a.kind == "navigate" and a.url == "https://demo.local/todo"


def test_rule_click_by_text():
    a = RuleDecider().decide("click Open the todo list", HOME, [])
    assert a.kind == "click"
    assert HOME.get(a.target).name.startswith("Open the todo")


def test_rule_type_into():
    a = RuleDecider().decide("type ghost into Search the demo web", HOME, [])
    assert a.kind == "type" and a.text == "ghost"


def test_rule_search_types_then_submits():
    d = RuleDecider()
    a1 = d.decide("search for ghost", HOME, [])
    assert a1.kind == "type" and a1.text == "ghost"
    history = [a1.to_dict()]
    a2 = d.decide("search for ghost", HOME, history)
    assert a2.kind == "click"


def test_rule_search_end_to_end():
    driver = FakeDriver(DEMO_PAGES)
    report = Runner(driver, RuleDecider(), trail=Trail()).run("search for ghost")
    assert report.stop_reason == "done"
    assert driver.current_url() == "https://demo.local/results"


def test_rule_done_when_nothing_matches():
    m = ElementMap.from_html("<p>nothing interactive</p>")
    a = RuleDecider().decide("click the flux capacitor", m, [])
    assert a.kind == "done"


def test_openai_decider_requires_env(monkeypatch):
    monkeypatch.delenv("GHOST_HANDS_BASE_URL", raising=False)
    monkeypatch.delenv("GHOST_HANDS_API_KEY", raising=False)
    with pytest.raises(HandsError):
        OpenAICompatibleDecider()


def test_openai_decider_requires_key(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_BASE_URL", "https://llm.local/v1")
    monkeypatch.delenv("GHOST_HANDS_API_KEY", raising=False)
    with pytest.raises(HandsError):
        OpenAICompatibleDecider()
