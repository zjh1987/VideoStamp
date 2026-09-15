"""ffmpeg 子进程执行：进度解析、stderr 捕获、可取消。"""
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .util import popen_kwargs

STDERR_TAIL_LINES = 30


class Canceled(Exception):
    pass


@dataclass
class RunResult:
    returncode: int
    stderr_tail: list[str]

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def error_text(self) -> str:
        return "\n".join(self.stderr_tail[-8:])


def _parse_progress_line(line: str) -> float | None:
    """解析 -progress 输出，返回已输出秒数。"""
    if "=" not in line:
        return None
    k, _, v = line.strip().partition("=")
    if k == "out_time_us":
        try:
            return float(v) / 1_000_000.0
        except ValueError:
            return None
    if k == "out_time_ms":  # 历史命名，实际单位微秒
        try:
            return float(v) / 1_000_000.0
        except ValueError:
            return None
    if k == "out_time":
        try:
            hh, mm, ss = v.split(":")
            return int(hh) * 3600 + int(mm) * 60 + float(ss)
        except (ValueError, AttributeError):
            return None
    return None


def run_stamp(
    ffmpeg: Path,
    src: Path,
    out: Path,
    filter_graph: str,
    venc_args: list[str],
    a_args: list[str],
    duration: float,
    progress_cb: Callable[[float], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    out_args: list[str] | None = None,
) -> RunResult:
    """执行单次打戳。progress_cb(seconds_done) 在主线程循环中回调。

    filter_graph 通过 subprocess 列表参数直接传给 -filter_complex，
    不经过 cmd.exe，因此无需处理 shell 转义。
    out_args: 像素格式/容器选项（如 -pix_fmt、-tag:v、-movflags），由调用方按源格式组装。
    """
    cmd = [
        str(ffmpeg), "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-i", str(src),
        "-filter_complex", filter_graph,
        "-map", "[v]", "-map", "0:a?",
        *venc_args,
        *(out_args or []),
        *a_args,
        str(out),
        "-progress", "pipe:1", "-nostats",
    ]

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", **popen_kwargs(),
    )

    stderr_tail: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_tail.append(line.rstrip())
            if len(stderr_tail) > 500:
                del stderr_tail[:250]

    t = threading.Thread(target=_drain_stderr, daemon=True)
    t.start()

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel_check is not None and cancel_check():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise Canceled("用户取消")
            sec = _parse_progress_line(line)
            if sec is not None and progress_cb is not None:
                progress_cb(sec)
        rc = proc.wait()
    finally:
        t.join(timeout=2)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass

    return RunResult(returncode=rc, stderr_tail=stderr_tail)
