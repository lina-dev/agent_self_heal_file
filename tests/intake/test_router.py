import os

import boto3
import pytest
from moto import mock_aws

from audio_repair.core.config import Settings
from audio_repair.core.models import DecodeVerifyResult, ProbeResult, StreamInfo, ToolResult
from audio_repair.core.s3 import S3Client
from audio_repair.intake.router import IntakeDeps, route

# Infra identifiers come from the environment (GitHub repo variables in CI);
# fall back to AWS's documentation dummy account for offline/local runs.
ACCOUNT = os.environ.get("AWS_ACCOUNT_ID") or "123456789012"
IN = "intake-bucket"
PROC = os.environ.get("PROCESSING_TOPIC_ARN") or f"arn:aws:sns:us-east-1:{ACCOUNT}:processing"
REPAIR = os.environ.get("REPAIR_TOPIC_ARN") or f"arn:aws:sns:us-east-1:{ACCOUNT}:repair"


def _settings(tmp_path, repeat=1):
    return Settings(processing_topic_arn=PROC, repair_topic_arn=REPAIR,
                    intake_repeat=repeat, work_dir=str(tmp_path / "work"))


class FakeSns:
    def __init__(self):
        self.published = []

    def publish(self, topic_arn, message):
        self.published.append((topic_arn, message))
        return "mid"


class StubFt:
    def __init__(self, *, duration=5.0, probe_ok=True, has_audio=True,
                 extract_ok=True, decode_ok=True, container="mp4"):
        self.duration = duration
        self.probe_ok = probe_ok
        self.has_audio = has_audio
        self.extract_ok = extract_ok
        self.decode_ok = decode_ok
        self.container = container
        self.extract_calls = 0

    def probe(self, path):
        streams = [StreamInfo(index=0, codec_type="audio", codec_name="aac",
                              channels=2, channel_layout="stereo")] if self.has_audio else []
        return ProbeResult(ok=self.probe_ok, duration_s=self.duration, streams=streams,
                           stderr="" if self.probe_ok else "Invalid data found when processing input")

    def inspect_bytes(self, path):
        size = 0 if self.container is None else 1000
        return {"size": size, "magic_hex": "00", "container_guess": self.container}

    def extract_audio(self, in_path, out_path, opts):
        self.extract_calls += 1
        if self.extract_ok:
            with open(out_path, "wb") as fh:
                fh.write(b"A")
            return ToolResult(tool="extract_audio", ok=True, output_path=out_path)
        return ToolResult(tool="extract_audio", ok=False, error="fail")

    def decode_verify(self, path):
        return DecodeVerifyResult(ok=self.decode_ok, error_count=0 if self.decode_ok else 3)


def _seed():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=IN)
    s3.put_object(Bucket=IN, Key="clip.mp4", Body=b"data")
    return f"s3://{IN}/clip.mp4"


@mock_aws
def test_readable_publishes_ok(tmp_path):
    uri = _seed()
    sns = FakeSns()
    ft = StubFt(extract_ok=True, decode_ok=True)
    deps = IntakeDeps(settings=_settings(tmp_path), s3=S3Client("us-east-1"), sns=sns, ft=ft)
    out = route(uri, deps.settings, deps)
    assert out.status == "ok"
    assert out.attempts == 1
    assert sns.published == [(PROC, {"status": "ok", "s3_path": uri, "category": out.category})]


@mock_aws
def test_length_gate_publishes_rejected(tmp_path):
    uri = _seed()
    sns = FakeSns()
    ft = StubFt(duration=11000.0)
    deps = IntakeDeps(settings=_settings(tmp_path), s3=S3Client("us-east-1"), sns=sns, ft=ft)
    out = route(uri, deps.settings, deps)
    assert out.status == "rejected"
    assert out.category == "DURATION_GE_3H"
    assert sns.published[0][0] == PROC
    assert sns.published[0][1]["status"] == "rejected"
    assert len(sns.published) == 1


@mock_aws
def test_unreadable_retries_then_repair(tmp_path):
    uri = _seed()
    sns = FakeSns()
    # probe fails + extract fails => unreadable, category not a reject policy.
    ft = StubFt(duration=None, probe_ok=False, extract_ok=False, container="mp4")
    deps = IntakeDeps(settings=_settings(tmp_path, repeat=1), s3=S3Client("us-east-1"),
                      sns=sns, ft=ft)
    out = route(uri, deps.settings, deps)
    assert out.status == "repair_needed"
    assert out.attempts == 2  # repeat + 1
    assert ft.extract_calls == 2
    assert len(sns.published) == 1
    assert sns.published[0][0] == REPAIR


@mock_aws
def test_zero_byte_rejected_no_retry(tmp_path):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=IN)
    s3.put_object(Bucket=IN, Key="empty.mp4", Body=b"")
    uri = f"s3://{IN}/empty.mp4"
    sns = FakeSns()
    ft = StubFt(duration=None, probe_ok=False, container=None)  # size 0 -> ZERO_BYTE
    deps = IntakeDeps(settings=_settings(tmp_path, repeat=3), s3=S3Client("us-east-1"),
                      sns=sns, ft=ft)
    out = route(uri, deps.settings, deps)
    assert out.status == "rejected"
    assert out.category == "ZERO_BYTE_OR_NONMEDIA"
    assert out.attempts == 1  # rejected category breaks the retry loop immediately
