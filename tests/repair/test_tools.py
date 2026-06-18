import shutil

import pytest

from audio_repair.core.config import get_settings
from audio_repair.core.ffmpeg_tools import FfmpegTools
from audio_repair.core.sandbox import JobSandbox
from audio_repair.repair.tools import ToolRegistry

HAS_FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
S = get_settings({})


def _registry(sandbox, input_path="/nonexistent/in.bin"):
    ft = FfmpegTools(S)
    return ToolRegistry(ft, sandbox, input_path)


def test_specs_lists_seven_tools(tmp_path):
    with JobSandbox(base=tmp_path) as sb:
        reg = _registry(sb)
        names = {s.name for s in reg.specs()}
    assert names == {
        "probe", "remux", "extract_audio", "reencode",
        "force_format", "inspect_bytes", "decode_verify",
    }


def test_unknown_tool_returns_error(tmp_path):
    with JobSandbox(base=tmp_path) as sb:
        out = _registry(sb).invoke("nope", {})
    assert out["ok"] is False
    assert "unknown" in out["error"].lower()


def test_invalid_argument_rejected(tmp_path):
    with JobSandbox(base=tmp_path) as sb:
        out = _registry(sb).invoke("extract_audio", {"audio_codec": "; rm -rf /"})
    assert out["ok"] is False


def test_extra_field_rejected(tmp_path):
    with JobSandbox(base=tmp_path) as sb:
        out = _registry(sb).invoke("remux", {"shell": "true", "map_audio_only": True})
    assert out["ok"] is False


def test_bad_arguments_type(tmp_path):
    with JobSandbox(base=tmp_path) as sb:
        out = _registry(sb).invoke("probe", ["not", "a", "dict"])
    assert out["ok"] is False


def test_path_traversal_in_path_arg_rejected(tmp_path):
    with JobSandbox(base=tmp_path) as sb:
        out = _registry(sb).invoke("decode_verify", {"path": "../../etc/passwd"})
    assert out["ok"] is False


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_probe_runs_on_real_input(tmp_path):
    # Build a real 1s sine wav as the input.
    import subprocess

    src = tmp_path / "in.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-ac", "2", str(src)],
        check=True, capture_output=True,
    )
    with JobSandbox(base=tmp_path) as sb:
        out = _registry(sb, str(src)).invoke("probe", {})
    assert out["ok"] is True
    assert out["has_audio"] is True if "has_audio" in out else len(out["audio_streams"]) > 0


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")
def test_extract_audio_produces_sandbox_output(tmp_path):
    import subprocess

    src = tmp_path / "in.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-ac", "2", str(src)],
        check=True, capture_output=True,
    )
    with JobSandbox(base=tmp_path) as sb:
        out = _registry(sb, str(src)).invoke("extract_audio", {"audio_codec": "flac"})
        assert out["ok"] is True
        assert out["output_path"] is not None
        assert str(sb.path) in out["output_path"]
