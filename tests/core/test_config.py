import pytest

from audio_repair.core.config import get_settings


def test_defaults():
    s = get_settings({})
    assert s.agent_max_iterations == 6
    assert s.max_duration_s == 10800
    assert s.llm_model == "qwen2.5:3b-instruct"
    assert s.llm_base_url == "http://localhost:11434/v1"


def test_env_override_and_coercion():
    s = get_settings({"AGENT_MAX_ITERATIONS": "9", "LLM_MODEL": "qwen2.5:7b-instruct"})
    assert s.agent_max_iterations == 9
    assert s.llm_model == "qwen2.5:7b-instruct"


def test_rejects_invalid_int():
    with pytest.raises(ValueError):
        get_settings({"AGENT_MAX_ITERATIONS": "notanint"})


def test_frozen():
    s = get_settings({})
    with pytest.raises(Exception):
        s.agent_max_iterations = 1  # type: ignore[misc]
