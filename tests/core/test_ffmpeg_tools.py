import shutil
import subprocess

import pytest

from audio_repair.core.config import get_settings
from audio_repair.core.ffmpeg_tools import (
    ExtractOpts,
    FfmpegTools,
    ForceFormatOpts,
    ReencodeOpts,
    _sniff_container,
)

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
needs_ffmpeg = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg not installed")


def tools():
    return FfmpegTools(get_settings({}))


@pytest.fixture
def wav(tmp_path):
    p = tmp_path / "tone.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-ac", "2", "-ar", "44100", str(p),
        ],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return p


# --- option validation (no ffmpeg needed) ----------------------------------


def test_opts_reject_unknown_field():
    with pytest.raises(ValueError):
        ExtractOpts(stream_index=0, audio_codec="copy", evil="rm -rf")  # type: ignore[call-arg]


def test_opts_reject_bad_codec():
    with pytest.raises(ValueError):
        ExtractOpts(stream_index=0, audio_codec="; rm -rf /")  # type: ignore[arg-type]


def test_opts_reject_negative_stream_index():
    with pytest.raises(ValueError):
        ExtractOpts(stream_index=-1)


def test_reencode_rejects_bad_rate():
    with pytest.raises(ValueError):
        ReencodeOpts(sample_rate=12345).validated_rate()


def test_forceformat_rejects_bad_format():
    with pytest.raises(ValueError):
        ForceFormatOpts(input_format="evil")  # type: ignore[arg-type]


def test_sniff_container():
    assert _sniff_container(b"RIFF\x00\x00\x00\x00WAVE") == "wav"
    assert _sniff_container(b"\x00\x00\x00\x18ftypmp42") == "mp4"
    assert _sniff_container(b"not media at all") is None
    assert _sniff_container(b"") is None


# --- real ffmpeg -----------------------------------------------------------


@needs_ffmpeg
def test_probe_good_file(wav):
    r = tools().probe(str(wav))
    assert r.ok and r.has_audio()
    assert r.audio_streams[0].channels == 2
    assert r.audio_streams[0].sample_rate == 44100


@needs_ffmpeg
def test_decode_verify_clean(wav):
    r = tools().decode_verify(str(wav))
    assert r.ok and r.error_count == 0


@needs_ffmpeg
def test_extract_audio_copy(wav, tmp_path):
    out = tmp_path / "out.wav"
    res = tools().extract_audio(str(wav), str(out), ExtractOpts(stream_index=0, audio_codec="copy"))
    assert res.ok and out.exists()


@needs_ffmpeg
def test_inspect_bytes_real(wav):
    info = tools().inspect_bytes(str(wav))
    assert info["size"] > 0
    assert info["container_guess"] == "wav"


@needs_ffmpeg
def test_probe_nonmedia(tmp_path):
    p = tmp_path / "junk.mp4"
    p.write_bytes(b"this is not media")
    r = tools().probe(str(p))
    assert r.ok is False
    assert r.error is not None


@needs_ffmpeg
def test_decode_verify_detects_corruption(tmp_path):
    p = tmp_path / "bad.wav"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunkjunkjunk")
    r = tools().decode_verify(str(p))
    assert r.ok is False
