import json
import logging

from audio_repair.core.telemetry import bind, get_logger, timed


def _capture(logger: logging.Logger, caplog):
    # Read the formatted JSON by attaching our formatter output via the handler.
    handler = next(h for h in logger.handlers if getattr(h, "_audio_repair_json", False))
    return handler


def test_log_is_json_with_correlation_id(capsys):
    logger = get_logger("test.telemetry.json")
    adapter = bind(logger, correlation_id="abc-123", category="DAMAGED_INDEX")
    adapter.info("repair started")
    err = capsys.readouterr().err.strip().splitlines()[-1]
    obj = json.loads(err)
    assert obj["msg"] == "repair started"
    assert obj["correlation_id"] == "abc-123"
    assert obj["category"] == "DAMAGED_INDEX"
    assert obj["level"] == "INFO"
    assert "ts" in obj


def test_get_logger_idempotent_handlers():
    a = get_logger("test.telemetry.idem")
    b = get_logger("test.telemetry.idem")
    assert a is b
    json_handlers = [h for h in a.handlers if getattr(h, "_audio_repair_json", False)]
    assert len(json_handlers) == 1


def test_timed_measures_elapsed():
    with timed("work") as t:
        sum(range(1000))
    assert t["label"] == "work"
    assert t["elapsed_ms"] >= 0


def test_non_serializable_extra_is_stringified(capsys):
    logger = get_logger("test.telemetry.safe")
    adapter = bind(logger, obj=object())
    adapter.info("x")
    err = capsys.readouterr().err.strip().splitlines()[-1]
    obj = json.loads(err)
    assert isinstance(obj["obj"], str)
