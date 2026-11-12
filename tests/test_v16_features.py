"""v1.6 features: the native Anthropic decider, goal-driven MCP runs
on it, and the batch runbook command."""

from __future__ import annotations

import json

from ghost_hands import cli, mcp_server
from ghost_hands.deciders import AnthropicDecider
from ghost_hands.drivers import DEMO_PAGES
from ghost_hands.eyes import ElementMap


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()
        self.headers_seen = None

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _anthropic_stub(contents, usage=None):
    state = {"i": 0, "requests": []}

    def fake(req, timeout=None):
        state["requests"].append(req)
        content = contents[min(state["i"], len(contents) - 1)]
        state["i"] += 1
        payload = {
            "content": [{"type": "text", "text": content}],
            "usage": usage or {"input_tokens": 50, "output_tokens": 4},
        }
        return _Resp(payload)

    return fake, state


def _map():
    return ElementMap.from_html(
        DEMO_PAGES["https://demo.local/"], url="https://demo.local/"
    )


def test_anthropic_decide_shape_and_usage(monkeypatch):
    fake, state = _anthropic_stub(
        [json.dumps({"kind": "extract"})],
        usage={"input_tokens": 210, "output_tokens": 9},
    )
    monkeypatch.setattr("urllib.request.urlopen", fake)
    d = AnthropicDecider(base_url="http://a.local", api_key="sk-test")
    action = d.decide("g", _map(), [])
    assert action.kind == "extract"
    assert d.usage == {"prompt_tokens": 210, "completion_tokens": 9, "calls": 1}
    req = state["requests"][0]
    assert req.full_url == "http://a.local/v1/messages"
    assert req.headers["X-api-key"] == "sk-test"
    assert req.headers["Anthropic-version"] == "2023-06-01"
    body = json.loads(req.data)
    assert body["model"] == "claude-sonnet-4-5"
    assert "system" in body and body["messages"][0]["role"] == "user"


def test_anthropic_batch_parse(monkeypatch):
    content = json.dumps(
        {"actions": [{"kind": "extract"}, {"kind": "done", "summary": "ok"}]}
    )
    fake, _ = _anthropic_stub([content])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    d = AnthropicDecider(base_url="http://a.local", api_key="k", batch=True)
    actions = d.decide_batch("g", _map(), [])
    assert [a.kind for a in actions] == ["extract", "done"]


def test_anthropic_requires_key(monkeypatch):
    monkeypatch.delenv("GHOST_HANDS_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    try:
        AnthropicDecider(base_url="http://a.local")
    except Exception as exc:
        assert "API_KEY" in str(exc)
    else:
        raise AssertionError("expected a missing-key error")


def _call(session, name, args, mid=1):
    resp = mcp_server.handle_request(
        session,
        {
            "jsonrpc": "2.0",
            "id": mid,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
    )
    return resp["result"]["content"][0]["text"]


def test_mcp_goal_run_on_anthropic(monkeypatch):
    monkeypatch.setenv("GHOST_HANDS_DECIDER", "anthropic")
    monkeypatch.setenv("GHOST_HANDS_ANTHROPIC_API_KEY", "sk-test")
    fake, _ = _anthropic_stub(
        [
            json.dumps({"kind": "verify", "url": "demo.local"}),
            json.dumps({"kind": "done", "summary": "claude drove it"}),
        ]
    )
    monkeypatch.setattr("urllib.request.urlopen", fake)
    session = mcp_server.HandsSession()
    out = json.loads(_call(session, "hands_run", {"goal": "check the demo"}))
    assert out["stop_reason"] == "done", out
    assert out["summary"] == "claude drove it"
    assert out["llm_calls"] == 2
    assert out["llm_prompt_tokens"] == 100


# ---------------------------------------------------------------------------
# batch runbook
# ---------------------------------------------------------------------------


def _script(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_batch_rollup_and_exit_codes(tmp_path, capsys):
    good = _script(
        tmp_path / "good.json",
        {
            "goal": "search it",
            "steps": [
                {"kind": "type", "target": 1, "text": "ghost"},
                {"kind": "click", "target": 2},
                {"kind": "done", "summary": "found"},
            ],
        },
    )
    bad = _script(
        tmp_path / "bad.json",
        {"steps": [{"kind": "verify", "text": "definitely not on the page"}]},
    )
    rc = cli.main(["batch", good, bad])
    out = capsys.readouterr().out
    assert rc == 1
    assert "good: done" in out
    assert "bad: error" in out
    assert "BATCH: 1/2 done" in out
    rc2 = cli.main(["batch", good])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "BATCH: 1/1 done" in out2


def test_batch_invalid_script_is_reported_not_fatal(tmp_path, capsys):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    rc = cli.main(["batch", str(broken)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "INVALID SCRIPT" in out
