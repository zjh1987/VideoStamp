"""批量任务编排：收集输入 → 解析时间 → 生成滤镜 → 执行 → 重试回退。"""
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import hwprobe
from .ffmpeg_paths import find_tools
from .filter_builder import StyleConfig, build_graph, validate_template
from .probe import audio_args, probe_video
from .runner import Canceled, RunResult, run_stamp
from .time_source import resolve, to_start_epoch

VIDEO_EXTS = {
    ".mp4", ".mov", ".avi", ".mkv", ".ts", ".m2ts", ".mts", ".flv",
    ".webm", ".wmv", ".mpg", ".mpeg", ".m4v", ".3gp", ".mxf", ".vob",
}

# hevc 可保留的容器；hevc 源遇到其他容器时改用 mp4
HEVC_KEEP_EXTS = {".mp4", ".mov", ".mkv", ".ts", ".m2ts", ".mts"}
# h264 无法放入的容器 → 改用 mp4
H264_DROP_EXTS = {".webm", ".vob"}


def src_family(codec: str | None) -> str:
    """源视频编码 → 输出编码族（hevc 保持 hevc，其余统一 h264）。"""
    return "hevc" if (codec or "").lower() in ("hevc", "h265") else "h264"


def decide_output(src: Path, info) -> tuple[str, str, str, bool]:
    """根据源格式决定输出的后缀/编码族/像素格式/是否强制 CPU。

    返回 (ext, family, pix_fmt, force_cpu)。
    分辨率与帧率不打任何滤镜，天然与源一致。
    """
    family = src_family(info.video_codec)
    src_ext = src.suffix.lower()
    if family == "hevc":
        ext = src_ext if src_ext in HEVC_KEEP_EXTS else ".mp4"
    else:
        ext = src_ext if src_ext not in H264_DROP_EXTS else ".mp4"

    pix = (info.pix_fmt or "").lower()
    ten_bit = pix.endswith("10le") or pix == "p010le"
    if ten_bit and family == "hevc":
        # 保留 10bit 位深；各硬件编码器对 10bit 支持参差，统一走 CPU x265
        return ext, family, "yuv420p10le", True
    return ext, family, "yuv420p", False


@dataclass
class StampSettings:
    time_mode: str = "metadata"       # metadata | ctime | manual | regex
    manual_time: str = ""
    filename_pattern: str = ""
    template: str = "%Y-%m-%d %H:%M:%S"
    font_path: Path = Path("C:/Windows/Fonts/msyh.ttc")
    font_size: int = 28
    color: str = "white"
    border_w: int = 2
    border_color: str = "black"
    box_opacity: int = 0
    position: str = "bottom-left"
    dx: int = 20
    dy: int = 20
    hw: str = "auto"                  # auto | cpu
    out_dir: Path | None = None       # None → 每个源文件旁的 stamped/
    suffix: str = "_stamped"
    overwrite: bool = False


@dataclass
class TaskResult:
    src: Path
    out: Path | None
    status: str                       # done | skipped | failed | canceled
    start_desc: str = ""
    encoder: str = ""
    error: str = ""
    seconds: float = 0.0


def collect_inputs(paths: list[Path]) -> list[Path]:
    """展开目录，过滤出视频文件，去重排序。"""
    files: set[Path] = set()
    for p in paths:
        if p.is_dir():
            for f in p.iterdir():
                if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
                    files.add(f.resolve())
        elif p.is_file() and p.suffix.lower() in VIDEO_EXTS:
            files.add(p.resolve())
    return sorted(files, key=lambda x: str(x).lower())


class StampPipeline:
    def __init__(
        self,
        settings: StampSettings,
        log_cb: Callable[[str], None] | None = None,
        progress_cb: Callable[[float], None] | None = None,   # 当前文件 0-1
        cancel_check: Callable[[], bool] | None = None,
    ):
        self.s = settings
        self.log = log_cb or (lambda msg: None)
        self.progress = progress_cb or (lambda frac: None)
        self.cancel_check = cancel_check or (lambda: False)
        self.ffmpeg, self.ffprobe = find_tools()

    # ---- 单文件 -------------------------------------------------------
    def process_one(self, src: Path, manual_time: str | None = None) -> TaskResult:
        """manual_time 非空时覆盖全局时间源，强制按该起始时间打戳。"""
        try:
            return self._process_one_inner(src, manual_time)
        except Canceled:
            return TaskResult(src, None, "canceled")
        except Exception as e:  # noqa: BLE001 - 顶层兜底，不让单文件拖垮整批
            return TaskResult(src, None, "failed", error=str(e))

    def _process_one_inner(self, src: Path,
                           manual_time: str | None = None) -> TaskResult:
        s = self.s
        if not validate_template(s.template):
            return TaskResult(src, None, "failed", error=f"无效模板: {s.template!r}")

        self.log(f"[探测] {src.name}")
        info = probe_video(src, self.ffprobe)
        if not info.has_video:
            return TaskResult(src, None, "failed", error="无视频流")

        out_dir = s.out_dir or src.parent / "stamped"
        out_dir.mkdir(parents=True, exist_ok=True)
        ext, family, pix_fmt, force_cpu = decide_output(src, info)
        out = out_dir / (src.stem + s.suffix + ext)

        if out.exists() and not s.overwrite:
            self.log(f"[跳过] 已存在: {out.name}")
            return TaskResult(src, out, "skipped")

        if manual_time:
            dt, desc = resolve("manual", src, None, manual_time)
        else:
            dt, desc = resolve(
                s.time_mode, src, info.creation_time, s.manual_time,
                s.filename_pattern
            )
        epoch = to_start_epoch(dt)
        self.log(f"[时间] {src.name} → {dt.strftime('%Y-%m-%d %H:%M:%S')} ({desc})")

        style = StyleConfig(
            start_epoch=epoch, template=s.template, font_path=s.font_path,
            font_size=s.font_size, color=s.color, border_w=s.border_w,
            border_color=s.border_color, box_opacity=s.box_opacity,
            position=s.position, dx=s.dx, dy=s.dy,
        )
        graph = build_graph(style, info.video_start or 0.0)

        if force_cpu:
            name, venc_args = hwprobe.cpu_args(family)
        else:
            name, venc_args = hwprobe.detect(self.ffmpeg, s.hw, family)
        codec_note = f"{info.video_codec or '?'}/{info.pix_fmt or '?'}"
        if src_family(info.video_codec) != family:
            self.log(f"[提示] {src.name}: 源编码 {info.video_codec} 改用 "
                     f"H.264 输出（保持分辨率/帧率不变）")
        self.log(f"[编码] {src.name} → {name}（输出与源一致: {codec_note}{ext}）")
        dur = info.duration or 0.0

        out_args = ["-pix_fmt", pix_fmt]
        if family == "hevc" and ext in (".mp4", ".mov"):
            out_args += ["-tag:v", "hvc1"]   # Apple 兼容的 hevc 标记
        if ext in (".mp4", ".mov"):
            out_args += ["-movflags", "+faststart"]

        def cb(done_sec: float) -> None:
            if dur > 0:
                self.progress(min(done_sec / dur, 1.0))

        self.log(f"[执行] {src.name} → {out.name}")
        r: RunResult = run_stamp(
            self.ffmpeg, src, out, graph, venc_args,
            audio_args(info), dur, cb, self.cancel_check, out_args,
        )
        if not r.ok and not name.startswith("cpu"):
            self.log(f"[回退] {src.name} 硬件编码失败，改用 CPU 重试")
            if r.stderr_tail:
                self.log(f"    原因: {r.stderr_tail[0][:120]}")
            fb_name, fb_args = hwprobe.cpu_args(family)
            r = run_stamp(
                self.ffmpeg, src, out, graph, fb_args,
                audio_args(info), dur, cb, self.cancel_check, out_args,
            )
            name = fb_name
        if not r.ok:
            # 清理半成品
            try:
                out.unlink(missing_ok=True)
            except OSError:
                pass
            return TaskResult(src, None, "failed", encoder=name,
                              error=r.error_text() or f"ffmpeg 退出码 {r.returncode}")
        self.progress(1.0)
        self.log(f"[完成] {out.name}")
        return TaskResult(src, out, "done", start_desc=desc, encoder=name,
                          seconds=info.duration)

    # ---- 计划（dry-run）------------------------------------------------
    def describe_one(self, src: Path) -> dict:
        """解析时间与输出路径，不执行。返回 {out, start, source}。"""
        s = self.s
        out_dir = s.out_dir or src.parent / "stamped"
        info = probe_video(src, self.ffprobe)
        ext, _family, _pix, _cpu = decide_output(src, info)
        out = out_dir / (src.stem + s.suffix + ext)
        dt, desc = resolve(
            s.time_mode, src, info.creation_time, s.manual_time, s.filename_pattern
        )
        return {"out": out, "start": dt, "source": desc, "duration": info.duration}

    # ---- 批量 ---------------------------------------------------------
    def process_all(self, inputs: list[Path]) -> list[TaskResult]:
        files = collect_inputs(inputs)
        if not files:
            self.log("未找到可处理的视频文件")
            return []
        self.log(f"共 {len(files)} 个文件待处理")
        results: list[TaskResult] = []
        for i, f in enumerate(files, 1):
            if self.cancel_check():
                self.log("已取消")
                break
            self.log(f"--- 任务 {i}/{len(files)} ---")
            results.append(self.process_one(f))
        return results

    # ---- 预览 ---------------------------------------------------------
    def preview(self, src: Path, out_png: Path) -> Path:
        """渲染中间帧样式预览（不写输出视频）。"""
        import subprocess

        from .util import popen_kwargs

        s = self.s
        if not validate_template(s.template):
            raise ValueError(f"无效模板: {s.template!r}")
        info = probe_video(src, self.ffprobe)
        dt, _desc = resolve(
            s.time_mode, src, info.creation_time, s.manual_time, s.filename_pattern
        )
        epoch = to_start_epoch(dt)
        style = StyleConfig(
            start_epoch=epoch, template=s.template, font_path=s.font_path,
            font_size=s.font_size, color=s.color, border_w=s.border_w,
            border_color=s.border_color, box_opacity=s.box_opacity,
            position=s.position, dx=s.dx, dy=s.dy,
        )
        graph = build_graph(style, info.video_start or 0.0)
        mid = max((info.duration or 2.0) / 2.0, 0.0)
        cmd = [
            str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{mid:.3f}", "-i", str(src),
            "-filter_complex", graph,
            "-map", "[v]",
            "-frames:v", "1", str(out_png),
        ]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", **popen_kwargs())
        if p.returncode != 0:
            raise RuntimeError(p.stderr.strip()[:300])
        return out_png
