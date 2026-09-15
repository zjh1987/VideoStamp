"""ffprobe 封装：读取视频时长、creation_time、流信息。"""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .util import popen_kwargs

# 这些音频编码可直接放进 mp4 容器，无需转码
AUDIO_COPY_OK = {"aac", "mp3", "ac3", "eac3"}


@dataclass
class ProbeInfo:
    duration: float = 0.0
    creation_time: str | None = None   # ISO8601 字符串（通常为 UTC）
    video_start: float | None = None   # 视频流 start_time（秒），TS 等容器可能非 0
    video_codec: str | None = None     # 如 h264 / hevc / mpeg4
    pix_fmt: str | None = None         # 如 yuv420p / yuv420p10le
    audio_codec: str | None = None
    has_audio: bool = False
    has_video: bool = False
    width: int = 0
    height: int = 0

    def get(self, path: Path, ffprobe: Path) -> None:  # noqa: ARG002
        raise NotImplementedError


def probe_video(path: Path, ffprobe: Path) -> ProbeInfo:
    cmd = [
        str(ffprobe), "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    p = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        errors="replace", **popen_kwargs(),
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffprobe 解析失败: {path.name}\n{p.stderr.strip()[:300]}")
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ffprobe 输出解析失败: {path.name}\n{e}") from e

    info = ProbeInfo()
    fmt = data.get("format", {})
    info.duration = float(fmt.get("duration") or 0.0)
    tags = fmt.get("tags") or {}
    info.creation_time = tags.get("creation_time")

    for st in data.get("streams", []):
        if st.get("codec_type") == "video" and not info.has_video:
            info.has_video = True
            info.video_codec = st.get("codec_name")
            info.pix_fmt = st.get("pix_fmt")
            info.width = int(st.get("width") or 0)
            info.height = int(st.get("height") or 0)
            try:
                info.video_start = float(st.get("start_time"))
            except (TypeError, ValueError):
                info.video_start = None
        elif st.get("codec_type") == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = st.get("codec_name")
    return info


def audio_args(info: ProbeInfo) -> list[str]:
    """根据源音频编码决定 copy 还是转 aac。"""
    if info.has_audio and info.audio_codec in AUDIO_COPY_OK:
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "160k"]
