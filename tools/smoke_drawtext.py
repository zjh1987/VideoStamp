"""drawtext 语法冒烟测试：验证 %{pts:gmtime:epoch:fmt} 的转义写法。

生成 3 秒黑底视频 → 应用不同写法的滤镜 → 提取中间帧 PNG 供人工查验。
期望画面显示: 2026-09-11 14:30:01 附近的本地时间。
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FF = ROOT / "videostamp" / "vendor" / "ffmpeg" / "ffmpeg.exe"
TMP = ROOT / "tests" / "_smoke_tmp"


def run(cmd, **kw):
    return subprocess.run([str(c) if isinstance(c, Path) else c for c in cmd],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kw)


def main() -> int:
    TMP.mkdir(exist_ok=True)
    src = TMP / "black3s.mp4"

    r = run([FF, "-y", "-v", "error", "-f", "lavfi",
             "-i", "color=black:s=640x360:d=3:r=25",
             "-c:v", "libx264", "-preset", "ultrafast", src])
    assert r.returncode == 0, r.stderr

    # 2026-09-11 14:30:00 本地时间对应的 epoch
    epoch = int(datetime(2026, 9, 11, 14, 30, 0).timestamp())
    print("epoch =", epoch)

    fmt = "%Y-%m-%d %H\\:%M\\:%S"          # strftime 中 ':' 需 \: 转义
    fmt_double = fmt.replace("\\", "\\\\")  # 双重转义版
    fontfile = "C:/Windows/Fonts/msyh.ttc"
    fontfile_esc = fontfile.replace(":", "\\\\:")  # C\\:/...
    style = ("fontsize=36:fontcolor=white:borderw=2:bordercolor=black:"
             "box=1:boxcolor=black@0.5:boxborderw=10:x=20:y=h-th-20")

    variants = {
        # C1: 引号用 \' 传到选项层（graph 层消费反斜杠），
        #     strftime 的 \: 在引号内原样存储给 drawtext 展开器
        "c1_escaped_quotes": (
            f"[0:v]drawtext=fontfile=\\'{fontfile}\\':"
            f"text=\\'%{{pts:gmtime:{epoch}:{fmt}}}\\':{style}[v]"
        ),
        # C2: 全程不用引号，逐层双重转义
        "c2_no_quotes": (
            f"[0:v]drawtext=fontfile={fontfile_esc}:"
            f"text=%{{pts\\\\:gmtime\\\\:{epoch}\\\\:{fmt_double}}}:"
            f"{style}[v]"
        ),
    }

    ok_variants = []
    for name, graph in variants.items():
        print(f"--- {name} ---")
        print("  graph:", graph)
        out = TMP / f"out_{name}.mp4"
        r = run([FF, "-y", "-v", "error", "-i", src,
                 "-filter_complex", graph, "-map", "[v]",
                 "-c:v", "libx264", "-preset", "ultrafast", out])
        print(f"{name}: rc={r.returncode}", r.stderr.strip()[:200])
        if r.returncode == 0:
            png = TMP / f"frame_{name}.png"
            r2 = run([FF, "-y", "-v", "error", "-ss", "1", "-i", out,
                      "-frames:v", "1", png])
            print(f"  frame: rc={r2.returncode} -> {png.name}")
            ok_variants.append(name)

    print("可用写法:", ok_variants or "无！需要调整")
    return 0 if ok_variants else 1


if __name__ == "__main__":
    sys.exit(main())
