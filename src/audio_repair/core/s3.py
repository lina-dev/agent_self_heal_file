"""S3 helpers with idempotent output keys (spec §9)."""

from __future__ import annotations

from pathlib import PurePosixPath

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class S3Error(RuntimeError):
    pass


def parse_s3_uri(uri: str) -> tuple[str, str]:
    if not isinstance(uri, str) or not uri.startswith("s3://"):
        raise ValueError(f"not an s3 uri: {uri!r}")
    rest = uri[len("s3://"):]
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"s3 uri missing bucket or key: {uri!r}")
    return bucket, key


class S3Client:
    def __init__(self, region: str):
        self._c = boto3.client("s3", region_name=region)

    def download(self, uri: str, dest_path: str) -> None:
        bucket, key = parse_s3_uri(uri)
        try:
            self._c.download_file(bucket, key, dest_path)
        except (ClientError, BotoCoreError) as e:
            raise S3Error(f"download failed for {uri}: {e}") from e

    def upload(self, src_path: str, bucket: str, key: str) -> str:
        try:
            self._c.upload_file(src_path, bucket, key)
        except (ClientError, BotoCoreError) as e:
            raise S3Error(f"upload failed for s3://{bucket}/{key}: {e}") from e
        return f"s3://{bucket}/{key}"

    @staticmethod
    def repaired_key(src_key: str, ext: str) -> str:
        p = PurePosixPath(src_key)
        ext = ext.lstrip(".")
        return str(p.with_name(f"{p.stem}.repaired.{ext}"))

    @staticmethod
    def report_key(src_key: str) -> str:
        p = PurePosixPath(src_key)
        return str(p.with_name(f"{p.stem}.report.json"))
