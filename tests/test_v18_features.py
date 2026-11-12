"""v1.8 features: bounded retry with backoff in the HTTP deciders,
JSON mode for the OpenAI-compatible decider, and the batch
command's per-script token cap flag."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from ghost_hands import cli
from ghost_hands.deciders import AnthropicDecider, OpenAICompatibleDecider
from ghost_hands.drivers import DEMO_PAGES
from ghost_hands.errors import HandsError
from ghost_hands.eyes import ElementMap


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_RETRY_BACKOFF", "0")


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _map():
    return ElementMap.from_html(
        DEMO_PAGES["https://demo.local/"], url="https://demo.local/"
    )


def _http_error(code):
    return urllib.error.HTTPError(
        "http://x.local", code, f"err {code}", hdrs=None, fp=io.BytesIO(b"no")
    )


def _flaky(failures, payload):
    state = {"n": 0, "bodies": []}

    def fake(req, timeout=None):
        state["n"] += 1
        state["bodies"].append(req.data)
        if state["n"] <= len(failures):
            raise failures[state["n"] - 1]
        return _Resp(payload)

    return fake, state


_OPENAI_PAYLOAD = {
    "choices": [
        {"message": {"content": json.dumps({"kind": "extract"})}}
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
}


def test_openai_retries_transient_500s(monkeypatch):
    fake, state = _flaky([_http_error(500), _http_error(503)], _OPENAI_PAYLOAD)
    monkeypatch.setattr("urllib.request.urlopen", fake)
    d = OpenAICompatibleDecider(base_url="http://x.local", api_key="k")
    action = d.decide("g", _map(), [])
    assert action.kind == "extract"
    assert state["n"] == 3
    assert d.usage["retries"] == 2
    assert d.usage["calls"] == 1
    assert d.usage["prompt_tokens"] == 10


def test_openai_400_fails_without_retry(monkeypatch):
    fake, state = _flaky(
        [_http_error(400), _http_error(400), _http_error(400)], _OPENAI_PAYLOAD
    )
    monkeypatch.setattr("urllib.request.urlopen", fake)
    d = OpenAICompatibleDecider(base_url="http://x.local", api_key="k")
    with pytest.raises(HandsError):
        d.decide("g", _map(), [])
    assert state["n"] == 1
    assert d.usage["retries"] == 0


def test_anthropic_retries_429(monkeypatch):
    payload = {
        "content": [{"type": "text", "text": json.dumps({"kind": "extract"})}],
        "usage": {"input_tokens": 7, "output_tokens": 1},
    }
    fake, state = _flaky([_http_error(429)], payload)
    monkeypatch.setattr("urllib.request.urlopen", fake)
    d = AnthropicDecider(base_url="http://a.local", api_key="k")
    action = d.decide("g", _map(), [])
    assert action.kind == "extract"
    assert d.usage["retries"] == 1
    assert d.usage["calls"] == 1


def test_json_mode_sends_response_format(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_JSON_MODE", "1")
    fake, state = _flaky([], _OPENAI_PAYLOAD)
    monkeypatch.setattr("urllib.request.urlopen", fake)
    d = OpenAICompatibleDecider(base_url="http://x.local", api_key="k")
    assert d.json_mode is True
    d.decide("g", _map(), [])
    body = json.loads(state["bodies"][0])
    assert body["response_format"] == {"type": "json_object"}


def test_retries_disabled_by_env(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_RETRIES", "0")
    fake, state = _flaky([_http_error(500)], _OPENAI_PAYLOAD)
    monkeypatch.setattr("urllib.request.urlopen", fake)
    d = OpenAICompatibleDecider(base_url="http://x.local", api_key="k")
    with pytest.raises(HandsError):
        d.decide("g", _map(), [])
    assert state["n"] == 1


def test_batch_accepts_max_llm_tokens(tmp_path, capsys):
    script = tmp_path / "s.json"
    script.write_text(
        json.dumps({"steps": [{"kind": "done", "summary": "ok"}]}),
        encoding="utf-8",
    )
    rc = cli.main(["batch", str(script), "--max-llm-tokens", "500"])
    assert rc == 0
    assert "BATCH: 1/1 done" in capsys.readouterr().out
