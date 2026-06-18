import pytest

from audio_repair.core.config import get_settings


def test_defaults():
    s = get_settings({})
    assert s.agent_max_iterations == 20
    assert s.agent_max_output_tokens == 1024
    assert s.agent_token_budget == 8192
    assert s.max_duration_s == 10800
    # LLM backend is intentionally not a Settings field (read from env by LLMClient).
    assert not hasattr(s, "llm_model")


def test_env_override_and_coercion():
    s = get_settings({"AGENT_MAX_ITERATIONS": "9", "INTAKE_REPEAT": "3"})
    assert s.agent_max_iterations == 9
    assert s.intake_repeat == 3


def test_empty_env_value_keeps_default():
    # Unset GitHub Actions variables/secrets render as empty strings; they must
    # not clobber a built-in default (e.g. the SNS topic stays "").
    s = get_settings({"S3_OUTPUT_BUCKET": ""})
    assert s.s3_output_bucket == ""


def test_rejects_invalid_int():
    with pytest.raises(ValueError):
        get_settings({"AGENT_MAX_ITERATIONS": "notanint"})


def test_frozen():
    s = get_settings({})
    with pytest.raises(Exception):
        s.agent_max_iterations = 1  # type: ignore[misc]
