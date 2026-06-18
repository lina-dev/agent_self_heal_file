import shutil

import boto3
import pytest
from moto import mock_aws

from audio_repair.core.config import Settings
from audio_repair.core.ffmpeg_tools import FfmpegTools
from audio_repair.core.s3 import S3Client
from audio_repair.eval.harness import run_eval
from audio_repair.llm.client import LLMResponse
from audio_repair.repair.worker import RepairDeps

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")

BUCKET = "eval-bucket"


class FakeSns:
    def publish(self, topic_arn, message):
        return "mid"


class StubLLM:
    """Default 'gives up' LLM so agent-path cases resolve deterministically."""

    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools, max_tokens):
        self.calls += 1
        return LLMResponse(content="no fix", tool_calls=[], total_tokens=5)


@mock_aws
def test_run_eval_deterministic_offline(tmp_path):
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
    settings = Settings(s3_output_bucket=BUCKET, work_dir=str(tmp_path / "work"))
    llm = StubLLM()
    deps = RepairDeps(settings=settings, s3=S3Client("us-east-1"), sns=FakeSns(),
                      llm=llm, ft=FfmpegTools(settings))

    card = run_eval(tmp_path / "seeds", settings, deps)

    # The full corruptor set ran.
    assert card.total == 5
    # At least one fast-path success without the LLM (wrong-container remux).
    assert card.success >= 1
    assert card.fast_path_hits >= 1
    # Zero-byte + truncated are unrecoverable and must be given up correctly.
    assert card.giveup_total >= 2
    assert card.giveup_correct == card.giveup_total
    # WRONG_CONTAINER recovered purely deterministically.
    assert card.per_category["WRONG_CONTAINER_VS_EXTENSION"].success == 1
