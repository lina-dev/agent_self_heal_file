"""Environment-driven settings. No secrets in code; everything via env vars."""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel

_ENV_MAP = {
    "ffmpeg_tool_timeout_s": "FFMPEG_TOOL_TIMEOUT_S",
    "agent_max_iterations": "AGENT_MAX_ITERATIONS",
    "agent_wall_clock_timeout_s": "AGENT_WALL_CLOCK_TIMEOUT_S",
    "agent_max_tool_calls": "AGENT_MAX_TOOL_CALLS",
    "agent_max_tokens": "AGENT_MAX_TOKENS",
    "max_duration_s": "MAX_DURATION_S",
    "intake_repeat": "INTAKE_REPEAT",
    "llm_base_url": "LLM_BASE_URL",
    "llm_model": "LLM_MODEL",
    "llm_api_key": "LLM_API_KEY",
    "processing_topic_arn": "PROCESSING_TOPIC_ARN",
    "repair_topic_arn": "REPAIR_TOPIC_ARN",
    "aws_region": "AWS_REGION",
    "s3_output_bucket": "S3_OUTPUT_BUCKET",
    "work_dir": "WORK_DIR",
}


class Settings(BaseModel):
    """Immutable runtime configuration.

    Defaults match the design spec (§3, §7 safety guards). The local LLM
    default is a small CPU-runnable Qwen served by Ollama's OpenAI-compatible
    endpoint.
    """

    model_config = {"frozen": True}

    # ffmpeg tool execution
    ffmpeg_tool_timeout_s: int = 60

    # agent safety guards
    agent_max_iterations: int = 6
    agent_wall_clock_timeout_s: int = 120
    agent_max_tool_calls: int = 12
    agent_max_tokens: int = 4096

    # intake policy
    max_duration_s: int = 10800  # 3h
    intake_repeat: int = 1

    # LLM backend (OpenAI-compatible; Ollama/llama.cpp local, vLLM prod)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model: str = "qwen2.5:3b-instruct"
    llm_api_key: str = "not-needed"

    # AWS
    processing_topic_arn: str = ""
    repair_topic_arn: str = ""
    aws_region: str = "us-east-1"
    s3_output_bucket: str = ""

    # local work area
    work_dir: str = "/tmp/audio_repair"


def get_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build Settings from an environment mapping (defaults to os.environ).

    Raises ValueError (pydantic ValidationError is a subclass) if a value
    cannot be coerced to its field type.
    """
    env = os.environ if env is None else env
    data = {field: env[var] for field, var in _ENV_MAP.items() if var in env}
    return Settings(**data)
