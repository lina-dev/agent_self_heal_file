from audio_repair.core.config import get_settings
from audio_repair.core.models import DecodeVerifyResult, ProbeResult, StreamInfo, ToolResult
from audio_repair.core.sandbox import JobSandbox
from audio_repair.core.taxonomy import Category
from audio_repair.intake.validate import validate_audio

S = get_settings({})


class StubFt:
    def __init__(self, *, duration, probe_ok=True, has_audio=True,
                 extract_ok=True, decode_ok=True, container="mp4"):
        self.duration = duration
        self.probe_ok = probe_ok
        self.has_audio = has_audio
        self.extract_ok = extract_ok
        self.decode_ok = decode_ok
        self.container = container
        self.extract_calls = 0

    def probe(self, path):
        streams = [StreamInfo(index=0, codec_type="audio", codec_name="aac",
                              channels=2, channel_layout="stereo")] if self.has_audio else []
        return ProbeResult(
            ok=self.probe_ok, duration_s=self.duration, streams=streams,
            stderr="" if self.probe_ok else "Invalid data found when processing input",
        )

    def inspect_bytes(self, path):
        return {"size": 1000, "magic_hex": "00", "container_guess": self.container}

    def extract_audio(self, in_path, out_path, opts):
        self.extract_calls += 1
        if self.extract_ok:
            with open(out_path, "wb") as fh:
                fh.write(b"A")
            return ToolResult(tool="extract_audio", ok=True, output_path=out_path)
        return ToolResult(tool="extract_audio", ok=False, error="fail")

    def decode_verify(self, path):
        return DecodeVerifyResult(ok=self.decode_ok, error_count=0 if self.decode_ok else 3)


def test_length_gate_rejects(tmp_path):
    ft = StubFt(duration=11000.0)
    with JobSandbox(base=tmp_path) as sb:
        r = validate_audio(ft, sb, "/in.mp4", S)
    assert r.readable is False
    assert r.category is Category.DURATION_GE_3H
    assert ft.extract_calls == 0  # short-circuited before extraction


def test_readable_file(tmp_path):
    ft = StubFt(duration=5.0, extract_ok=True, decode_ok=True)
    with JobSandbox(base=tmp_path) as sb:
        r = validate_audio(ft, sb, "/in.mp4", S)
    assert r.readable is True
    assert r.audio_path is not None


def test_unreadable_extract_fails(tmp_path):
    ft = StubFt(duration=None, probe_ok=False, extract_ok=False)
    with JobSandbox(base=tmp_path) as sb:
        r = validate_audio(ft, sb, "/in.mp4", S)
    assert r.readable is False
    assert r.audio_path is None


def test_unreadable_decode_fails(tmp_path):
    ft = StubFt(duration=5.0, extract_ok=True, decode_ok=False)
    with JobSandbox(base=tmp_path) as sb:
        r = validate_audio(ft, sb, "/in.mp4", S)
    assert r.readable is False
