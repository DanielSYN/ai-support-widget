"""The Claude call itself, with the API client replaced by a fake."""

import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app import llm


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def install_fake(monkeypatch, **kwargs):
    messages = FakeMessages(**kwargs)
    monkeypatch.setattr(llm, "_client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=messages)))
    return messages


def response(text, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])


BOT = {"name": "Acme", "extra_info": "Open 9-5."}
FAQS = [{"question": "Returns?", "answer": "30 days."}]
HISTORY = [{"role": "user", "content": "Can I return this?"}]


def test_sends_knowledge_in_a_cached_system_prompt_and_parses_the_reply(monkeypatch):
    fake = install_fake(monkeypatch, response=response(json.dumps(
        {"reply": "Yes, within 30 days.", "answered_from_knowledge": True, "needs_human": False})))

    reply = llm.claude_reply(llm.build_system_prompt(BOT, FAQS), HISTORY)

    assert reply == llm.Reply("Yes, within 30 days.", answered=True, needs_human=False)
    call = fake.calls[0]
    system = call["system"][0]
    assert "Q: Returns?\nA: 30 days." in system["text"] and "Open 9-5." in system["text"]
    assert system["cache_control"] == {"type": "ephemeral"}
    assert call["output_config"]["format"]["schema"] == llm.REPLY_SCHEMA
    assert call["fallbacks"] == "default"
    assert call["messages"] == HISTORY


def test_refusal_offers_a_human(monkeypatch):
    install_fake(monkeypatch, response=response("", stop_reason="refusal"))
    reply = llm.claude_reply("system", HISTORY)
    assert reply.needs_human and not reply.answered


def test_truncated_json_falls_back_safely(monkeypatch):
    install_fake(monkeypatch, response=response('{"reply": "Yes, wit', stop_reason="max_tokens"))
    assert llm.claude_reply("system", HISTORY).text == llm.FALLBACK_REPLY


@pytest.mark.parametrize("error", [
    anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")),
    anthropic.AuthenticationError(
        "bad key", response=httpx2.Response(401, request=httpx2.Request("POST", "https://api.anthropic.com")),
        body=None),
])
def test_api_errors_become_llm_unavailable(monkeypatch, error):
    install_fake(monkeypatch, error=error)
    with pytest.raises(llm.LLMUnavailable):
        llm.claude_reply("system", HISTORY)


def test_empty_knowledge_base_is_explicit():
    assert "(No information has been added yet.)" in llm.build_system_prompt({"name": "X", "extra_info": ""}, [])
