import shutil

import boto3
import pytest
from moto import mock_aws

import audio_repair.cli as cli
from audio_repair.llm.client import LLMResponse

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")

BUCKET = "cli-eval-bucket"


class StubLLM:
    def __init__(self, settings=None):
        pass

    def complete(self, messages, tools, max_tokens):
        return LLMResponse(content="no fix", tool_calls=[], total_tokens=1)


def test_no_command_prints_help_nonzero(capsys):
    rc = cli.main([])
    assert rc != 0
    out = capsys.readouterr().out
    assert "audio-repair" in out


def test_unknown_args_exit():
    with pytest.raises(SystemExit):
        cli.main(["intake"])  # missing required --s3


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
@mock_aws
def test_eval_command_returns_zero(tmp_path, monkeypatch):
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
    monkeypatch.setenv("S3_OUTPUT_BUCKET", BUCKET)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("WORK_DIR", str(tmp_path / "work"))
    monkeypatch.setattr(cli, "LLMClient", StubLLM)

    rc = cli.main(["eval", "--seed-dir", str(tmp_path / "seeds"),
                   "--out-dir", str(tmp_path / "out")])
    assert rc == 0
    assert (tmp_path / "out" / "scorecard.json").exists()
