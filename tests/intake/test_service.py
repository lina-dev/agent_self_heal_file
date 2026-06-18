import os

import boto3
import pytest
from moto import mock_aws

from audio_repair.core.config import Settings
from audio_repair.core.models import DecodeVerifyResult, ProbeResult, StreamInfo, ToolResult
from audio_repair.core.s3 import S3Client
from audio_repair.intake.router import IntakeDeps
from audio_repair.intake.service import handle_request

ACCOUNT = os.environ.get("AWS_ACCOUNT_ID") or "123456789012"
IN = "svc-bucket"
PROC = os.environ.get("PROCESSING_TOPIC_ARN") or f"arn:aws:sns:us-east-1:{ACCOUNT}:processing"


class FakeSns:
    def __init__(self):
        self.published = []

    def publish(self, topic_arn, message):
        self.published.append((topic_arn, message))
        return "mid"


class StubFt:
    def probe(self, path):
        return ProbeResult(ok=True, duration_s=5.0,
                           streams=[StreamInfo(index=0, codec_type="audio",
                                               codec_name="aac", channels=2,
                                               channel_layout="stereo")])

    def inspect_bytes(self, path):
        return {"size": 1000, "magic_hex": "00", "container_guess": "mp4"}

    def extract_audio(self, in_path, out_path, opts):
        with open(out_path, "wb") as fh:
            fh.write(b"A")
        return ToolResult(tool="extract_audio", ok=True, output_path=out_path)

    def decode_verify(self, path):
        return DecodeVerifyResult(ok=True, error_count=0)


def _deps(tmp_path):
    return IntakeDeps(
        settings=Settings(processing_topic_arn=PROC, work_dir=str(tmp_path / "work")),
        s3=S3Client("us-east-1"), sns=FakeSns(), ft=StubFt(),
    )


@mock_aws
def test_handle_request_ok(tmp_path):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=IN)
    s3.put_object(Bucket=IN, Key="c.mp4", Body=b"data")
    deps = _deps(tmp_path)
    result = handle_request({"s3_path": f"s3://{IN}/c.mp4"}, deps)
    assert result["status"] == "ok"


def test_handle_request_missing_s3_path(tmp_path):
    with pytest.raises(ValueError):
        handle_request({"repeat": 2}, _deps(tmp_path))


def test_handle_request_bad_repeat(tmp_path):
    with pytest.raises(ValueError):
        handle_request({"s3_path": "s3://b/k", "repeat": "lots"}, _deps(tmp_path))


def test_handle_request_negative_repeat(tmp_path):
    with pytest.raises(ValueError):
        handle_request({"s3_path": "s3://b/k", "repeat": -1}, _deps(tmp_path))
