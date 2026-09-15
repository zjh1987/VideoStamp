"""定位 ffmpeg/ffprobe 可执行文件。

查找顺序：
1. 打包目录或包目录下的 vendor/ffmpeg/ffmpeg.exe（便携分发）
2. 系统 PATH
"""
import shutil
import sys
from pathlib import Path


def vendor_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent  # videostamp/
    return base / "vendor" / "ffmpeg"


def _candidate_dirs() -> list[Path]:
    dirs = [vendor_dir()]
    # PyInstaller 6 onedir 把 datas 放在 _internal/ 下
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass) / "vendor" / "ffmpeg")
    return dirs


def find_tools() -> tuple[Path, Path]:
    """返回 (ffmpeg, ffprobe) 路径；找不到抛 FileNotFoundError。"""
    for d in _candidate_dirs():
        ff, fp = d / "ffmpeg.exe", d / "ffprobe.exe"
        if ff.exists() and fp.exists():
            return ff, fp
    w, p = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if w and p:
        return Path(w), Path(p)
    raise FileNotFoundError(
        "未找到 ffmpeg/ffprobe。请将 ffmpeg.exe 与 ffprobe.exe 放入: "
        + str(vendor_dir())
    )
