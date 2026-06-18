"""Repair orchestration + publish (spec §4, §5, §10).

`repair_file` is the heart of the repair service. It is deterministic-first:
classify → (fail-fast guard) → cheap stream-copy fast-path → constrained agent
on the residual → objective verify gate → publish. All external clients are
injected for testability, and the function never raises: any unexpected error
becomes an `unrepairable` report so a poisoned message can't crash the worker
(and the caller can route it to a DLQ safely).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Optional

from ..core.classify import classify
from ..core.config import Settings
from ..core.ffmpeg_tools import FfmpegTools
from ..core.messaging import SnsPublisher, parse_sns_wrapped_sqs_body
from ..core.models import RepairReport, ToolResult
from ..core.s3 import S3Client, parse_s3_uri
from ..core.sandbox import JobSandbox
from ..core.taxonomy import Category
from ..core.telemetry import bind, get_logger
from ..llm.client import LLMClient
from .agent import run_agent
from .fastpath import try_fastpath
from .tools import ToolRegistry
from .verify import verify_repaired

_log = get_logger("audio_repair.worker")


@dataclass
class RepairDeps:
    settings: Settings
    s3: S3Client
    sns: SnsPublisher
    llm: LLMClient
    ft: FfmpegTools


def _ext_of(path: str) -> str:
    return (Path(path).suffix.lstrip(".") or "bin")


def repair_file(
    input_s3: str,
    *,
    settings: Settings,
    s3: S3Client,
    sns: SnsPublisher,
    llm: LLMClient,
    ft: FfmpegTools,
) -> RepairReport:
    correlation_id = uuid.uuid4().hex[:12]
    log = bind(_log, correlation_id=correlation_id, input_s3=input_s3)
    start = time.monotonic()
    report = RepairReport(status="unrepairable", input_s3=input_s3)

    try:
        bucket, key = parse_s3_uri(input_s3)
        with JobSandbox(base=settings.work_dir) as sandbox:
            local_in = str(sandbox.resolve("input_" + PurePosixPath(key).name))
            s3.download(input_s3, local_in)

            probe = ft.probe(local_in)
            sniff = ft.inspect_bytes(local_in)
            category = classify(probe, sniff, settings)
            report.category = category.name
            report.original_params = {
                "format_name": probe.format_name,
                "duration_s": probe.duration_s,
                "size": sniff.get("size"),
                "container_guess": sniff.get("container_guess"),
            }
            log.info("classified", extra={"category": category.name})

            # 1. Fail-fast policy/non-recoverable categories: no repair attempt.
            if category.fail_fast:
                report.status = "unrepairable"
                report.reason = f"fail-fast category {category.name}"
                return _finalize(report, start, settings, s3, sns, key, log, output_path=None)

            # 2. Deterministic fast-path.
            fp = try_fastpath(ft, sandbox, local_in)
            report.attempts.extend(fp.attempts)
            if fp.ok and fp.output_path and verify_repaired(ft, fp.output_path).ok:
                report.status = "repaired"
                report.strategy = fp.strategy
                return _finalize(report, start, settings, s3, sns, key, log,
                                 output_path=fp.output_path)

            # 3. Constrained agent on the residual.
            registry = ToolRegistry(ft, sandbox, local_in)
            ctx = (
                f"Container guess: {sniff.get('container_guess')}. "
                f"Probe ok: {probe.ok}. Size: {sniff.get('size')} bytes."
            )
            outcome = run_agent(llm, registry, ft, local_in, settings, category,
                                initial_context=ctx)
            report.attempts.extend(outcome.attempts)
            report.strategy = f"agent:{outcome.stop_reason}"
            if outcome.repaired and outcome.output_path and verify_repaired(ft, outcome.output_path).ok:
                report.status = "repaired"
                return _finalize(report, start, settings, s3, sns, key, log,
                                 output_path=outcome.output_path)

            report.status = "unrepairable"
            report.reason = f"agent stop_reason={outcome.stop_reason}"
            return _finalize(report, start, settings, s3, sns, key, log, output_path=None)

    except Exception as e:  # noqa: BLE001 - worker must never crash
        log.warning("repair failed with exception: %s", e)
        report.status = "unrepairable"
        report.reason = f"internal error: {e}"
        report.elapsed_ms = int((time.monotonic() - start) * 1000)
        _safe_publish(sns, settings, report, log)
        return report


def _finalize(
    report: RepairReport,
    start: float,
    settings: Settings,
    s3: S3Client,
    sns: SnsPublisher,
    src_key: str,
    log,
    *,
    output_path: Optional[str],
) -> RepairReport:
    if report.status == "repaired" and output_path and settings.s3_output_bucket:
        ext = _ext_of(output_path)
        out_key = S3Client.repaired_key(src_key, ext)
        try:
            report.output_s3 = s3.upload(output_path, settings.s3_output_bucket, out_key)
        except Exception as e:  # noqa: BLE001
            log.warning("output upload failed: %s", e)
            report.status = "unrepairable"
            report.reason = f"upload failed: {e}"

    report.elapsed_ms = int((time.monotonic() - start) * 1000)

    # Upload the report JSON alongside the output (best effort).
    if settings.s3_output_bucket:
        try:
            rep_key = S3Client.report_key(src_key)
            tmp = Path(settings.work_dir) / f"report_{uuid.uuid4().hex[:8]}.json"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(report.model_dump_json(indent=2))
            report.report_s3 = s3.upload(str(tmp), settings.s3_output_bucket, rep_key)
            tmp.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            log.warning("report upload failed: %s", e)

    _safe_publish(sns, settings, report, log)
    log.info("repair complete", extra={"status": report.status})
    return report


def _safe_publish(sns: SnsPublisher, settings: Settings, report: RepairReport, log) -> None:
    if not settings.processing_topic_arn:
        return
    message = {
        "status": report.status,
        "input_s3": report.input_s3,
        "audio_s3_path": report.output_s3,
        "category": report.category,
        "reason": report.reason,
    }
    try:
        sns.publish(settings.processing_topic_arn, message)
    except Exception as e:  # noqa: BLE001 - publish failure must not crash worker
        log.warning("publish failed: %s", e)


def handle_sqs_record(record: dict, deps: RepairDeps) -> RepairReport:
    """Parse one SQS record (SNS-wrapped) and run the repair."""
    body = record.get("body", "")
    payload = parse_sns_wrapped_sqs_body(body)
    input_s3 = payload.get("s3_path") or payload.get("input_s3")
    if not input_s3:
        raise ValueError("record missing s3_path")
    return repair_file(
        input_s3,
        settings=deps.settings,
        s3=deps.s3,
        sns=deps.sns,
        llm=deps.llm,
        ft=deps.ft,
    )
