"""Bounded, constrained LLM repair agent (spec §7 safety guards).

The agent only ever emits typed tool calls (validated by `ToolRegistry`); it can
never run a shell. Every loop is fenced by hard limits so a confused small model
cannot burn resources or loop forever:

  * `agent_max_iterations`   - max LLM round-trips
  * `agent_max_tool_calls`   - max total tool invocations
  * `agent_wall_clock_timeout_s` - real-time budget (injectable clock)
  * token ceiling            - cumulative completion tokens
  * no-progress detection    - identical (tool, args) repeated, or decode-error
                               count not improving across two rounds

Success is decided by the same objective gate used everywhere: the produced
file must decode cleanly AND still contain audio.
"""

from __future__ import annotations

import json
import time
from typing import Callable, Optional

from pydantic import BaseModel

from ..core.config import Settings
from ..core.ffmpeg_tools import FfmpegTools
from ..core.models import ToolResult
from ..core.telemetry import get_logger
from ..llm.client import LLMClient
from ..core.taxonomy import Category
from .tools import ToolRegistry

_log = get_logger("audio_repair.agent")

_SYSTEM = """You are an audio-file repair agent. A media file failed to decode.
Use ONLY the provided tools to recover a clean, decodable audio file. Prefer the
cheapest fix first (remux / stream-copy) before re-encoding. Each tool call must
use valid arguments. When a tool produces an output that decodes cleanly with
audio present, you are done. Do not repeat an identical call. Stop if you cannot
make progress."""


class AgentOutcome(BaseModel):
    repaired: bool = False
    output_path: Optional[str] = None
    attempts: list[ToolResult] = []
    iterations: int = 0
    tokens: int = 0
    stop_reason: str = ""


def _signature(name: str, args: dict) -> str:
    return name + "::" + json.dumps(args, sort_keys=True, default=str)


def _verify_success(ft: FfmpegTools, path: str) -> bool:
    decode = ft.decode_verify(path)
    if not decode.ok:
        return False
    probe = ft.probe(path)
    return probe.ok and probe.has_audio()


def run_agent(
    llm: LLMClient,
    registry: ToolRegistry,
    ft: FfmpegTools,
    input_path: str,
    settings: Settings,
    category: Category,
    *,
    now: Callable[[], float] = time.monotonic,
    initial_context: str = "",
) -> AgentOutcome:
    specs = registry.specs()
    start = now()
    deadline = start + settings.agent_wall_clock_timeout_s

    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"Failure category: {category.name} (tier {category.tier.value}).\n"
                f"{initial_context}\n"
                "Recover the audio. Begin."
            ),
        },
    ]

    outcome = AgentOutcome()
    seen: set[str] = set()
    best_error_count: Optional[int] = None
    rounds_without_improvement = 0

    while True:
        # --- pre-round guards ---
        if now() >= deadline:
            outcome.stop_reason = "wall_clock"
            break
        if outcome.iterations >= settings.agent_max_iterations:
            outcome.stop_reason = "max_iterations"
            break
        if outcome.tokens >= settings.agent_max_tokens:
            outcome.stop_reason = "token_limit"
            break

        outcome.iterations += 1

        try:
            resp = llm.complete(messages, specs, settings.agent_max_tokens)
        except Exception as e:  # noqa: BLE001 - LLMError or backend failure
            _log.warning("llm completion failed: %s", e)
            outcome.stop_reason = "llm_error"
            break

        outcome.tokens += resp.total_tokens

        if not resp.tool_calls:
            outcome.stop_reason = "no_tool_calls"
            break

        # Record assistant turn so the model retains conversation state.
        messages.append(
            {
                "role": "assistant",
                "content": resp.content or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in resp.tool_calls
                ],
            }
        )

        round_improved = False
        round_had_error_count = False
        for tc in resp.tool_calls:
            # --- per-call guards ---
            if len(outcome.attempts) >= settings.agent_max_tool_calls:
                outcome.stop_reason = "max_tool_calls"
                return outcome

            if tc.malformed:
                result = {"ok": False, "error": "malformed tool-call arguments"}
            else:
                sig = _signature(tc.name, tc.arguments)
                if sig in seen:
                    outcome.stop_reason = "no_progress"
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id,
                         "content": json.dumps({"ok": False, "error": "duplicate call"})}
                    )
                    return outcome
                seen.add(sig)
                result = registry.invoke(tc.name, tc.arguments)

            outcome.attempts.append(
                ToolResult(
                    tool=tc.name,
                    ok=bool(result.get("ok")),
                    output_path=result.get("output_path"),
                    error=result.get("error"),
                )
            )
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)}
            )

            # Track decode-error improvement for no-progress detection.
            ec = result.get("error_count")
            if isinstance(ec, int):
                round_had_error_count = True
                if best_error_count is None or ec < best_error_count:
                    best_error_count = ec
                    round_improved = True

            # --- success gate ---
            out = result.get("output_path")
            if out and _verify_success(ft, out):
                outcome.repaired = True
                outcome.output_path = out
                outcome.stop_reason = "repaired"
                return outcome

        # Only the *decode-error-stagnation* form of no-progress applies here;
        # rounds that never measured decode errors are left to the iteration cap.
        if round_had_error_count:
            if round_improved:
                rounds_without_improvement = 0
            else:
                rounds_without_improvement += 1
                if rounds_without_improvement >= 2:
                    outcome.stop_reason = "no_progress"
                    break

    return outcome
