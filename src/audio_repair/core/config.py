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
    "agent_max_output_tokens": "AGENT_MAX_OUTPUT_TOKENS",
    "agent_token_budget": "AGENT_TOKEN_BUDGET",
    "max_duration_s": "MAX_DURATION_S",
    "intake_repeat": "INTAKE_REPEAT",
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
    agent_max_iterations: int = 20
    agent_wall_clock_timeout_s: int = 120
    agent_max_tool_calls: int = 12
    # Per-completion output ceiling: tool-call JSON is tiny, so this only needs
    # to be large enough for one tool call plus brief reasoning.
    agent_max_output_tokens: int = 8000
    # Cumulative completion-token budget across the whole repair: a cost/abuse
    # fence, not a context-window limit. Exhausting it means "give up".
    agent_token_budget: int = 32000

    # intake policy
    max_duration_s: int = 10800  # 3h
    intake_repeat: int = 1

    # NOTE: the LLM backend (base url / model / api key) is intentionally NOT
    # part of Settings. It is read directly from the environment by LLMClient
    # (LLM_BASE_URL, LLM_MODEL as GitHub variables; LLM_API_KEY as a secret).

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
    # Skip missing *and* empty values: an unset GitHub Actions variable/secret
    # (`${{ vars.X }}`) arrives as an empty string, which must not clobber a
    # sensible default (e.g. the localhost LLM base url).
    data = {field: env[var] for field, var in _ENV_MAP.items() if env.get(var)}
    return Settings(**data)
