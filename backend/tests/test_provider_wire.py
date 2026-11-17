"""OpenAI-compatible wire contract for JSON mode + native tool calling.

Uses an httpx MockTransport behind the real ``openai`` SDK, so request shape
(tools, tool_choice, response_format) and response parsing are verified
without network or keys.  The same code path serves Gemini (OpenAI-compatible
endpoint) and Ollama.
"""

from __future__ import annotations

import json

import httpx
from openai import OpenAI
from pydantic import BaseModel

import app.services.llm.provider as provider_mod
from app.services.llm.provider import OpenAiProvider


def _provider(handler) -> tuple[OpenAiProvider, list[dict]]:
    seen: list[dict] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return handler(body)

    p = OpenAiProvider(api_key="test", model="test-model", max_retries=0)
    p._client = OpenAI(api_key="test", base_url="http://mock/v1", max_retries=0,
                       http_client=httpx.Client(transport=httpx.MockTransport(wrapped)))
    return p, seen


def _completion(message: dict, finish: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        },
    )


def test_chat_tools_parses_tool_calls(monkeypatch):
    monkeypatch.setattr(provider_mod, "_log_usage", lambda usage: None)
    p, seen = _provider(
        lambda body: _completion(
            {
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": "search_jobs", "arguments": '{"query": "python", "limit": 3}'}},
                    {"id": "call_2", "type": "function", "function": {"name": "get_profile", "arguments": "not json"}},
                ],
            },
            finish="tool_calls",
        )
    )
    tools = [{"type": "function", "function": {"name": "search_jobs", "parameters": {"type": "object"}}}]
    turn = p.chat_tools([{"role": "user", "content": "find python jobs"}], tools)

    assert seen[0]["tools"] == tools and seen[0]["tool_choice"] == "auto"
    assert [c.name for c in turn.tool_calls] == ["search_jobs", "get_profile"]
    assert turn.tool_calls[0].arguments == {"query": "python", "limit": 3}
    assert turn.tool_calls[1].parse_error and turn.tool_calls[1].arguments == {}
    assert turn.usage.prompt_tokens == 11 and turn.finish_reason == "tool_calls"


def test_chat_tools_without_tools_omits_tool_choice(monkeypatch):
    monkeypatch.setattr(provider_mod, "_log_usage", lambda usage: None)
    p, seen = _provider(lambda body: _completion({"content": "final"}))
    turn = p.chat_tools([{"role": "user", "content": "hi"}], [])
    assert "tools" not in seen[0] and "tool_choice" not in seen[0]
    assert turn.content == "final" and turn.tool_calls == []


class _Out(BaseModel):
    city: str


def test_complete_json_uses_json_mode_and_falls_back(monkeypatch):
    monkeypatch.setattr(provider_mod, "_log_usage", lambda usage: None)
    provider_mod._CACHE.clear()

    def handler(body):
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format is not supported"}})
        return _completion({"content": '```json\n{"city": "Pune"}\n```'})

    p, seen = _provider(handler)
    out, usage = p.complete_json(_Out, "Where? Return JSON.", route="t")
    assert out.city == "Pune"
    assert seen[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in seen[1]   # retried without JSON mode
    assert p._json_mode is False


def test_complete_json_repair_turn_counts_tokens(monkeypatch):
    monkeypatch.setattr(provider_mod, "_log_usage", lambda usage: None)
    provider_mod._CACHE.clear()
    replies = iter(['{"town": "x"}', '{"city": "Delhi"}'])
    p, seen = _provider(lambda body: _completion({"content": next(replies)}))
    out, usage = p.complete_json(_Out, "Where now? JSON please.", route="t2")
    assert out.city == "Delhi"
    assert len(seen) == 2 and "failed validation" in seen[1]["messages"][-1]["content"]
    assert usage.prompt_tokens == 22   # both calls counted


def test_gemini_thought_signature_is_echoed(monkeypatch):
    """Gemini 3 rejects a follow-up without the tool call's thought_signature."""
    from app.services.llm.provider import assistant_tool_message

    monkeypatch.setattr(provider_mod, "_log_usage", lambda usage: None)
    sig = {"google": {"thought_signature": "SIG123"}}
    p, _seen = _provider(
        lambda body: _completion(
            {"content": "", "tool_calls": [{"id": "c1", "type": "function", "extra_content": sig,
                                            "function": {"name": "get_profile", "arguments": "{}"}}]},
            finish="tool_calls",
        )
    )
    turn = p.chat_tools([{"role": "user", "content": "me?"}], [{"type": "function", "function": {"name": "get_profile", "parameters": {"type": "object"}}}])
    assert turn.tool_calls[0].extra_content == sig
    msg = assistant_tool_message(turn)
    assert msg["tool_calls"][0]["extra_content"] == sig
    assert msg["tool_calls"][0]["function"]["arguments"] == "{}"
