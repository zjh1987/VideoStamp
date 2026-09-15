"""端到端验证：pipeline.process_one 打戳 → 抽帧 → 人工核对时间显示。

预期画面底部显示: 2026-09-11 14:30:0x
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from videostamp.core.pipeline import StampPipeline, StampSettings  # noqa: E402
from videostamp.core.ffmpeg_paths import find_tools  # noqa: E402

TMP = ROOT / "tests" / "_e2e_tmp"


def main() -> int:
    shutil.rmtree(TMP, ignore_errors=True)
    TMP.mkdir(parents=True)
    ffmpeg, ffprobe = find_tools()

    # 1) 生成 3 秒测试视频（含音轨，验证音频 copy）
    src = TMP / "test_video.mp4"
    r = subprocess.run([
        str(ffmpeg), "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=s=640x360:d=3:r=25",
        "-f", "lavfi", "-i", "sine=frequency=440:d=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
        str(src),
    ], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr

    # 2) 打戳：手动指定起始时间
    s = StampSettings(time_mode="manual", manual_time="2026-09-11 14:30:00")
    logs: list[str] = []
    pipe = StampPipeline(s, log_cb=logs.append)
    result = pipe.process_one(src)
    for line in logs:
        print(line)
    print("result:", result.status, result.out, result.error)

    if result.status != "done":
        return 1

    # 3) 抽中间帧供人工核对
    png = TMP / "check_frame.png"
    r = subprocess.run([
        str(ffmpeg), "-y", "-v", "error", "-ss", "1", "-i", str(result.out),
        "-frames:v", "1", str(png),
    ], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    print("frame:", png)
    return 0


if __name__ == "__main__":
    sys.exit(main())
