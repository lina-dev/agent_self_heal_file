"""Map a probe result + byte sniff to a taxonomy Category (spec §6).

Pure function, no I/O. Rules are an ordered table; first match wins. The goal is
not perfect forensic classification but a useful routing/short-circuit decision
and a label for logs, metrics and eval.
"""

from __future__ import annotations

from .config import Settings
from .models import ProbeResult
from .taxonomy import Category

# Substrings seen in ffmpeg/ffprobe stderr → category. Order matters.
_STDERR_RULES: list[tuple[str, Category]] = [
    ("moov atom not found", Category.TRUNCATED_MISSING_MOOV),
    ("could not find corresponding trex", Category.TRUNCATED_MISSING_MOOV),
    ("error reading header", Category.DAMAGED_CONTAINER_HEADER),
    ("invalid data found when processing input", Category.DAMAGED_CONTAINER_HEADER),
    ("non monotonically increasing dts", Category.NON_MONOTONIC_DTS_PTS),
    ("non-monotonic dts", Category.NON_MONOTONIC_DTS_PTS),
    ("invalid timestamp", Category.NON_MONOTONIC_DTS_PTS),
    ("error while decoding stream", Category.PARTIAL_MIDSTREAM_CORRUPTION),
    ("invalid nal", Category.PARTIAL_MIDSTREAM_CORRUPTION),
    ("decoder not found", Category.CODEC_UNSUPPORTED),
    ("unknown codec", Category.CODEC_UNSUPPORTED),
]


def classify(probe: ProbeResult, sniff: dict, settings: Settings) -> Category:
    size = sniff.get("size", 0)
    container_guess = sniff.get("container_guess")

    # 1. empty / non-media bytes -> fail fast
    if size == 0:
        return Category.ZERO_BYTE_OR_NONMEDIA
    if not probe.ok and container_guess is None:
        return Category.ZERO_BYTE_OR_NONMEDIA

    # 2. policy: length gate (only meaningful when we have a duration)
    if probe.duration_s is not None and probe.duration_s >= settings.max_duration_s:
        return Category.DURATION_GE_3H

    # 3. readable cases
    if probe.ok:
        if probe.has_audio():
            if probe.duration_s is None:
                return Category.WRONG_OR_MISSING_DURATION_META
            if probe.duration_s <= 0:
                return Category.NEGATIVE_ZERO_UNKNOWN_DURATION
            a = probe.audio_streams[0]
            if a.channels is not None and a.channels == 0:
                return Category.INCORRECT_CHANNEL_COUNT
            if a.channel_layout in (None, "", "unknown"):
                return Category.UNKNOWN_CHANNEL_LAYOUT
            # readable + valid: not a defect; report as duration-meta as a benign label
            return Category.WRONG_OR_MISSING_DURATION_META
        # readable but no audio stream
        if probe.video_streams:
            return Category.AUDIO_IN_VIDEO
        return Category.AUDIO_PHYSICALLY_ABSENT

    # 4. unreadable: consult stderr rule table
    stderr = (probe.stderr or "").lower() + " " + (probe.error or "").lower()
    for needle, category in _STDERR_RULES:
        if needle in stderr:
            return category

    # 5. wrong container vs extension (sniff disagrees, but bytes look like media)
    if container_guess is not None:
        return Category.WRONG_CONTAINER_VS_EXTENSION

    # 6. fallback for unreadable-but-has-bytes
    return Category.DAMAGED_CONTAINER_HEADER
