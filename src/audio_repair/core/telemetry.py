"""Structured JSON logging + lightweight timing (spec §9).

Every log line is a single JSON object so logs are machine-parseable in any
aggregator. No external deps — stdlib `logging` with a JSON `Formatter`. A
`bind()` helper attaches a correlation id / category to every record, and
`timed()` measures a block in milliseconds.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

# Attributes present on every stdlib LogRecord; anything else is an "extra"
# we want to surface in the JSON payload.
_STD_ATTRS = set(
    vars(logging.makeLogRecord({})).keys()
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Render a LogRecord as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Merge any structured extras (e.g. correlation_id, category).
        for key, value in record.__dict__.items():
            if key not in _STD_ATTRS and not key.startswith("_"):
                payload[key] = _safe(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


def _safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def get_logger(name: str) -> logging.Logger:
    """Return a logger that emits single-line JSON to stderr exactly once."""
    logger = logging.getLogger(name)
    if not any(getattr(h, "_audio_repair_json", False) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler._audio_repair_json = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def bind(logger: logging.Logger, **context: Any) -> logging.LoggerAdapter:
    """Bind structured context onto every record emitted via the adapter."""
    return _ContextAdapter(logger, context)


class _ContextAdapter(logging.LoggerAdapter):
    def process(self, msg: Any, kwargs: dict) -> tuple[Any, dict]:
        extra = dict(self.extra or {})
        extra.update(kwargs.get("extra") or {})
        kwargs["extra"] = extra
        return msg, kwargs


@contextmanager
def timed(label: str) -> Iterator[dict]:
    """Time a block; yields a dict that gets an `elapsed_ms` key on exit."""
    holder: dict[str, Any] = {"label": label, "elapsed_ms": 0}
    start = time.monotonic()
    try:
        yield holder
    finally:
        holder["elapsed_ms"] = int((time.monotonic() - start) * 1000)
