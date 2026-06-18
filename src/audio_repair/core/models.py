"""Typed pydantic result models used end-to-end (spec §7, §9)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, computed_field


class StreamInfo(BaseModel):
    index: int
    codec_type: str
    codec_name: str | None = None
    channels: int | None = None
    sample_rate: int | None = None
    sample_fmt: str | None = None
    channel_layout: str | None = None
    duration_s: float | None = None


class ProbeResult(BaseModel):
    ok: bool
    format_name: str | None = None
    duration_s: float | None = None
    streams: list[StreamInfo] = []
    stderr: str = ""
    error: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def audio_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "audio"]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def video_streams(self) -> list[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "video"]

    def has_audio(self) -> bool:
        return len(self.audio_streams) > 0


class ToolResult(BaseModel):
    tool: str
    ok: bool
    output_path: str | None = None
    stderr: str = ""
    error: str | None = None
    returncode: int | None = None
    duration_ms: int = 0


class DecodeVerifyResult(BaseModel):
    ok: bool
    error_count: int
    stderr: str = ""


class RepairReport(BaseModel):
    status: Literal["ok", "repaired", "unrepairable", "rejected"]
    category: str | None = None
    input_s3: str
    output_s3: str | None = None
    report_s3: str | None = None
    attempts: list[ToolResult] = []
    strategy: str | None = None
    original_params: dict = {}
    final_params: dict | None = None
    reason: str | None = None
    elapsed_ms: int = 0
