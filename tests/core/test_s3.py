import boto3
import pytest
from moto import mock_aws

from audio_repair.core.s3 import S3Client, S3Error, parse_s3_uri


def test_parse_s3_uri_ok():
    assert parse_s3_uri("s3://b/dir/k.mp4") == ("b", "dir/k.mp4")


def test_parse_s3_uri_bad_scheme():
    with pytest.raises(ValueError):
        parse_s3_uri("https://x/y")


def test_parse_s3_uri_missing_key():
    with pytest.raises(ValueError):
        parse_s3_uri("s3://bucketonly")


@mock_aws
def test_s3_round_trip(tmp_path):
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="test-bucket")
    c = S3Client("us-east-1")
    src = tmp_path / "in.bin"
    src.write_bytes(b"hello")
    uri = c.upload(str(src), "test-bucket", "k.bin")
    assert uri == "s3://test-bucket/k.bin"
    dest = tmp_path / "out.bin"
    c.download("s3://test-bucket/k.bin", str(dest))
    assert dest.read_bytes() == b"hello"


@mock_aws
def test_download_missing_raises(tmp_path):
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="test-bucket")
    c = S3Client("us-east-1")
    with pytest.raises(S3Error):
        c.download("s3://test-bucket/nope.bin", str(tmp_path / "x"))


def test_repaired_key_idempotent():
    assert S3Client.repaired_key("a/b/k.mp4", "mp4") == "a/b/k.repaired.mp4"
    # idempotent shape: applying naming twice keeps the .repaired infix once
    assert S3Client.repaired_key("k.mov", ".wav") == "k.repaired.wav"


def test_report_key():
    assert S3Client.report_key("a/b/k.mp4") == "a/b/k.report.json"
