"""Audio validation (spec §5).

Validation is stricter than classification: a file is *readable* only if we can
actually extract its audio stream and decode it without errors. The length gate
runs first (a ≥3h file is rejected by policy regardless of integrity).
"""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict

from ..core.config import Settings
from ..core.ffmpeg_tools import ExtractOpts, FfmpegTools
from ..core.models import ProbeResult
from ..core.sandbox import JobSandbox
from ..core.taxonomy import Category
from .probe import probe_and_classify


class ValidationResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    readable: bool
    audio_path: Optional[str] = None
    category: Optional[Category] = None
    probe: ProbeResult


def validate_audio(
    ft: FfmpegTools, sandbox: JobSandbox, path: str, settings: Settings
) -> ValidationResult:
    probe = ft.probe(path)

    # Length gate first — policy rejection independent of integrity.
    if probe.duration_s is not None and probe.duration_s >= settings.max_duration_s:
        return ValidationResult(
            readable=False, audio_path=None, category=Category.DURATION_GE_3H, probe=probe
        )

    _, _, category = probe_and_classify(ft, sandbox, path, settings)

    out = str(sandbox.resolve(f"validate_{uuid.uuid4().hex[:8]}.mka"))
    extract = ft.extract_audio(path, out, ExtractOpts(audio_codec="copy"))
    if not extract.ok or not extract.output_path:
        return ValidationResult(readable=False, audio_path=None, category=category, probe=probe)

    decode = ft.decode_verify(extract.output_path)
    readable = decode.ok
    return ValidationResult(
        readable=readable,
        audio_path=extract.output_path if readable else None,
        category=category,
        probe=probe,
    )
