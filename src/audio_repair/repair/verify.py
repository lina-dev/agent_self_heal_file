"""Objective repair-acceptance gate (spec §7, §8).

A repair is only accepted if the output is genuinely usable: ffprobe reads it,
a full decode pass reports zero errors, and at least one audio stream is
present. This is the single gate used by both the fast-path and the agent so
"repaired" always means the same thing.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from ..core.ffmpeg_tools import FfmpegTools


class VerifyResult(BaseModel):
    ok: bool
    decode_clean: bool
    audio_present: bool
    reason: Optional[str] = None


def verify_repaired(ft: FfmpegTools, path: str) -> VerifyResult:
    probe = ft.probe(path)
    if not probe.ok:
        return VerifyResult(ok=False, decode_clean=False, audio_present=False,
                            reason="output not probeable")
    audio_present = probe.has_audio()
    decode = ft.decode_verify(path)
    decode_clean = decode.ok
    ok = decode_clean and audio_present
    reason = None
    if not ok:
        if not audio_present:
            reason = "no audio stream in output"
        elif not decode_clean:
            reason = f"decode reported {decode.error_count} errors"
    return VerifyResult(ok=ok, decode_clean=decode_clean, audio_present=audio_present, reason=reason)
