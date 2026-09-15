"""硬件编码器探测：按目标编码族(h264/hevc)实测试编，失败回退 CPU。

探测时使用与正式打戳完全相同的编码参数，确保"探测可用"即"运行可用"。
先用 `-encoders` 过滤掉构建中不存在的编码器，避免无 GPU 机器上的无谓超时。
"""
import re
import subprocess
from pathlib import Path

from .util import popen_kwargs

# family → [(厂商名, 编码器名, 附加参数)]，按优先级排列
HW_ENCODERS: dict[str, list[tuple[str, str, list[str]]]] = {
    "h264": [
        ("nvenc", "h264_nvenc", ["-preset", "p4", "-cq", "21", "-b:v", "0"]),
        ("qsv",   "h264_qsv",   ["-global_quality", "21", "-preset", "medium"]),
        ("amf",   "h264_amf",   ["-quality", "balanced", "-rc", "cqp",
                                 "-qp_i", "22", "-qp_p", "22"]),
    ],
    "hevc": [
        ("nvenc", "hevc_nvenc", ["-preset", "p4", "-cq", "21", "-b:v", "0"]),
        ("qsv",   "hevc_qsv",   ["-global_quality", "21", "-preset", "medium"]),
        ("amf",   "hevc_amf",   ["-quality", "balanced", "-rc", "cqp",
                                 "-qp_i", "22", "-qp_p", "22"]),
    ],
}

# family → (名称, 编码参数，含 -c:v 前缀与编码器名)
CPU_ENCODERS: dict[str, tuple[str, list[str]]] = {
    "h264": ("cpu", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]),
    "hevc": ("cpu", ["-c:v", "libx265", "-preset", "veryfast", "-crf", "22"]),
}

_cache: dict[tuple[str, str], tuple[str, list[str]]] = {}


def cpu_args(family: str = "h264") -> tuple[str, list[str]]:
    """指定编码族的 CPU 编码参数（含编码器名）。"""
    return CPU_ENCODERS.get(family, CPU_ENCODERS["h264"])


def _test_encoder(ffmpeg: Path, enc: str, enc_args: list[str], timeout: int = 60) -> bool:
    cmd = [
        str(ffmpeg), "-v", "error", "-f", "lavfi",
        "-i", "color=black:s=256x256:d=0.2:r=25", "-frames:v", "5",
        "-c:v", enc, *enc_args, "-pix_fmt", "yuv420p", "-f", "null", "-",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, **popen_kwargs())
        return p.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _available_encoders(ffmpeg: Path) -> set[str]:
    """解析 -encoders，返回构建中存在的视频编码器名集合；失败返回空集合。"""
    try:
        p = subprocess.run(
            [str(ffmpeg), "-hide_banner", "-encoders"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, **popen_kwargs(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return set()
    names: set[str] = set()
    for line in (p.stdout or "").splitlines():
        m = re.match(r"\s*V\w*\s+(\S+)", line)
        if m:
            names.add(m.group(1))
    return names


def detect(ffmpeg: Path, mode: str = "auto", family: str = "h264") -> tuple[str, list[str]]:
    """mode: auto | cpu；family: h264 | hevc。

    返回 (名称, 编码参数列表)。
    名称形如 nvenc-h264 / qsv-hevc / cpu；参数含 -c:v 编码器指定。
    """
    if family not in HW_ENCODERS:
        family = "h264"
    key = (mode, family)
    if key in _cache:
        return _cache[key]
    if mode == "cpu":
        result = cpu_args(family)
    else:
        have = _available_encoders(ffmpeg)
        result = None
        for name, enc, args in HW_ENCODERS[family]:
            if have and enc not in have:
                continue  # 构建中根本没有，跳过避免超时
            if _test_encoder(ffmpeg, enc, args):
                result = (f"{name}-{family}", ["-c:v", enc, *args])
                break
        if result is None:
            result = cpu_args(family)
    _cache[key] = result
    return result
