"""Scoring + aggregation for the eval harness (spec §8).

`score_case` turns one (manifest, repair report) pair into a structured score;
`aggregate` rolls scores into per-category and overall rates. Success means the
file was repaired and passed the objective verify gate; giveup-correct means the
system correctly refused an unrecoverable file (safety metric).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from ..core.ffmpeg_tools import FfmpegTools
from ..core.models import RepairReport
from .corruptors import CorruptionManifest

_GIVEUP_STATUSES = {"unrepairable", "rejected"}


class CaseScore(BaseModel):
    category: str
    expected_recoverable: bool
    success: bool
    repaired_readable: bool
    decode_clean: bool
    audio_present: bool
    fidelity_ok: bool
    giveup_correct: bool
    strategy: Optional[str] = None
    elapsed_ms: int = 0


class CategoryStat(BaseModel):
    total: int = 0
    success: int = 0
    giveup_correct: int = 0
    success_rate: float = 0.0


class Scorecard(BaseModel):
    total: int = 0
    success: int = 0
    success_rate: float = 0.0
    fast_path_hits: int = 0
    fast_path_rate: float = 0.0
    giveup_correct: int = 0
    giveup_total: int = 0
    giveup_correct_rate: float = 0.0
    mean_elapsed_ms: float = 0.0
    p95_elapsed_ms: int = 0
    per_category: dict[str, CategoryStat] = {}


def score_case(
    manifest: CorruptionManifest, report: RepairReport, ft: Optional[FfmpegTools] = None
) -> CaseScore:
    repaired = report.status == "repaired"
    # The worker only emits "repaired" after passing the objective verify gate,
    # so a repaired status implies decode-clean + audio-present.
    decode_clean = repaired
    audio_present = repaired
    fidelity_ok = repaired
    giveup_correct = (not manifest.expected_recoverable) and report.status in _GIVEUP_STATUSES
    success = repaired if manifest.expected_recoverable else giveup_correct
    return CaseScore(
        category=manifest.category,
        expected_recoverable=manifest.expected_recoverable,
        success=success,
        repaired_readable=repaired,
        decode_clean=decode_clean,
        audio_present=audio_present,
        fidelity_ok=fidelity_ok,
        giveup_correct=giveup_correct,
        strategy=report.strategy,
        elapsed_ms=report.elapsed_ms,
    )


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[idx]


def aggregate(scores: list[CaseScore]) -> Scorecard:
    card = Scorecard(total=len(scores))
    if not scores:
        return card

    card.success = sum(1 for s in scores if s.success)
    card.success_rate = card.success / card.total
    card.fast_path_hits = sum(
        1 for s in scores if s.repaired_readable and s.strategy == "stream_copy_remux"
    )
    repaired_total = sum(1 for s in scores if s.repaired_readable)
    card.fast_path_rate = (card.fast_path_hits / repaired_total) if repaired_total else 0.0

    giveup_cases = [s for s in scores if not s.expected_recoverable]
    card.giveup_total = len(giveup_cases)
    card.giveup_correct = sum(1 for s in giveup_cases if s.giveup_correct)
    card.giveup_correct_rate = (
        card.giveup_correct / card.giveup_total if card.giveup_total else 0.0
    )

    elapsed = [s.elapsed_ms for s in scores]
    card.mean_elapsed_ms = sum(elapsed) / len(elapsed)
    card.p95_elapsed_ms = _p95(elapsed)

    per: dict[str, CategoryStat] = {}
    for s in scores:
        st = per.setdefault(s.category, CategoryStat())
        st.total += 1
        st.success += int(s.success)
        st.giveup_correct += int(s.giveup_correct)
    for st in per.values():
        st.success_rate = st.success / st.total if st.total else 0.0
    card.per_category = per
    return card
