"""Long-polling SQS consumer — the runtime for the ECS services.

ECS has no native SQS push-trigger, so each service runs as a long-running task
that long-polls a queue and dispatches every message to the right handler:

  * ``agent``  -> repair worker (``handle_sqs_record``)
  * ``intake`` -> validation/routing service (``handle_request``)

Design notes for production:
  * **Graceful shutdown.** ECS sends SIGTERM before stopping a task; we finish
    the in-flight batch and exit cleanly so no message is lost mid-flight.
  * **At-least-once + DLQ.** A message is deleted only after its handler
    succeeds. A raised handler leaves the message to reappear after the
    visibility timeout and, after ``maxReceiveCount``, move to the DLQ. Note
    ``repair_file`` itself never raises (an unrepairable file is a *successful*
    outcome with a report), so only genuinely un-parseable messages redrive.
  * **Testable.** The boto3 client, deps, dispatch fn and a loop bound are all
    injectable, so the loop is exercised fully offline.
"""

from __future__ import annotations

import os
import signal
from typing import Any, Callable, Optional

from .core.config import Settings, get_settings
from .core.ffmpeg_tools import FfmpegTools
from .core.messaging import SnsPublisher, parse_sns_wrapped_sqs_body
from .core.s3 import S3Client
from .core.telemetry import bind, get_logger
from .intake.router import IntakeDeps
from .intake.service import handle_request
from .llm.client import LLMClient
from .repair.worker import RepairDeps, handle_sqs_record

_log = get_logger("audio_repair.service")

# A dispatch fn takes (record, deps) and either succeeds (-> delete message) or
# raises (-> leave message for redrive/DLQ).
Dispatch = Callable[[dict, Any], Any]
DepsFactory = Callable[[Settings], Any]


def _build_repair_deps(settings: Settings) -> RepairDeps:
    return RepairDeps(
        settings=settings,
        s3=S3Client(settings.aws_region),
        sns=SnsPublisher(settings.aws_region),
        llm=LLMClient(settings),
        ft=FfmpegTools(settings),
    )


def _build_intake_deps(settings: Settings) -> IntakeDeps:
    return IntakeDeps(
        settings=settings,
        s3=S3Client(settings.aws_region),
        sns=SnsPublisher(settings.aws_region),
        ft=FfmpegTools(settings),
    )


def _dispatch_intake(record: dict, deps: IntakeDeps) -> Any:
    payload = parse_sns_wrapped_sqs_body(record.get("body", ""))
    s3_path = payload.get("s3_path") or payload.get("input_s3")
    if not s3_path:
        raise ValueError("record missing s3_path")
    return handle_request({"s3_path": s3_path, "repeat": payload.get("repeat")}, deps)


# mode -> (deps factory, dispatch fn)
_MODES: dict[str, tuple[DepsFactory, Dispatch]] = {
    "agent": (_build_repair_deps, handle_sqs_record),
    "intake": (_build_intake_deps, _dispatch_intake),
}


class _Stopper:
    """Flips to stop on SIGTERM/SIGINT so the poll loop drains and exits."""

    def __init__(self) -> None:
        self.stop = False

    def request(self, *_: Any) -> None:
        self.stop = True


def serve(
    mode: Optional[str] = None,
    queue_url: Optional[str] = None,
    *,
    settings: Optional[Settings] = None,
    sqs: Any = None,
    deps: Any = None,
    dispatch: Optional[Dispatch] = None,
    stopper: Optional[_Stopper] = None,
    max_loops: Optional[int] = None,
    wait_time_s: int = 20,
    max_messages: int = 10,
) -> int:
    """Run the consumer loop; returns the number of messages processed.

    Injecting ``sqs`` / ``deps`` / ``dispatch`` / ``stopper`` / ``max_loops``
    makes the loop fully testable offline. In production only ``mode`` (and the
    ``SQS_QUEUE_URL`` env var) are needed.
    """
    settings = settings or get_settings()

    if dispatch is None or deps is None:
        if mode not in _MODES:
            raise ValueError(f"unknown mode {mode!r}; expected one of {sorted(_MODES)}")
        build_deps, mode_dispatch = _MODES[mode]
        dispatch = dispatch or mode_dispatch
        deps = deps if deps is not None else build_deps(settings)

    queue_url = queue_url or os.environ.get("SQS_QUEUE_URL")
    if not queue_url:
        raise ValueError("queue_url is required (set SQS_QUEUE_URL)")

    if sqs is None:
        import boto3  # local import so unit tests never need boto3 configured

        sqs = boto3.client("sqs", region_name=settings.aws_region)

    # Install signal handlers only for a self-managed stopper on the main thread.
    if stopper is None:
        stopper = _Stopper()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, stopper.request)
            except (ValueError, OSError):  # not main thread / unsupported
                pass

    # Clamp to SQS hard limits so a misconfigured value can't fail every poll.
    wait_time_s = max(0, min(wait_time_s, 20))
    max_messages = max(1, min(max_messages, 10))

    log = bind(_log, mode=mode, queue_url=queue_url)
    log.info("service started", extra={"event": "service_start",
                                       "wait_time_s": wait_time_s, "max_messages": max_messages})

    processed = 0
    loops = 0
    while not stopper.stop:
        if max_loops is not None and loops >= max_loops:
            break
        loops += 1

        resp = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=max_messages,
            WaitTimeSeconds=wait_time_s,
            AttributeNames=["All"],
        )
        for msg in resp.get("Messages", []):
            mid = msg.get("MessageId")
            record = {"body": msg.get("Body", ""), "messageId": mid}
            try:
                dispatch(record, deps)
            except Exception as e:  # noqa: BLE001 - leave message for redrive/DLQ
                log.warning("record failed; leaving for redrive",
                            extra={"event": "record_error", "messageId": mid, "error": str(e)})
                continue
            try:
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
                processed += 1
            except Exception as e:  # noqa: BLE001 - a failed delete just re-delivers
                log.warning("delete failed", extra={"event": "delete_error",
                                                     "messageId": mid, "error": str(e)})

    log.info("service stopped", extra={"event": "service_stop", "processed": processed})
    return processed
