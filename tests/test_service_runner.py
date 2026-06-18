import pytest

from audio_repair import service
from audio_repair.core.config import Settings

S = Settings(aws_region="us-east-1")


class FakeSqs:
    """Returns each queued batch once, then empty; records deletes."""

    def __init__(self, batches):
        self._batches = list(batches)
        self.deleted = []
        self.received = 0

    def receive_message(self, **kwargs):
        self.received += 1
        if self._batches:
            return {"Messages": self._batches.pop(0)}
        return {"Messages": []}

    def delete_message(self, QueueUrl, ReceiptHandle):
        self.deleted.append(ReceiptHandle)


def _msg(i):
    return {"Body": "{}", "ReceiptHandle": f"rh{i}", "MessageId": f"m{i}"}


def test_processes_and_deletes_on_success():
    seen = []
    sqs = FakeSqs([[_msg(1), _msg(2)]])
    n = service.serve(
        queue_url="http://q", settings=S, sqs=sqs, deps=object(),
        dispatch=lambda rec, deps: seen.append(rec["messageId"]), max_loops=1,
    )
    assert n == 2
    assert sqs.deleted == ["rh1", "rh2"]
    assert seen == ["m1", "m2"]


def test_failed_record_is_not_deleted():
    def boom(rec, deps):
        raise RuntimeError("bad message")

    sqs = FakeSqs([[_msg(1)]])
    n = service.serve(queue_url="http://q", settings=S, sqs=sqs, deps=object(),
                      dispatch=boom, max_loops=1)
    assert n == 0
    assert sqs.deleted == []  # left for visibility-timeout redrive / DLQ


def test_stopper_breaks_loop():
    stopper = service._Stopper()
    stopper.stop = True
    sqs = FakeSqs([[_msg(1)]])
    n = service.serve(queue_url="http://q", settings=S, sqs=sqs, deps=object(),
                      dispatch=lambda rec, deps: None, stopper=stopper)
    assert n == 0
    assert sqs.received == 0  # never polled


def test_requires_queue_url(monkeypatch):
    monkeypatch.delenv("SQS_QUEUE_URL", raising=False)
    with pytest.raises(ValueError, match="queue_url is required"):
        service.serve(queue_url=None, settings=S, sqs=object(), deps=object(),
                      dispatch=lambda rec, deps: None, max_loops=1)


def test_unknown_mode_rejected():
    with pytest.raises(ValueError, match="unknown mode"):
        service.serve(mode="bogus", queue_url="http://q", settings=S, sqs=object(),
                      max_loops=1)
