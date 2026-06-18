from unittest.mock import MagicMock

import pytest

from audio_repair.core.config import get_settings
from audio_repair.llm.client import LLMClient, LLMError, ToolSpec


def _fake_openai(tool_name, args_json, total_tokens=33, content=None):
    fake = MagicMock()
    msg = MagicMock()
    msg.content = content
    if tool_name is None:
        msg.tool_calls = []
    else:
        tc = MagicMock()
        tc.id = "call_1"
        tc.function.name = tool_name
        tc.function.arguments = args_json
        msg.tool_calls = [tc]
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "tool_calls" if tool_name else "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.total_tokens = total_tokens
    fake.chat.completions.create.return_value = resp
    return fake


def test_parses_tool_call():
    c = LLMClient(get_settings({}))
    c._client = _fake_openai("probe", '{"path": "/x.mp4"}')
    r = c.complete(
        [{"role": "user", "content": "fix"}],
        [ToolSpec(name="probe", description="d", parameters={"type": "object"})],
        256,
    )
    assert r.tool_calls[0].name == "probe"
    assert r.tool_calls[0].arguments == {"path": "/x.mp4"}
    assert r.tool_calls[0].malformed is False
    assert r.total_tokens == 33
    assert r.finish_reason == "tool_calls"


def test_malformed_args_tolerated():
    c = LLMClient(get_settings({}))
    c._client = _fake_openai("probe", "{not json")
    r = c.complete([{"role": "user", "content": "x"}], [], 256)
    assert r.tool_calls[0].malformed is True
    assert r.tool_calls[0].arguments == {}


def test_non_object_args_tolerated():
    c = LLMClient(get_settings({}))
    c._client = _fake_openai("probe", "[1, 2, 3]")
    r = c.complete([{"role": "user", "content": "x"}], [], 256)
    assert r.tool_calls[0].malformed is True


def test_plain_text_completion():
    c = LLMClient(get_settings({}))
    c._client = _fake_openai(None, None, content="all good")
    r = c.complete([{"role": "user", "content": "x"}], [], 64)
    assert r.content == "all good"
    assert r.tool_calls == []


def test_backend_error_wrapped():
    c = LLMClient(get_settings({}))
    fake = MagicMock()
    fake.chat.completions.create.side_effect = RuntimeError("connection refused")
    c._client = fake
    with pytest.raises(LLMError):
        c.complete([{"role": "user", "content": "x"}], [], 64)


def test_tool_spec_to_openai():
    spec = ToolSpec(name="remux", description="copy", parameters={"type": "object"})
    d = spec.to_openai_tool()
    assert d["type"] == "function"
    assert d["function"]["name"] == "remux"


def test_unconfigured_backend_raises():
    # No LLM_BASE_URL / LLM_MODEL in the environment -> fail loudly when used.
    c = LLMClient(env={})
    with pytest.raises(LLMError, match="LLM backend not configured"):
        c.complete([{"role": "user", "content": "x"}], [], 64)


def test_empty_env_values_treated_as_unset():
    # Unset GitHub variables render as "" — must not count as configured.
    c = LLMClient(env={"LLM_BASE_URL": "", "LLM_MODEL": ""})
    assert c.base_url is None and c.model is None


def test_reads_backend_from_env():
    c = LLMClient(env={"LLM_BASE_URL": "http://vllm:8000/v1",
                       "LLM_MODEL": "qwen2.5:7b-instruct", "LLM_API_KEY": "sk-x"})
    assert c.base_url == "http://vllm:8000/v1"
    assert c.model == "qwen2.5:7b-instruct"
    assert c.api_key == "sk-x"


@pytest.mark.integration
def test_live_ollama_roundtrip():
    import os

    if os.environ.get("RUN_LLM_INTEGRATION") != "1":
        pytest.skip("set RUN_LLM_INTEGRATION=1 to hit a live Ollama")
    c = LLMClient(get_settings(os.environ))
    r = c.complete([{"role": "user", "content": "say hi"}], [], 16)
    assert isinstance(r.content, str)
