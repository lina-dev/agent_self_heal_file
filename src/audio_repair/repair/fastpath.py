"""Deterministic stream-copy fast-path (spec §10 deterministic-first).

Before spending an LLM call, try the single cheapest repair that fixes the
common case (damaged index / wrong container / non-monotonic timestamps): copy
the audio stream into a clean container while regenerating timestamps, then
verify the result decodes cleanly. One attempt, no ladder — if it doesn't work
we hand off to the constrained agent.
"""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel

from ..core.ffmpeg_tools import FfmpegTools, RemuxOpts
from ..core.models import ToolResult
from ..core.sandbox import JobSandbox


class FastPathResult(BaseModel):
    ok: bool
    output_path: Optional[str] = None
    strategy: str = "stream_copy_remux"
    attempts: list[ToolResult] = []


def try_fastpath(ft: FfmpegTools, sandbox: JobSandbox, input_path: str) -> FastPathResult:
    out = str(sandbox.resolve(f"fastpath_{uuid.uuid4().hex[:8]}.mka"))
    opts = RemuxOpts(fflags="+genpts", map_audio_only=True)
    remux = ft.remux(input_path, out, opts)
    attempts = [remux]

    if not remux.ok or not remux.output_path:
        return FastPathResult(ok=False, attempts=attempts)

    verify = ft.decode_verify(remux.output_path)
    if not verify.ok:
        return FastPathResult(ok=False, output_path=remux.output_path, attempts=attempts)

    probe = ft.probe(remux.output_path)
    ok = probe.ok and probe.has_audio()
    return FastPathResult(ok=ok, output_path=remux.output_path, attempts=attempts)
