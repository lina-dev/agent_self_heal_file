"""Intake routing (spec §5).

Downloads the candidate, validates it up to `repeat + 1` times (the spec's
`repeat` is an intake-only retry knob, NOT an agent-loop limit), and routes:

  * readable                 -> processing topic, status=ok
  * ≥3h / zero-byte/non-media -> processing topic, status=rejected
  * unreadable after retries -> repair topic (hand off to the repair agent)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal, Optional

from pydantic import BaseModel

from ..core.config import Settings
from ..core.ffmpeg_tools import FfmpegTools
from ..core.messaging import SnsPublisher
from ..core.s3 import S3Client, parse_s3_uri
from ..core.sandbox import JobSandbox
from ..core.taxonomy import Category
from ..core.telemetry import bind, get_logger
from . import validate as _validate

_log = get_logger("audio_repair.intake")

# Policy categories that intake rejects outright (never sent for repair).
_REJECT = {Category.DURATION_GE_3H, Category.ZERO_BYTE_OR_NONMEDIA}


@dataclass
class IntakeDeps:
    settings: Settings
    s3: S3Client
    sns: SnsPublisher
    ft: FfmpegTools


class IntakeOutcome(BaseModel):
    status: Literal["ok", "rejected", "repair_needed"]
    category: Optional[str] = None
    audio_path: Optional[str] = None
    attempts: int = 0
    published_topic: Optional[str] = None


def route(
    input_s3: str,
    settings: Settings,
    deps: IntakeDeps,
    *,
    repeat: Optional[int] = None,
) -> IntakeOutcome:
    repeat = settings.intake_repeat if repeat is None else repeat
    if repeat < 0:
        repeat = 0
    log = bind(_log, input_s3=input_s3)

    parse_s3_uri(input_s3)  # validate shape early (raises ValueError on bad uri)
    _, key = parse_s3_uri(input_s3)

    with JobSandbox(base=settings.work_dir) as sandbox:
        local = str(sandbox.resolve("intake_" + PurePosixPath(key).name))
        deps.s3.download(input_s3, local)

        result = None
        attempts = 0
        for _ in range(repeat + 1):
            attempts += 1
            result = _validate.validate_audio(deps.ft, sandbox, local, settings)
            if result.readable or (result.category in _REJECT):
                break

        category = result.category.name if result and result.category else None

        # Policy rejection is authoritative: a zero-byte / ≥3h file is never "ok".
        if result and result.category in _REJECT:
            outcome = IntakeOutcome(status="rejected", category=category, attempts=attempts)
            _publish(deps.sns, settings.processing_topic_arn,
                     {"status": "rejected", "s3_path": input_s3, "category": category}, log)
            outcome.published_topic = settings.processing_topic_arn
            return outcome

        if result and result.readable:
            outcome = IntakeOutcome(status="ok", category=category, attempts=attempts)
            _publish(deps.sns, settings.processing_topic_arn,
                     {"status": "ok", "s3_path": input_s3, "category": category}, log)
            outcome.published_topic = settings.processing_topic_arn
            return outcome

        outcome = IntakeOutcome(status="repair_needed", category=category, attempts=attempts)
        _publish(deps.sns, settings.repair_topic_arn,
                 {"s3_path": input_s3, "category": category}, log)
        outcome.published_topic = settings.repair_topic_arn
        return outcome


def _publish(sns: SnsPublisher, topic_arn: str, message: dict, log) -> None:
    if not topic_arn:
        log.warning("no topic configured; skipping publish", extra={"message": message})
        return
    try:
        sns.publish(topic_arn, message)
    except Exception as e:  # noqa: BLE001 - publish failure must not crash intake
        log.warning("intake publish failed: %s", e)
