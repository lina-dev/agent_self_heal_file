"""Command-line entrypoints (spec §11 ops).

Subcommands:
  * intake --s3 <uri>    validate a file and route it (ok / rejected / repair)
  * repair --s3 <uri>    run the repair pipeline on a file
  * eval   --seed-dir D  synthesize corruptions, run the pipeline, write a scorecard

All external clients are built from environment-driven `Settings`. The class
references below (`LLMClient`, etc.) are module-level so tests can substitute
offline stubs.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .core.config import Settings, get_settings
from .core.ffmpeg_tools import FfmpegTools
from .core.messaging import SnsPublisher
from .core.s3 import S3Client
from .intake.router import IntakeDeps
from .intake.service import handle_request
from .llm.client import LLMClient
from .repair.worker import RepairDeps, repair_file


def _repair_deps(settings: Settings) -> RepairDeps:
    return RepairDeps(
        settings=settings,
        s3=S3Client(settings.aws_region),
        sns=SnsPublisher(settings.aws_region),
        llm=LLMClient(settings),
        ft=FfmpegTools(settings),
    )


def _intake_deps(settings: Settings) -> IntakeDeps:
    return IntakeDeps(
        settings=settings,
        s3=S3Client(settings.aws_region),
        sns=SnsPublisher(settings.aws_region),
        ft=FfmpegTools(settings),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="audio-repair", description="Audio repair agent")
    sub = parser.add_subparsers(dest="command")

    p_intake = sub.add_parser("intake", help="validate and route a file")
    p_intake.add_argument("--s3", required=True, help="s3://bucket/key of the input")
    p_intake.add_argument("--repeat", type=int, default=None, help="intake retry override")

    p_repair = sub.add_parser("repair", help="repair a file")
    p_repair.add_argument("--s3", required=True, help="s3://bucket/key of the input")

    p_eval = sub.add_parser("eval", help="run the eval harness")
    p_eval.add_argument("--seed-dir", required=True, help="directory for synthesized seeds")
    p_eval.add_argument("--out-dir", default="eval_out", help="scorecard output directory")

    p_serve = sub.add_parser("serve", help="run a long-polling SQS consumer (ECS service)")
    p_serve.add_argument("--mode", required=True, choices=["agent", "intake"],
                         help="agent = repair worker; intake = validation/routing")
    p_serve.add_argument("--queue-url", default=None,
                         help="SQS queue url (defaults to $SQS_QUEUE_URL)")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    settings = get_settings()

    if args.command == "intake":
        result = handle_request({"s3_path": args.s3, "repeat": args.repeat}, _intake_deps(settings))
        print(json.dumps(result, indent=2))
        return 0

    if args.command == "repair":
        deps = _repair_deps(settings)
        report = repair_file(args.s3, settings=settings, s3=deps.s3, sns=deps.sns,
                             llm=deps.llm, ft=deps.ft)
        print(report.model_dump_json(indent=2))
        return 0 if report.status in ("ok", "repaired") else 2

    if args.command == "eval":
        # Imported here so `intake`/`repair` don't pay the corruptor import cost.
        from .eval.harness import run_eval
        from .eval.report import write_scorecard

        card = run_eval(args.seed_dir, settings, _repair_deps(settings))
        paths = write_scorecard(card, args.out_dir)
        print(json.dumps({"scorecard": paths, "success_rate": card.success_rate,
                          "total": card.total}, indent=2))
        return 0

    if args.command == "serve":
        from .service import serve

        serve(args.mode, args.queue_url, settings=settings)
        return 0

    parser.print_help()
    return 1


def main_cli() -> None:
    """Console-script entrypoint declared in pyproject."""
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    main_cli()
