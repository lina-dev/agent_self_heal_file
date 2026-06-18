"""Per-job temp sandbox + secure subprocess runner (spec §7 safety guards).

All file work happens inside a unique temp directory; `resolve()` refuses any
path that escapes the sandbox. Subprocesses are always argv lists (never
shell strings), run in their own session so we can kill the whole process
group on timeout, and have output captured as text.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CompletedProc:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


class JobSandbox:
    """Context manager owning a unique temp dir, cleaned up on exit."""

    def __init__(self, base: str | os.PathLike = "/tmp/audio_repair"):
        self._base = Path(base)
        self.path: Path | None = None

    def __enter__(self) -> "JobSandbox":
        self._base.mkdir(parents=True, exist_ok=True)
        self.path = Path(
            tempfile.mkdtemp(prefix=f"job-{uuid.uuid4().hex[:8]}-", dir=self._base)
        )
        return self

    def __exit__(self, *exc) -> None:
        if self.path and self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)

    def resolve(self, name: str) -> Path:
        """Resolve `name` to an absolute path strictly inside the sandbox.

        Raises ValueError on any traversal or absolute path escape.
        """
        if self.path is None:
            raise RuntimeError("sandbox not entered")
        root = self.path.resolve()
        candidate = (root / name).resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"path escapes sandbox: {name!r}")
        return candidate


def run_argv(argv: list[str], timeout_s: int) -> CompletedProc:
    """Run an argv list with a hard timeout. Never uses a shell.

    On timeout the entire process group is SIGKILLed. A missing executable is
    reported as returncode 127 rather than raising, so callers can treat it
    uniformly.
    """
    if (
        not isinstance(argv, (list, tuple))
        or not argv
        or not all(isinstance(a, str) for a in argv)
    ):
        raise ValueError("argv must be a non-empty list of strings")
    if timeout_s <= 0:
        raise ValueError("timeout_s must be positive")
    try:
        proc = subprocess.Popen(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except FileNotFoundError as e:
        return CompletedProc(returncode=127, stdout="", stderr=str(e), timed_out=False)
    try:
        out, err = proc.communicate(timeout=timeout_s)
        return CompletedProc(
            returncode=proc.returncode, stdout=out, stderr=err, timed_out=False
        )
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = "", ""
        return CompletedProc(
            returncode=-9, stdout=out or "", stderr=err or "", timed_out=True
        )
