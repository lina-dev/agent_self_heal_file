"""Corruption / rejection taxonomy — single source of truth (spec §6).

26 cases across 4 tiers. Each Category carries its 1-based code, its Tier, and
whether it short-circuits the repair loop (`fail_fast`).
"""

from __future__ import annotations

from enum import Enum, IntEnum


class Tier(IntEnum):
    HARD = 1  # ffmpeg can't read
    DISCOVERY = 2  # "no audio stream found"
    INVALID_DOWNSTREAM = 3  # readable but invalid for downstream
    POLICY = 4  # policy / non-recoverable


class Category(Enum):
    """(code, tier, fail_fast) — ordering matches spec §6."""

    # Tier 1 — Hard corruption
    TRUNCATED_MISSING_MOOV = (1, Tier.HARD, True)
    DAMAGED_CONTAINER_HEADER = (2, Tier.HARD, False)
    WRONG_CONTAINER_VS_EXTENSION = (3, Tier.HARD, False)
    DAMAGED_INDEX = (4, Tier.HARD, False)
    PARTIAL_MIDSTREAM_CORRUPTION = (5, Tier.HARD, False)
    NON_MONOTONIC_DTS_PTS = (6, Tier.HARD, False)
    HEADERLESS_RAW_STREAM = (7, Tier.HARD, False)
    ZERO_BYTE_OR_NONMEDIA = (8, Tier.HARD, True)

    # Tier 2 — Stream-discovery failures
    PROBE_TOO_SHALLOW = (9, Tier.DISCOVERY, False)
    AUDIO_IN_VIDEO = (10, Tier.DISCOVERY, False)
    MULTIPLE_AUDIO_TRACKS = (11, Tier.DISCOVERY, False)
    COVER_ART_VIDEO_STREAM = (12, Tier.DISCOVERY, False)
    AUDIO_AS_DATA_STREAM = (13, Tier.DISCOVERY, False)
    CODEC_UNSUPPORTED = (14, Tier.DISCOVERY, False)

    # Tier 3 — Readable but invalid for downstream
    INCORRECT_CHANNEL_COUNT = (15, Tier.INVALID_DOWNSTREAM, False)
    UNSUPPORTED_SAMPLE_RATE = (16, Tier.INVALID_DOWNSTREAM, False)
    WRONG_SAMPLE_FORMAT = (17, Tier.INVALID_DOWNSTREAM, False)
    WRONG_CODEC_FOR_DOWNSTREAM = (18, Tier.INVALID_DOWNSTREAM, False)
    UNKNOWN_CHANNEL_LAYOUT = (19, Tier.INVALID_DOWNSTREAM, False)
    WRONG_OR_MISSING_DURATION_META = (20, Tier.INVALID_DOWNSTREAM, False)
    SILENT_ALL_ZERO_AUDIO = (21, Tier.INVALID_DOWNSTREAM, False)
    ENDIANNESS_WRONG_RAW_PCM = (22, Tier.INVALID_DOWNSTREAM, False)
    EXCESSIVE_LEADING_METADATA = (23, Tier.INVALID_DOWNSTREAM, False)

    # Tier 4 — Policy / non-recoverable
    DURATION_GE_3H = (24, Tier.POLICY, True)
    NEGATIVE_ZERO_UNKNOWN_DURATION = (25, Tier.POLICY, False)
    AUDIO_PHYSICALLY_ABSENT = (26, Tier.POLICY, True)

    def __init__(self, code: int, tier: Tier, fail_fast: bool):
        self.code = code
        self.tier = tier
        self.fail_fast = fail_fast

    @classmethod
    def by_code(cls, code: int) -> "Category":
        for c in cls:
            if c.code == code:
                return c
        raise KeyError(code)


FAIL_FAST = frozenset(c for c in Category if c.fail_fast)
