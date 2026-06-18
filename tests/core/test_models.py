import pytest

from audio_repair.core.models import ProbeResult, RepairReport, StreamInfo


def test_probe_has_audio_and_computed():
    s = StreamInfo(
        index=0,
        codec_type="audio",
        codec_name="aac",
        channels=2,
        sample_rate=44100,
        sample_fmt="fltp",
        channel_layout="stereo",
        duration_s=12.0,
    )
    v = StreamInfo(index=1, codec_type="video", codec_name="h264")
    p = ProbeResult(ok=True, format_name="mov", duration_s=12.0, streams=[s, v], stderr="")
    assert p.has_audio() is True
    assert len(p.audio_streams) == 1
    assert p.audio_streams[0].codec_name == "aac"
    assert len(p.video_streams) == 1


def test_report_roundtrip_json():
    r = RepairReport(
        status="repaired",
        category="DAMAGED_INDEX",
        input_s3="s3://b/k.mp4",
        output_s3="s3://b/k.repaired.mp4",
        attempts=[],
        strategy="fastpath",
        original_params={"codec": "aac"},
        final_params={"codec": "aac"},
        reason=None,
        elapsed_ms=42,
    )
    data = r.model_dump_json()
    assert "repaired" in data
    assert RepairReport.model_validate_json(data).status == "repaired"


def test_status_literal_rejects_bad_value():
    with pytest.raises(ValueError):
        RepairReport(
            status="weird",  # type: ignore[arg-type]
            input_s3="x",
            original_params={},
            attempts=[],
            elapsed_ms=0,
        )
