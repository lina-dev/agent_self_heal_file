"""Intake API entrypoint (spec §5).

Thin request-validation layer over `route`. Accepts a request dict with an
`s3_path` and an optional `repeat` override (defaults to the configured value).
"""

from __future__ import annotations

from .router import IntakeDeps, route


def handle_request(request: dict, deps: IntakeDeps) -> dict:
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    s3_path = request.get("s3_path")
    if not s3_path or not isinstance(s3_path, str):
        raise ValueError("request missing required 's3_path'")

    repeat = request.get("repeat")
    if repeat is not None:
        try:
            repeat = int(repeat)
        except (TypeError, ValueError) as e:
            raise ValueError(f"invalid 'repeat': {request.get('repeat')!r}") from e
        if repeat < 0:
            raise ValueError("'repeat' must be >= 0")

    outcome = route(s3_path, deps.settings, deps, repeat=repeat)
    return outcome.model_dump()
