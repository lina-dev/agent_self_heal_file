import json

import boto3
import pytest
from moto import mock_aws

from audio_repair.core.messaging import SnsPublisher, parse_sns_wrapped_sqs_body


@mock_aws
def test_publish_returns_message_id():
    sns = boto3.client("sns", region_name="us-east-1")
    arn = sns.create_topic(Name="processing")["TopicArn"]
    pub = SnsPublisher("us-east-1")
    mid = pub.publish(arn, {"status": "ok", "audio_s3_path": "s3://b/k.wav"})
    assert isinstance(mid, str) and mid


def test_publish_rejects_empty_arn():
    pub = SnsPublisher.__new__(SnsPublisher)  # no AWS needed for the guard
    with pytest.raises(ValueError):
        SnsPublisher.publish(pub, "", {"x": 1})


def test_parse_raw_payload():
    body = json.dumps({"s3_path": "s3://b/k.mp4"})
    assert parse_sns_wrapped_sqs_body(body) == {"s3_path": "s3://b/k.mp4"}


def test_parse_sns_envelope():
    inner = {"s3_path": "s3://b/k.mp4", "category": "DAMAGED_INDEX"}
    body = json.dumps({"Type": "Notification", "Message": json.dumps(inner)})
    assert parse_sns_wrapped_sqs_body(body) == inner
