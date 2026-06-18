"""Intake probe helper: run ffprobe + byte sniff and classify (spec §5)."""

from __future__ import annotations

from ..core.classify import classify
from ..core.config import Settings
from ..core.ffmpeg_tools import FfmpegTools
from ..core.models import ProbeResult
from ..core.sandbox import JobSandbox
from ..core.taxonomy import Category


def probe_and_classify(
    ft: FfmpegTools, sandbox: JobSandbox, path: str, settings: Settings
) -> tuple[ProbeResult, dict, Category]:
    probe = ft.probe(path)
    sniff = ft.inspect_bytes(path)
    category = classify(probe, sniff, settings)
    return probe, sniff, category
