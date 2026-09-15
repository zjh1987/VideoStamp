"""FFmpeg 9.0 drawtext 时间函数语法探测。

逐一测试候选写法，输出可用清单，并渲染帧 PNG 供人工核对显示内容。
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FF = ROOT / "videostamp" / "vendor" / "ffmpeg" / "ffmpeg.exe"
TMP = ROOT / "tests" / "_smoke_tmp"
EPOCH = int(datetime(2026, 9, 11, 14, 30, 0).timestamp())
FONT = "C:/Windows/Fonts/msyh.ttc"
STYLE = ("fontsize=36:fontcolor=white:borderw=2:bordercolor=black:"
         "box=1:boxcolor=black@0.5:boxborderw=10:x=20:y=h-th-20")


def run(cmd):
    return subprocess.run([str(c) if isinstance(c, Path) else c for c in cmd],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def main() -> int:
    TMP.mkdir(exist_ok=True)
    src = TMP / "black3s.mp4"
    if not src.exists():
        r = run([FF, "-y", "-v", "error", "-f", "lavfi",
                 "-i", "color=black:s=640x360:d=3:r=25",
                 "-c:v", "libx264", "-preset", "ultrafast", src])
        assert r.returncode == 0, r.stderr

    variants = {
        # A: pts 函数 + gmtime 标志，%T 规避格式串冒号
        "A_pts_gmtime_T": (
            f"[0:v]drawtext=fontfile=\\'{FONT}\\':"
            f"text=\\'%{{pts:gmtime:{EPOCH}:%Y-%m-%d %T}}\\':{STYLE}[v]"
        ),
        # B: 独立 gmtime 函数 + setpts 偏移
        "B_gmtime_setpts": (
            f"[0:v]setpts=PTS+{EPOCH}/TB,"
            f"drawtext=fontfile=\\'{FONT}\\':"
            f"text=\\'%{{gmtime:%Y-%m-%d %T}}\\':{STYLE}[v]"
        ),
        # C: pts+gmtime 无格式串（看默认输出格式）
        "C_pts_gmtime_noformat": (
            f"[0:v]drawtext=fontfile=\\'{FONT}\\':"
            f"text=\\'%{{pts:gmtime:{EPOCH}}}\\':{STYLE}[v]"
        ),
        # D: 独立 gmtime 带偏移参数（2 参数形式）
        "D_gmtime_offset_fmt": (
            f"[0:v]drawtext=fontfile=\\'{FONT}\\':"
            f"text=\\'%{{gmtime:{EPOCH}:%Y-%m-%d %T}}\\':{STYLE}[v]"
        ),
        # E: pts+localtime+偏移（对照）
        "E_pts_localtime": (
            f"[0:v]drawtext=fontfile=\\'{FONT}\\':"
            f"text=\\'%{{pts:localtime:{EPOCH}:%Y-%m-%d %T}}\\':{STYLE}[v]"
        ),
    }

    ok = []
    for name, graph in variants.items():
        out = TMP / f"o_{name}.mp4"
        r = run([FF, "-y", "-v", "error", "-i", src,
                 "-filter_complex", graph, "-map", "[v]",
                 "-c:v", "libx264", "-preset", "ultrafast", out])
        status = "OK " if r.returncode == 0 else "ERR"
        print(f"[{status}] {name}", "" if r.returncode == 0 else r.stderr.strip()[:160])
        if r.returncode == 0:
            png = TMP / f"f_{name}.png"
            r2 = run([FF, "-y", "-v", "error", "-ss", "1", "-i", out,
                      "-frames:v", "1", png])
            if r2.returncode == 0:
                ok.append(name)
                print(f"       frame -> {png.name}")

    print("可用写法:", ok or "无")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
