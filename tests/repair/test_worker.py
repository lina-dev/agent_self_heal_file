import os

import boto3
import pytest
from moto import mock_aws

from audio_repair.core.config import Settings
from audio_repair.core.models import DecodeVerifyResult, ProbeResult, StreamInfo, ToolResult
from audio_repair.core.s3 import S3Client
from audio_repair.llm.client import LLMResponse, ToolCall
from audio_repair.repair.worker import RepairDeps, handle_sqs_record, repair_file

ACCOUNT = os.environ.get("AWS_ACCOUNT_ID") or "123456789012"
IN_BUCKET = "in-bucket"
OUT_BUCKET = os.environ.get("S3_OUTPUT_BUCKET") or "out-bucket"
PROC = os.environ.get("PROCESSING_TOPIC_ARN") or f"arn:aws:sns:us-east-1:{ACCOUNT}:processing"


def _settings(tmp_path):
    return Settings(
        s3_output_bucket=OUT_BUCKET,
        processing_topic_arn=PROC,
        work_dir=str(tmp_path / "work"),
    )


class FakeSns:
    def __init__(self):
        self.published = []

    def publish(self, topic_arn, message):
        self.published.append((topic_arn, message))
        return "mid-1"


class MockLLM:
    def __init__(self, responses=None):
        self.responses = responses or []
        self.calls = 0

    def complete(self, messages, tools, max_tokens):
        if not self.responses:
            raise AssertionError("LLM should not have been called")
        idx = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[idx]


class StubFt:
    """Controls classification + tool outcomes; writes real bytes for uploads."""

    def __init__(self, *, probe_ok, has_audio, size, container, decode_ok,
                 remux_ok=True, extract_ok=True):
        self._probe_ok = probe_ok
        self._has_audio = has_audio
        self._size = size
        self._container = container
        self._decode_ok = decode_ok
        self._remux_ok = remux_ok
        self._extract_ok = extract_ok

    def probe(self, path):
        streams = [StreamInfo(index=0, codec_type="audio", codec_name="aac",
                              channels=2, channel_layout="stereo")] if self._has_audio else []
        return ProbeResult(
            ok=self._probe_ok, duration_s=1.0, streams=streams,
            stderr="" if self._probe_ok else "Invalid data found when processing input",
        )

    def inspect_bytes(self, path):
        return {"size": self._size, "magic_hex": "00", "container_guess": self._container}

    def decode_verify(self, path):
        return DecodeVerifyResult(ok=self._decode_ok, error_count=0 if self._decode_ok else 9)

    def remux(self, in_path, out_path, opts):
        if self._remux_ok:
            with open(out_path, "wb") as fh:
                fh.write(b"OUTPUT")
            return ToolResult(tool="remux", ok=True, output_path=out_path)
        return ToolResult(tool="remux", ok=False, error="remux failed")

    def extract_audio(self, in_path, out_path, opts):
        if self._extract_ok:
            with open(out_path, "wb") as fh:
                fh.write(b"AUDIO")
            return ToolResult(tool="extract_audio", ok=True, output_path=out_path)
        return ToolResult(tool="extract_audio", ok=False, error="extract failed")


def _seed_input(body=b"data"):
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=IN_BUCKET)
    s3.create_bucket(Bucket=OUT_BUCKET)
    s3.put_object(Bucket=IN_BUCKET, Key="clip.mp4", Body=body)
    return f"s3://{IN_BUCKET}/clip.mp4"


@mock_aws
def test_fail_fast_zero_byte(tmp_path):
    uri = _seed_input(b"")
    sns = FakeSns()
    llm = MockLLM()  # must not be called
    ft = StubFt(probe_ok=False, has_audio=False, size=0, container=None, decode_ok=False)
    report = repair_file(uri, settings=_settings(tmp_path), s3=S3Client("us-east-1"),
                         sns=sns, llm=llm, ft=ft)
    assert report.status == "unrepairable"
    assert report.category == "ZERO_BYTE_OR_NONMEDIA"
    assert llm.calls == 0
    assert len(sns.published) == 1


@mock_aws
def test_fastpath_success_skips_agent(tmp_path):
    uri = _seed_input()
    sns = FakeSns()
    llm = MockLLM()  # must not be called
    ft = StubFt(probe_ok=True, has_audio=True, size=500, container="mp4",
                decode_ok=True, remux_ok=True)
    report = repair_file(uri, settings=_settings(tmp_path), s3=S3Client("us-east-1"),
                         sns=sns, llm=llm, ft=ft)
    assert report.status == "repaired"
    assert report.strategy == "stream_copy_remux"
    assert report.output_s3 == f"s3://{OUT_BUCKET}/clip.repaired.mka"
    assert llm.calls == 0
    # output + report objects exist
    s3 = boto3.client("s3", region_name="us-east-1")
    keys = {o["Key"] for o in s3.list_objects_v2(Bucket=OUT_BUCKET).get("Contents", [])}
    assert "clip.repaired.mka" in keys
    assert "clip.report.json" in keys


@mock_aws
def test_agent_repairs_after_fastpath_fails(tmp_path):
    uri = _seed_input()
    sns = FakeSns()
    llm = MockLLM([
        LLMResponse(tool_calls=[ToolCall(id="c1", name="extract_audio",
                                         arguments={"audio_codec": "flac"})],
                    total_tokens=15),
    ])
    # remux fails (fast-path dead) but extract_audio in the agent succeeds.
    ft = StubFt(probe_ok=True, has_audio=True, size=500, container="mp4",
                decode_ok=True, remux_ok=False, extract_ok=True)
    report = repair_file(uri, settings=_settings(tmp_path), s3=S3Client("us-east-1"),
                         sns=sns, llm=llm, ft=ft)
    assert report.status == "repaired"
    assert report.strategy.startswith("agent:")
    assert llm.calls >= 1
    assert report.output_s3.endswith("clip.repaired.mka")


@mock_aws
def test_unrepairable_when_agent_gives_up(tmp_path):
    uri = _seed_input()
    sns = FakeSns()
    llm = MockLLM([LLMResponse(content="cannot fix", tool_calls=[], total_tokens=5)])
    ft = StubFt(probe_ok=False, has_audio=True, size=500, container="mp4",
                decode_ok=True, remux_ok=False)
    report = repair_file(uri, settings=_settings(tmp_path), s3=S3Client("us-east-1"),
                         sns=sns, llm=llm, ft=ft)
    assert report.status == "unrepairable"
    assert "agent stop_reason" in report.reason
    assert len(sns.published) == 1


@mock_aws
def test_internal_error_never_raises(tmp_path):
    # Input object does not exist -> download raises -> caught -> unrepairable.
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=IN_BUCKET)
    sns = FakeSns()
    report = repair_file(f"s3://{IN_BUCKET}/missing.mp4", settings=_settings(tmp_path),
                         s3=S3Client("us-east-1"), sns=sns, llm=MockLLM(),
                         ft=StubFt(probe_ok=True, has_audio=True, size=1, container="mp4",
                                   decode_ok=True))
    assert report.status == "unrepairable"
    assert "internal error" in report.reason


@mock_aws
def test_handle_sqs_record_parses_envelope(tmp_path):
    import json

    uri = _seed_input(b"")
    sns = FakeSns()
    deps = RepairDeps(settings=_settings(tmp_path), s3=S3Client("us-east-1"),
                      sns=sns, llm=MockLLM(),
                      ft=StubFt(probe_ok=False, has_audio=False, size=0,
                                container=None, decode_ok=False))
    body = json.dumps({"Type": "Notification", "Message": json.dumps({"s3_path": uri})})
    report = handle_sqs_record({"body": body}, deps)
    assert report.status == "unrepairable"


def test_handle_sqs_record_missing_path():
    import json

    deps = RepairDeps(settings=Settings(), s3=None, sns=None, llm=None, ft=None)
    with pytest.raises(ValueError):
        handle_sqs_record({"body": json.dumps({"foo": "bar"})}, deps)
