"""Bounded, constrained LLM repair agent (spec §7 safety guards).

The agent only ever emits typed tool calls (validated by `ToolRegistry`); it can
never run a shell. Every loop is fenced by hard limits so a confused small model
cannot burn resources or loop forever:

  * ``agent_max_iterations``       - max LLM round-trips
  * ``agent_max_tool_calls``       - max total tool invocations
  * ``agent_wall_clock_timeout_s`` - real-time budget (injectable clock)
  * ``agent_token_budget``         - cumulative completion-token budget
  * no-progress detection          - identical ``(tool, args)`` repeated, or the
                                     decode-error count failing to improve

Every exit from the loop is an explicit, logged :class:`StopReason` — there are
no silent ``break``s. Each run emits a structured ``agent_start`` / ``agent_stop``
pair (plus per-round/per-call debug records) carrying a correlation id, so a
repair can be traced end-to-end in a log aggregator.

Success is decided by one objective gate used everywhere: the produced file must
decode cleanly AND still contain audio.
"""

from __future__ import annotations

import json
import time
import uuid
from enum import StrEnum
from typing import Callable, Optional

from pydantic import BaseModel

from ..core.config import Settings
from ..core.ffmpeg_tools import FfmpegTools
from ..core.models import ToolResult
from ..core.taxonomy import Category
from ..core.telemetry import bind, get_logger
from ..llm.client import LLMClient
from .tools import ToolRegistry

_log = get_logger("audio_repair.agent")

_SYSTEM = """You are an audio-file repair agent. A media file failed to decode.
Use ONLY the provided tools to recover a clean, decodable audio file. Prefer the
cheapest fix first (remux / stream-copy) before re-encoding. Each tool call must
use valid arguments. When a tool produces an output that decodes cleanly with
audio present, you are done. Do not repeat an identical call. Stop if you cannot
make progress."""


class StopReason(StrEnum):
    """Why the agent loop terminated. Compares equal to its string value."""

    REPAIRED = "repaired"
    WALL_CLOCK = "wall_clock"
    MAX_ITERATIONS = "max_iterations"
    TOKEN_BUDGET = "token_budget"
    MAX_TOOL_CALLS = "max_tool_calls"
    LLM_ERROR = "llm_error"
    NO_TOOL_CALLS = "no_tool_calls"
    NO_PROGRESS = "no_progress"


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
    """Objective success gate: decodes cleanly AND still has an audio stream."""
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
    """Run the constrained repair loop and return a fully-populated outcome.

    The loop never raises and never exits silently: every return path goes
    through :func:`finish`, which records the :class:`StopReason` and emits a
    structured ``agent_stop`` log line.
    """
    run_id = uuid.uuid4().hex[:12]
    log = bind(
        _log,
        run_id=run_id,
        category=category.name,
        tier=category.tier.value,
    )

    specs = registry.specs()
    start = now()
    deadline = start + settings.agent_wall_clock_timeout_s

    outcome = AgentOutcome()
    seen: set[str] = set()
    best_error_count: Optional[int] = None
    rounds_without_improvement = 0

    def finish(reason: StopReason, **fields) -> AgentOutcome:
        """Single termination point: stamp the reason and log a summary."""
        outcome.stop_reason = reason
        log.info(
            "agent stopped",
            extra={
                "event": "agent_stop",
                "reason": str(reason),
                "repaired": outcome.repaired,
                "iterations": outcome.iterations,
                "tool_calls": len(outcome.attempts),
                "tokens": outcome.tokens,
                "elapsed_ms": int((now() - start) * 1000),
                **fields,
            },
        )
        return outcome

    log.info(
        "agent started",
        extra={
            "event": "agent_start",
            "input_path": input_path,
            "max_iterations": settings.agent_max_iterations,
            "max_tool_calls": settings.agent_max_tool_calls,
            "token_budget": settings.agent_token_budget,
            "wall_clock_s": settings.agent_wall_clock_timeout_s,
        },
    )

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

    while True:
        # --- pre-round budget guards -------------------------------------
        if now() >= deadline:
            return finish(StopReason.WALL_CLOCK)
        if outcome.iterations >= settings.agent_max_iterations:
            return finish(StopReason.MAX_ITERATIONS)
        if outcome.tokens >= settings.agent_token_budget:
            return finish(StopReason.TOKEN_BUDGET)

        outcome.iterations += 1
        log.debug(
            "agent round",
            extra={"event": "agent_round", "iteration": outcome.iterations,
                   "tokens": outcome.tokens},
        )

        # --- ask the model ----------------------------------------------
        try:
            resp = llm.complete(messages, specs, settings.agent_max_output_tokens)
        except Exception as e:  # noqa: BLE001 - LLMError or any backend failure
            log.warning(
                "llm completion failed",
                extra={"event": "llm_error", "error": str(e)},
            )
            return finish(StopReason.LLM_ERROR, error=str(e))

        outcome.tokens += resp.total_tokens

        if not resp.tool_calls:
            # Model produced prose instead of a tool call: it has given up.
            return finish(StopReason.NO_TOOL_CALLS)

        # Record the assistant turn so the model retains conversation state.
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
            # --- per-call guards ----------------------------------------
            if len(outcome.attempts) >= settings.agent_max_tool_calls:
                return finish(StopReason.MAX_TOOL_CALLS)

            if tc.malformed:
                # Tolerate a small model emitting bad JSON: feed the error back
                # as a tool result rather than crashing the loop.
                result = {"ok": False, "error": "malformed tool-call arguments"}
            else:
                sig = _signature(tc.name, tc.arguments)
                if sig in seen:
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id,
                         "content": json.dumps({"ok": False, "error": "duplicate call"})}
                    )
                    return finish(StopReason.NO_PROGRESS, tool=tc.name, detail="duplicate_call")
                seen.add(sig)
                # ToolRegistry.invoke is contractually no-raise: it returns a
                # {"ok": False, "error": ...} dict on any failure.
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
                {"role": "tool", "tool_call_id": tc.id,
                 "content": json.dumps(result, default=str)}
            )
            log.debug(
                "tool result",
                extra={"event": "tool_result", "tool": tc.name,
                       "ok": bool(result.get("ok")), "error": result.get("error")},
            )

            # Track decode-error improvement for no-progress detection.
            ec = result.get("error_count")
            if isinstance(ec, int):
                round_had_error_count = True
                if best_error_count is None or ec < best_error_count:
                    best_error_count = ec
                    round_improved = True

            # --- objective success gate ---------------------------------
            out = result.get("output_path")
            if out and _verify_success(ft, out):
                outcome.repaired = True
                outcome.output_path = out
                return finish(StopReason.REPAIRED, output_path=out, tool=tc.name)

        # Only the *decode-error-stagnation* form of no-progress applies here;
        # rounds that never measured decode errors are left to the iteration cap.
        if round_had_error_count:
            if round_improved:
                rounds_without_improvement = 0
            else:
                rounds_without_improvement += 1
                if rounds_without_improvement >= 2:
                    return finish(StopReason.NO_PROGRESS, detail="error_count_stagnation")
