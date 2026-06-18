import shutil

import pytest

from audio_repair.core.taxonomy import Category
from audio_repair.eval.corruptors import CORRUPTORS, make_seed

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


def _apply(corruptor, tmp_path, name):
    seed = make_seed(tmp_path / f"seed{corruptor.seed_suffix}")
    dst = tmp_path / f"{name}{corruptor.output_suffix}"
    manifest = corruptor(seed, dst)
    return seed, dst, manifest


def test_zero_byte(tmp_path):
    c = CORRUPTORS[Category.ZERO_BYTE_OR_NONMEDIA]
    seed, dst, m = _apply(c, tmp_path, "zero")
    assert dst.stat().st_size == 0
    assert m.expected_recoverable is False
    assert m.category == "ZERO_BYTE_OR_NONMEDIA"
    assert m.original_params["original_size"] > 0


def test_wrong_container_extension(tmp_path):
    c = CORRUPTORS[Category.WRONG_CONTAINER_VS_EXTENSION]
    seed, dst, m = _apply(c, tmp_path, "wrong")
    assert dst.suffix == ".mp4"
    # bytes are still a real WAV (RIFF magic), so the extension lies
    assert dst.read_bytes()[:4] == b"RIFF"
    assert m.expected_recoverable is True


def test_truncated_smaller_than_original(tmp_path):
    c = CORRUPTORS[Category.TRUNCATED_MISSING_MOOV]
    seed, dst, m = _apply(c, tmp_path, "trunc")
    assert dst.stat().st_size < seed.stat().st_size
    assert m.expected_recoverable is False


def test_damaged_header_same_size_diff_bytes(tmp_path):
    c = CORRUPTORS[Category.DAMAGED_CONTAINER_HEADER]
    seed, dst, m = _apply(c, tmp_path, "dmg")
    assert dst.stat().st_size == seed.stat().st_size
    assert dst.read_bytes() != seed.read_bytes()


def test_partial_midstream_same_size_diff_bytes(tmp_path):
    c = CORRUPTORS[Category.PARTIAL_MIDSTREAM_CORRUPTION]
    seed, dst, m = _apply(c, tmp_path, "mid")
    assert dst.stat().st_size == seed.stat().st_size
    assert dst.read_bytes() != seed.read_bytes()


def test_all_corruptors_produce_manifest(tmp_path):
    for i, (cat, corr) in enumerate(CORRUPTORS.items()):
        seed, dst, m = _apply(corr, tmp_path, f"c{i}")
        assert m.path == str(dst)
        assert m.category == cat.name
