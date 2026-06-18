import shutil
import subprocess

import pytest

from audio_repair.core.config import get_settings
from audio_repair.core.ffmpeg_tools import FfmpegTools
from audio_repair.core.models import DecodeVerifyResult, ProbeResult, StreamInfo, ToolResult
from audio_repair.core.sandbox import JobSandbox
from audio_repair.repair.fastpath import try_fastpath

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
S = get_settings({})


class _StubFt:
    """In-memory stand-in for FfmpegTools to unit-test the control flow."""

    def __init__(self, remux_ok=True, decode_ok=True, has_audio=True):
        self._remux_ok = remux_ok
        self._decode_ok = decode_ok
        self._has_audio = has_audio

    def remux(self, in_path, out_path, opts):
        return ToolResult(
            tool="remux",
            ok=self._remux_ok,
            output_path=out_path if self._remux_ok else None,
        )

    def decode_verify(self, path):
        return DecodeVerifyResult(ok=self._decode_ok, error_count=0 if self._decode_ok else 3)

    def probe(self, path):
        streams = [StreamInfo(index=0, codec_type="audio", codec_name="aac")] if self._has_audio else []
        return ProbeResult(ok=True, duration_s=1.0, streams=streams)


def test_fastpath_success_unit(tmp_path):
    with JobSandbox(base=tmp_path) as sb:
        r = try_fastpath(_StubFt(), sb, "/in.mp4")
    assert r.ok is True
    assert r.output_path is not None
    assert r.strategy == "stream_copy_remux"


def test_fastpath_remux_fails(tmp_path):
    with JobSandbox(base=tmp_path) as sb:
        r = try_fastpath(_StubFt(remux_ok=False), sb, "/in.mp4")
    assert r.ok is False


def test_fastpath_decode_verify_fails(tmp_path):
    with JobSandbox(base=tmp_path) as sb:
        r = try_fastpath(_StubFt(decode_ok=False), sb, "/in.mp4")
    assert r.ok is False


def test_fastpath_no_audio_in_output(tmp_path):
    with JobSandbox(base=tmp_path) as sb:
        r = try_fastpath(_StubFt(has_audio=False), sb, "/in.mp4")
    assert r.ok is False


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_fastpath_real_wrong_extension(tmp_path):
    # A real WAV file with a wrong .mp4 extension is remuxable by stream-copy.
    wav = tmp_path / "a.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-ac", "2", str(wav)],
        check=True, capture_output=True,
    )
    mislabeled = tmp_path / "a.mp4"
    mislabeled.write_bytes(wav.read_bytes())

    ft = FfmpegTools(S)
    with JobSandbox(base=tmp_path) as sb:
        r = try_fastpath(ft, sb, str(mislabeled))
    assert r.ok is True
    assert r.output_path is not None
