"""OpenAI-compatible LLM client (spec §7 pluggable backend).

The same code talks to any OpenAI-compatible server: Ollama serving a small
CPU-runnable Qwen locally (`http://localhost:11434/v1`) or vLLM on GPU in
production. Only `base_url` + `model` change. Tool-call arguments coming back
from a small model may be malformed JSON; we tolerate that (mark the call
`malformed=True` with empty args) instead of raising into the agent loop.
"""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import BaseModel, Field

from ..core.config import Settings


class LLMError(RuntimeError):
    pass


class ToolSpec(BaseModel):
    """One callable tool advertised to the model."""

    name: str
    description: str
    parameters: dict = Field(default_factory=lambda: {"type": "object"})

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict = Field(default_factory=dict)
    malformed: bool = False


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str = ""
    total_tokens: int = 0


def _parse_arguments(raw: Any) -> tuple[dict, bool]:
    """Parse tool-call args. Returns (args, malformed)."""
    if isinstance(raw, dict):
        return raw, False
    if raw is None or raw == "":
        return {}, False
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}, True
    if not isinstance(parsed, dict):
        return {}, True
    return parsed, False


class LLMClient:
    """Thin typed wrapper over an OpenAI-compatible chat-completions endpoint."""

    def __init__(self, settings: Settings | None = None, env: Any = None):
        self.settings = settings
        # LLM backend identity comes straight from the environment, not Settings
        # (LLM_BASE_URL / LLM_MODEL as GitHub variables, LLM_API_KEY as a secret).
        env = os.environ if env is None else env
        self.base_url = env.get("LLM_BASE_URL") or None
        self.model = env.get("LLM_MODEL") or None
        self.api_key = env.get("LLM_API_KEY") or None
        self._client: Any = None  # lazily constructed so unit tests can inject

    def _ensure_client(self) -> Any:
        if self._client is None:
            if not self.base_url or not self.model:
                raise LLMError(
                    "LLM backend not configured: set LLM_BASE_URL and LLM_MODEL "
                    "(e.g. http://localhost:11434/v1 and qwen2.5:3b-instruct for "
                    "local Ollama, or your vLLM endpoint/model in production)."
                )
            try:
                from openai import OpenAI
            except ImportError as e:  # pragma: no cover - dep is declared
                raise LLMError("openai package not installed") from e
            self._client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key or "not-needed",
            )
        return self._client

    def complete(
        self,
        messages: list[dict],
        tools: list[ToolSpec],
        max_tokens: int,
    ) -> LLMResponse:
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        if tools:
            kwargs["tools"] = [t.to_openai_tool() for t in tools]
            kwargs["tool_choice"] = "auto"
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:  # noqa: BLE001 - normalize any backend error
            raise LLMError(f"LLM completion failed: {e}") from e
        return self._to_response(resp)

    @staticmethod
    def _to_response(resp: Any) -> LLMResponse:
        try:
            choice = resp.choices[0]
            msg = choice.message
        except (AttributeError, IndexError) as e:
            raise LLMError(f"malformed LLM response: {e}") from e

        tool_calls: list[ToolCall] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            args, malformed = _parse_arguments(getattr(tc.function, "arguments", None))
            tool_calls.append(
                ToolCall(
                    id=getattr(tc, "id", "") or "call",
                    name=getattr(tc.function, "name", "") or "",
                    arguments=args,
                    malformed=malformed,
                )
            )

        usage = getattr(resp, "usage", None)
        total = getattr(usage, "total_tokens", 0) if usage else 0
        return LLMResponse(
            content=getattr(msg, "content", None),
            tool_calls=tool_calls,
            finish_reason=getattr(choice, "finish_reason", "") or "",
            total_tokens=int(total or 0),
        )
