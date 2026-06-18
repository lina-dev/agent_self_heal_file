"""SNS publishing for hand-off between services (spec §4)."""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class MessagingError(RuntimeError):
    pass


class SnsPublisher:
    def __init__(self, region: str):
        self._c = boto3.client("sns", region_name=region)

    def publish(self, topic_arn: str, message: dict) -> str:
        if not topic_arn:
            raise ValueError("topic_arn must be non-empty")
        try:
            resp = self._c.publish(TopicArn=topic_arn, Message=json.dumps(message))
        except (ClientError, BotoCoreError) as e:
            raise MessagingError(f"publish failed to {topic_arn}: {e}") from e
        return resp["MessageId"]


def parse_sns_wrapped_sqs_body(body: str) -> dict:
    """SQS messages fanned out from SNS wrap the payload in an envelope.

    Accepts either the raw payload JSON or the SNS envelope ({"Message": "..."}).
    """
    outer = json.loads(body)
    if isinstance(outer, dict) and "Message" in outer and isinstance(outer["Message"], str):
        return json.loads(outer["Message"])
    return outer
