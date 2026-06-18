"""Deterministic corruptors for the eval harness (spec §8).

Each corruptor turns a known-good seed into a file exhibiting one taxonomy
failure mode, and returns a `CorruptionManifest` recording what was done and
whether a correct system should recover it. Corruption is done with explicit,
deterministic byte operations (or ffmpeg for the seed) so eval runs are
reproducible. We only model the subset we can synthesize reliably across ffmpeg
versions; expanding the set is purely additive.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from ..core.taxonomy import Category


class CorruptionManifest(BaseModel):
    category: str
    original_params: dict = {}
    expected_recoverable: bool
    path: str


class SeedError(RuntimeError):
    pass


def make_seed(path: str | Path) -> Path:
    """Synthesize a known-good media seed (requires ffmpeg).

    `.wav` -> 1s stereo sine; `.mp4` -> 1s AAC audio (moov at end, no faststart).
    """
    path = Path(path)
    if not shutil.which("ffmpeg"):
        raise SeedError("ffmpeg required to synthesize seeds")
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".mp4":
        argv = ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-ac", "2", "-c:a", "aac", str(path)]
    else:
        argv = ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-ac", "2", "-c:a", "pcm_s16le", str(path)]
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode != 0 or not path.exists() or path.stat().st_size == 0:
        raise SeedError(f"seed synthesis failed: {r.stderr[-300:]}")
    return path


@dataclass
class Corruptor:
    """A callable (src, dst) -> CorruptionManifest with the seed it needs."""

    category: Category
    seed_suffix: str
    expected_recoverable: bool
    fn: Callable[[Path, Path], dict] = field(repr=False)
    out_suffix: str | None = None  # corrupted-file extension (defaults to seed_suffix)

    @property
    def output_suffix(self) -> str:
        return self.out_suffix or self.seed_suffix

    def __call__(self, src: Path, dst: Path) -> CorruptionManifest:
        params = self.fn(src, dst)
        return CorruptionManifest(
            category=self.category.name,
            original_params=params,
            expected_recoverable=self.expected_recoverable,
            path=str(dst),
        )


# --- byte operations -------------------------------------------------------


def _zero_byte(src: Path, dst: Path) -> dict:
    orig = src.stat().st_size
    dst.write_bytes(b"")
    return {"original_size": orig, "corrupted_size": 0}


def _wrong_container(src: Path, dst: Path) -> dict:
    # Valid WAV bytes written under a .mp4 name: container disagrees with extension.
    data = src.read_bytes()
    dst.write_bytes(data)
    return {"original_size": len(data), "real_container": "wav", "claimed_ext": dst.suffix}


def _truncate_moov(src: Path, dst: Path) -> dict:
    data = src.read_bytes()
    keep = len(data) // 2
    dst.write_bytes(data[:keep])
    return {"original_size": len(data), "corrupted_size": keep}


def _damage_header(src: Path, dst: Path) -> dict:
    data = bytearray(src.read_bytes())
    # Corrupt bytes in the WAV header region (after RIFF magic) without resizing.
    for i in range(16, min(40, len(data))):
        data[i] ^= 0xFF
    dst.write_bytes(bytes(data))
    return {"original_size": len(data), "corrupted_range": [16, 40]}


def _partial_midstream(src: Path, dst: Path) -> dict:
    data = bytearray(src.read_bytes())
    mid = len(data) // 2
    span = min(512, len(data) - mid)
    for i in range(mid, mid + span):
        data[i] = 0x00
    dst.write_bytes(bytes(data))
    return {"original_size": len(data), "zeroed_range": [mid, mid + span]}


CORRUPTORS: dict[Category, Corruptor] = {
    Category.ZERO_BYTE_OR_NONMEDIA: Corruptor(
        Category.ZERO_BYTE_OR_NONMEDIA, ".wav", False, _zero_byte),
    Category.WRONG_CONTAINER_VS_EXTENSION: Corruptor(
        Category.WRONG_CONTAINER_VS_EXTENSION, ".wav", True, _wrong_container,
        out_suffix=".mp4"),
    Category.TRUNCATED_MISSING_MOOV: Corruptor(
        Category.TRUNCATED_MISSING_MOOV, ".mp4", False, _truncate_moov),
    Category.DAMAGED_CONTAINER_HEADER: Corruptor(
        Category.DAMAGED_CONTAINER_HEADER, ".wav", True, _damage_header),
    Category.PARTIAL_MIDSTREAM_CORRUPTION: Corruptor(
        Category.PARTIAL_MIDSTREAM_CORRUPTION, ".wav", True, _partial_midstream),
}
