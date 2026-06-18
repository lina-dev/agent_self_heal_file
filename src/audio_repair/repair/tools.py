"""Typed agent tool registry mapping LLM tool calls to vetted ffmpeg argv.

This is the security boundary between the model and the operating system. The
agent can only ask for one of seven named tools; arguments are validated by
constructing the matching `extra="forbid"` pydantic opts model (rejecting any
unknown or out-of-range value), and every output file is written through
`sandbox.resolve(...)` so nothing can escape the per-job temp dir. `invoke`
never raises into the agent loop — failures come back as `{"ok": False, ...}`.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError

from ..core.ffmpeg_tools import (
    ExtractOpts,
    FfmpegTools,
    ForceFormatOpts,
    ReencodeOpts,
    RemuxOpts,
)
from ..core.sandbox import JobSandbox
from ..llm.client import ToolSpec

# Output container/extension chosen per tool.
_EXT = {
    "remux": "mka",
    "extract_audio": "mka",
    "reencode": "m4a",
    "force_format": "wav",
}


class ToolRegistry:
    def __init__(self, ffmpeg_tools: FfmpegTools, sandbox: JobSandbox, input_path: str):
        self.ft = ffmpeg_tools
        self.sandbox = sandbox
        self.input_path = input_path

    # -- advertised specs ---------------------------------------------------

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="probe",
                description="Run ffprobe and return container/streams/duration.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            ToolSpec(
                name="inspect_bytes",
                description="Return file size, magic-byte hex, and a container guess.",
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            ToolSpec(
                name="decode_verify",
                description="Full decode pass; reports whether the file decodes cleanly.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Optional prior tool output filename; defaults to the input.",
                        }
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="remux",
                description="Stream-copy audio into a clean container (no re-encode).",
                parameters={
                    "type": "object",
                    "properties": {
                        "fflags": {
                            "type": "string",
                            "enum": ["", "+genpts", "+igndts", "+discardcorrupt",
                                     "+genpts+igndts", "+genpts+discardcorrupt"],
                        },
                        "map_audio_only": {"type": "boolean"},
                        "movflags_faststart": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="extract_audio",
                description="Extract one audio stream, optionally with error concealment.",
                parameters={
                    "type": "object",
                    "properties": {
                        "stream_index": {"type": "integer", "minimum": 0, "maximum": 63},
                        "audio_codec": {
                            "type": "string",
                            "enum": ["copy", "aac", "libmp3lame", "pcm_s16le",
                                     "pcm_s24le", "flac", "libopus", "libvorbis"],
                        },
                        "err_detect": {
                            "type": "string",
                            "enum": ["", "ignore_err", "crccheck", "careful",
                                     "compliant", "aggressive", "explode"],
                        },
                        "fflags": {
                            "type": "string",
                            "enum": ["", "+genpts", "+igndts", "+discardcorrupt",
                                     "+genpts+igndts", "+genpts+discardcorrupt"],
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="reencode",
                description="Re-encode audio to a target codec/rate/channels.",
                parameters={
                    "type": "object",
                    "properties": {
                        "audio_codec": {
                            "type": "string",
                            "enum": ["aac", "libmp3lame", "pcm_s16le", "pcm_s24le",
                                     "flac", "libopus", "libvorbis"],
                        },
                        "sample_rate": {"type": "integer"},
                        "channels": {"type": "integer", "minimum": 1, "maximum": 8},
                        "sample_fmt": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSpec(
                name="force_format",
                description="Interpret headerless raw PCM with an explicit format.",
                parameters={
                    "type": "object",
                    "properties": {
                        "input_format": {
                            "type": "string",
                            "enum": ["s16le", "s16be", "s24le", "u8", "f32le", "mulaw", "alaw"],
                        },
                        "sample_rate": {"type": "integer", "minimum": 4000, "maximum": 192000},
                        "channels": {"type": "integer", "minimum": 1, "maximum": 8},
                        "output_codec": {
                            "type": "string",
                            "enum": ["aac", "libmp3lame", "pcm_s16le", "pcm_s24le",
                                     "flac", "libopus", "libvorbis"],
                        },
                    },
                    "additionalProperties": False,
                },
            ),
        ]

    # -- invocation ---------------------------------------------------------

    def invoke(self, name: str, arguments: dict | None) -> dict:
        args = arguments or {}
        if not isinstance(args, dict):
            return {"ok": False, "error": "arguments must be an object"}
        handler = getattr(self, f"_do_{name}", None)
        if handler is None:
            return {"ok": False, "error": f"unknown tool: {name}"}
        try:
            return handler(args)
        except ValidationError as e:
            return {"ok": False, "error": f"invalid arguments: {e.errors()[0]['msg']}"}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001 - never crash the loop
            return {"ok": False, "error": f"tool error: {e}"}

    def _out(self, name: str) -> str:
        return str(self.sandbox.resolve(f"{name}_{uuid.uuid4().hex[:8]}.{_EXT[name]}"))

    def _resolve_input(self, args: dict) -> str:
        """A `path` argument names a prior in-sandbox output; default = input."""
        raw = args.get("path")
        if not raw:
            return self.input_path
        # Only a bare filename inside the sandbox is allowed.
        return str(self.sandbox.resolve(str(raw)))

    def _do_probe(self, args: dict) -> dict:
        return self.ft.probe(self._resolve_input(args)).model_dump()

    def _do_inspect_bytes(self, args: dict) -> dict:
        return self.ft.inspect_bytes(self._resolve_input(args))

    def _do_decode_verify(self, args: dict) -> dict:
        return self.ft.decode_verify(self._resolve_input(args)).model_dump()

    def _do_remux(self, args: dict) -> dict:
        opts = RemuxOpts(**args)
        out = self._out("remux")
        return self.ft.remux(self.input_path, out, opts).model_dump()

    def _do_extract_audio(self, args: dict) -> dict:
        opts = ExtractOpts(**args)
        out = self._out("extract_audio")
        return self.ft.extract_audio(self.input_path, out, opts).model_dump()

    def _do_reencode(self, args: dict) -> dict:
        opts = ReencodeOpts(**args)
        out = self._out("reencode")
        return self.ft.reencode(self.input_path, out, opts).model_dump()

    def _do_force_format(self, args: dict) -> dict:
        opts = ForceFormatOpts(**args)
        out = self._out("force_format")
        return self.ft.force_format(self.input_path, out, opts).model_dump()
