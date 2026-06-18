"""Allow-listed, sandboxed ffmpeg/ffprobe wrappers (spec §7).

The agent never constructs a shell string. Every operation takes a typed,
`extra="forbid"` options model whose fields are constrained (Literals / bounded
ints), and we build an explicit argv list from validated values. This makes
command injection structurally impossible: an attacker-controlled string can
never become a flag or a second command.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .config import Settings
from .models import DecodeVerifyResult, ProbeResult, StreamInfo, ToolResult
from .sandbox import run_argv

# --- allow-lists -----------------------------------------------------------

AudioCodec = Literal[
    "copy",
    "aac",
    "libmp3lame",
    "pcm_s16le",
    "pcm_s24le",
    "flac",
    "libopus",
    "libvorbis",
]
ErrDetect = Literal[
    "", "ignore_err", "crccheck", "careful", "compliant", "aggressive", "explode"
]
FFlags = Literal[
    "", "+genpts", "+igndts", "+discardcorrupt", "+genpts+igndts", "+genpts+discardcorrupt"
]
RawInputFormat = Literal["s16le", "s16be", "s24le", "u8", "f32le", "mulaw", "alaw"]

_VALID_SAMPLE_RATES = {8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 96000}


class RemuxOpts(BaseModel):
    model_config = {"extra": "forbid"}
    fflags: FFlags = ""
    map_audio_only: bool = True
    movflags_faststart: bool = False


class ExtractOpts(BaseModel):
    model_config = {"extra": "forbid"}
    stream_index: int = Field(default=0, ge=0, le=63)
    audio_codec: AudioCodec = "copy"
    err_detect: ErrDetect = ""
    fflags: FFlags = ""


class ReencodeOpts(BaseModel):
    model_config = {"extra": "forbid"}
    audio_codec: AudioCodec = "aac"
    sample_rate: int | None = None
    channels: int | None = Field(default=None, ge=1, le=8)
    sample_fmt: str | None = None

    def validated_rate(self) -> int | None:
        if self.sample_rate is not None and self.sample_rate not in _VALID_SAMPLE_RATES:
            raise ValueError(f"unsupported sample_rate: {self.sample_rate}")
        return self.sample_rate


class ForceFormatOpts(BaseModel):
    model_config = {"extra": "forbid"}
    input_format: RawInputFormat = "s16le"
    sample_rate: int = Field(default=44100, ge=4000, le=192000)
    channels: int = Field(default=1, ge=1, le=8)
    output_codec: AudioCodec = "pcm_s16le"


# --- helpers ---------------------------------------------------------------


def _to_float(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_int(v: object) -> int | None:
    try:
        return int(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class FfmpegTools:
    def __init__(self, settings: Settings, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe"):
        self.s = settings
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def _t(self) -> int:
        return self.s.ffmpeg_tool_timeout_s

    # -- probe / inspect ----------------------------------------------------

    def _stream(self, d: dict) -> StreamInfo:
        return StreamInfo(
            index=_to_int(d.get("index")) or 0,
            codec_type=str(d.get("codec_type", "unknown")),
            codec_name=d.get("codec_name"),
            channels=_to_int(d.get("channels")),
            sample_rate=_to_int(d.get("sample_rate")),
            sample_fmt=d.get("sample_fmt"),
            channel_layout=d.get("channel_layout"),
            duration_s=_to_float(d.get("duration")),
        )

    def probe(self, path: str) -> ProbeResult:
        argv = [
            self.ffprobe, "-v", "error", "-show_format", "-show_streams",
            "-of", "json", "-probesize", "50M", "-analyzeduration", "50M", path,
        ]
        r = run_argv(argv, self._t())
        if r.timed_out:
            return ProbeResult(ok=False, stderr=r.stderr, error="probe timeout")
        try:
            data = json.loads(r.stdout or "{}")
        except json.JSONDecodeError as e:
            return ProbeResult(ok=False, stderr=r.stderr, error=f"probe json error: {e}")
        streams = [self._stream(s) for s in data.get("streams", [])]
        fmt = data.get("format", {})
        dur = _to_float(fmt.get("duration"))
        ok = r.returncode == 0 and bool(streams)
        return ProbeResult(
            ok=ok,
            format_name=fmt.get("format_name"),
            duration_s=dur,
            streams=streams,
            stderr=r.stderr,
            error=None if ok else (r.stderr.strip() or "probe failed"),
        )

    def inspect_bytes(self, path: str) -> dict:
        p = Path(path)
        try:
            size = p.stat().st_size
        except OSError as e:
            return {"size": 0, "magic_hex": "", "container_guess": None, "error": str(e)}
        head = b""
        if size:
            with p.open("rb") as fh:
                head = fh.read(16)
        return {
            "size": size,
            "magic_hex": head.hex(),
            "container_guess": _sniff_container(head),
        }

    # -- transforms ---------------------------------------------------------

    def _run_tool(self, tool: str, argv: list[str], out_path: str) -> ToolResult:
        start = time.monotonic()
        r = run_argv(argv, self._t())
        elapsed = int((time.monotonic() - start) * 1000)
        produced = Path(out_path).exists() and Path(out_path).stat().st_size > 0
        ok = (r.returncode == 0) and not r.timed_out and produced
        err = None
        if r.timed_out:
            err = "tool timeout"
        elif not ok:
            err = r.stderr.strip()[-500:] or "tool failed / no output"
        return ToolResult(
            tool=tool,
            ok=ok,
            output_path=out_path if produced else None,
            stderr=r.stderr[-2000:],
            error=err,
            returncode=r.returncode,
            duration_ms=elapsed,
        )

    def remux(self, in_path: str, out_path: str, opts: RemuxOpts) -> ToolResult:
        argv = [self.ffmpeg, "-y"]
        if opts.fflags:
            argv += ["-fflags", opts.fflags]
        argv += ["-i", in_path]
        if opts.map_audio_only:
            argv += ["-map", "0:a?", "-vn"]
        argv += ["-c", "copy"]
        if opts.movflags_faststart:
            argv += ["-movflags", "+faststart"]
        argv += [out_path]
        return self._run_tool("remux", argv, out_path)

    def extract_audio(self, in_path: str, out_path: str, opts: ExtractOpts) -> ToolResult:
        argv = [self.ffmpeg, "-y"]
        if opts.err_detect:
            argv += ["-err_detect", opts.err_detect]
        if opts.fflags:
            argv += ["-fflags", opts.fflags]
        argv += ["-i", in_path, "-vn", "-map", f"0:a:{opts.stream_index}", "-c:a", opts.audio_codec, out_path]
        return self._run_tool("extract_audio", argv, out_path)

    def reencode(self, in_path: str, out_path: str, opts: ReencodeOpts) -> ToolResult:
        rate = opts.validated_rate()
        argv = [self.ffmpeg, "-y", "-i", in_path, "-vn", "-c:a", opts.audio_codec]
        if rate is not None:
            argv += ["-ar", str(rate)]
        if opts.channels is not None:
            argv += ["-ac", str(opts.channels)]
        if opts.sample_fmt is not None:
            if not opts.sample_fmt.replace("_", "").isalnum():
                raise ValueError(f"invalid sample_fmt: {opts.sample_fmt}")
            argv += ["-sample_fmt", opts.sample_fmt]
        argv += [out_path]
        return self._run_tool("reencode", argv, out_path)

    def force_format(self, in_path: str, out_path: str, opts: ForceFormatOpts) -> ToolResult:
        argv = [
            self.ffmpeg, "-y",
            "-f", opts.input_format, "-ar", str(opts.sample_rate), "-ac", str(opts.channels),
            "-i", in_path, "-c:a", opts.output_codec, out_path,
        ]
        return self._run_tool("force_format", argv, out_path)

    def decode_verify(self, path: str) -> DecodeVerifyResult:
        argv = [self.ffmpeg, "-v", "error", "-xerror", "-i", path, "-f", "null", "-"]
        r = run_argv(argv, self._t())
        lines = [ln for ln in r.stderr.splitlines() if ln.strip()]
        ok = (r.returncode == 0) and not r.timed_out and len(lines) == 0
        return DecodeVerifyResult(ok=ok, error_count=len(lines), stderr=r.stderr[-2000:])


def _sniff_container(head: bytes) -> str | None:
    """Best-effort magic-byte container guess. Returns None for non-media."""
    if not head:
        return None
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"\x1aE\xdf\xa3":
        return "matroska"
    if head[:4] == b"FORM":
        return "aiff"
    return None
