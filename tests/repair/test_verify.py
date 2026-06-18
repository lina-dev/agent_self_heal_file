import shutil
import subprocess

import pytest

from audio_repair.core.config import get_settings
from audio_repair.core.ffmpeg_tools import FfmpegTools
from audio_repair.core.models import DecodeVerifyResult, ProbeResult, StreamInfo
from audio_repair.repair.verify import verify_repaired

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
S = get_settings({})


class _StubFt:
    def __init__(self, probe_ok, has_audio, decode_ok):
        self._probe_ok = probe_ok
        self._has_audio = has_audio
        self._decode_ok = decode_ok

    def probe(self, path):
        streams = [StreamInfo(index=0, codec_type="audio")] if self._has_audio else []
        return ProbeResult(ok=self._probe_ok, duration_s=1.0, streams=streams)

    def decode_verify(self, path):
        return DecodeVerifyResult(ok=self._decode_ok, error_count=0 if self._decode_ok else 4)


def test_verify_ok_unit():
    r = verify_repaired(_StubFt(True, True, True), "/x")
    assert r.ok is True and r.decode_clean and r.audio_present


def test_verify_no_audio():
    r = verify_repaired(_StubFt(True, False, True), "/x")
    assert r.ok is False
    assert r.reason == "no audio stream in output"


def test_verify_decode_errors():
    r = verify_repaired(_StubFt(True, True, False), "/x")
    assert r.ok is False
    assert "decode" in r.reason


def test_verify_unprobeable():
    r = verify_repaired(_StubFt(False, False, False), "/x")
    assert r.ok is False
    assert r.reason == "output not probeable"


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_verify_real_clean_file(tmp_path):
    wav = tmp_path / "ok.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-ac", "2", str(wav)],
        check=True, capture_output=True,
    )
    r = verify_repaired(FfmpegTools(S), str(wav))
    assert r.ok is True


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_verify_real_garbage(tmp_path):
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"\x00\x01\x02not a real media file" * 10)
    r = verify_repaired(FfmpegTools(S), str(junk))
    assert r.ok is False
