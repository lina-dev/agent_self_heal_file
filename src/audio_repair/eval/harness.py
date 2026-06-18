"""Eval harness (spec §8).

For each corruptor: synthesize a seed, corrupt it, push it through the real
`repair_file` pipeline, and score the result. By default the harness uses
whatever LLM is in `deps` — tests pass a stub so the run is deterministic and
offline; set `RUN_LLM_INTEGRATION=1` and point the settings at a live backend
for an end-to-end agent eval. Fast-path-recoverable cases must succeed without
the LLM ever being called.
"""

from __future__ import annotations

from pathlib import Path

from ..core.config import Settings
from ..core.telemetry import get_logger
from ..repair.worker import RepairDeps, repair_file
from .corruptors import CORRUPTORS, make_seed
from .metrics import CaseScore, Scorecard, aggregate, score_case

_log = get_logger("audio_repair.eval")

# Where corrupted eval inputs are staged in S3 (the configured output bucket).
_EVAL_PREFIX = "eval-inputs"


def run_eval(seed_dir: str | Path, settings: Settings, deps: RepairDeps) -> Scorecard:
    seed_dir = Path(seed_dir)
    seed_dir.mkdir(parents=True, exist_ok=True)
    if not settings.s3_output_bucket:
        raise ValueError("settings.s3_output_bucket is required for eval staging")

    scores: list[CaseScore] = []
    for category, corruptor in CORRUPTORS.items():
        seed = make_seed(seed_dir / f"seed_{category.name}{corruptor.seed_suffix}")
        corrupted = seed_dir / f"corrupt_{category.name}{corruptor.output_suffix}"
        manifest = corruptor(seed, corrupted)

        key = f"{_EVAL_PREFIX}/{corrupted.name}"
        input_s3 = deps.s3.upload(str(corrupted), settings.s3_output_bucket, key)

        report = repair_file(
            input_s3,
            settings=settings,
            s3=deps.s3,
            sns=deps.sns,
            llm=deps.llm,
            ft=deps.ft,
        )
        score = score_case(manifest, report, deps.ft)
        scores.append(score)
        _log.info(
            "eval case scored",
            extra={"category": category.name, "success": score.success,
                   "status": report.status},
        )

    return aggregate(scores)
