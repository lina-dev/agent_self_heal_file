from audio_repair.core.classify import classify
from audio_repair.core.config import get_settings
from audio_repair.core.models import ProbeResult, StreamInfo
from audio_repair.core.taxonomy import Category

S = get_settings({})


def test_zero_byte():
    p = ProbeResult(ok=False, stderr="Invalid data found when processing input")
    sniff = {"size": 0, "container_guess": None}
    assert classify(p, sniff, S) is Category.ZERO_BYTE_OR_NONMEDIA


def test_nonmedia_unsniffable():
    p = ProbeResult(ok=False, stderr="Invalid data found when processing input")
    sniff = {"size": 50, "container_guess": None}
    assert classify(p, sniff, S) is Category.ZERO_BYTE_OR_NONMEDIA


def test_truncated_moov():
    p = ProbeResult(ok=False, stderr="moov atom not found")
    sniff = {"size": 1000, "container_guess": "mp4"}
    assert classify(p, sniff, S) is Category.TRUNCATED_MISSING_MOOV


def test_audio_in_video():
    v = StreamInfo(index=0, codec_type="video", codec_name="h264")
    p = ProbeResult(ok=True, duration_s=5.0, streams=[v], stderr="")
    sniff = {"size": 5000, "container_guess": "mp4"}
    assert classify(p, sniff, S) is Category.AUDIO_IN_VIDEO


def test_no_audio_no_video():
    p = ProbeResult(ok=True, duration_s=5.0, streams=[StreamInfo(index=0, codec_type="data")], stderr="")
    sniff = {"size": 5000, "container_guess": "mp4"}
    assert classify(p, sniff, S) is Category.AUDIO_PHYSICALLY_ABSENT


def test_duration_policy():
    a = StreamInfo(index=0, codec_type="audio", codec_name="aac", channels=2, channel_layout="stereo")
    p = ProbeResult(ok=True, duration_s=11000.0, streams=[a], stderr="")
    sniff = {"size": 9000, "container_guess": "wav"}
    assert classify(p, sniff, S) is Category.DURATION_GE_3H


def test_damaged_header():
    p = ProbeResult(ok=False, stderr="Invalid data found when processing input")
    sniff = {"size": 9000, "container_guess": "wav"}
    assert classify(p, sniff, S) is Category.DAMAGED_CONTAINER_HEADER


def test_non_monotonic_dts():
    p = ProbeResult(ok=False, stderr="Application provided invalid, non monotonically increasing dts")
    sniff = {"size": 9000, "container_guess": "mp4"}
    assert classify(p, sniff, S) is Category.NON_MONOTONIC_DTS_PTS


def test_zero_channels():
    a = StreamInfo(index=0, codec_type="audio", codec_name="pcm_s16le", channels=0)
    p = ProbeResult(ok=True, duration_s=5.0, streams=[a], stderr="")
    sniff = {"size": 9000, "container_guess": "wav"}
    assert classify(p, sniff, S) is Category.INCORRECT_CHANNEL_COUNT


def test_unknown_layout():
    a = StreamInfo(index=0, codec_type="audio", codec_name="aac", channels=2, channel_layout="unknown")
    p = ProbeResult(ok=True, duration_s=5.0, streams=[a], stderr="")
    sniff = {"size": 9000, "container_guess": "mp4"}
    assert classify(p, sniff, S) is Category.UNKNOWN_CHANNEL_LAYOUT
