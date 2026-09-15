"""下载 FFmpeg（Windows x64 GPL 构建）到 videostamp/vendor/ffmpeg/。

用法:
    python tools/fetch_ffmpeg.py             # 默认 BtbN FFmpeg 8.1 构建
    python tools/fetch_ffmpeg.py --url <zip> # 自定义构建下载地址

说明: 程序运行与打包都需要 ffmpeg.exe/ffprobe.exe，本脚本从 BtbN
FFmpeg-Builds 的 Release 下载并解压到 vendor 目录（该目录已被
.gitignore 排除，不会进入版本库）。
"""
import argparse
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_URL = ("https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/"
               "ffmpeg-n8.1-latest-win64-gpl-8.1.zip")
DEST = Path(__file__).resolve().parent.parent / "videostamp" / "vendor" / "ffmpeg"


def _progress(count: int, block: int, total: int) -> None:
    done = count * block / (1024 * 1024)
    if total > 0:
        sys.stdout.write(f"\r下载中 {done:6.1f}/{total / 1048576:.1f} MB")
    else:
        sys.stdout.write(f"\r下载中 {done:6.1f} MB")
    sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL, help="FFmpeg 构建 zip 下载地址")
    ap.add_argument("--dest", default=str(DEST), help="目标目录")
    args = ap.parse_args()
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)

    if (dest / "ffmpeg.exe").exists() and (dest / "ffprobe.exe").exists():
        print("ffmpeg/ffprobe 已存在，跳过下载:", dest)
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="videostamp_ffmpeg_"))
    try:
        z = tmp / "ffmpeg.zip"
        print("下载:", args.url)
        urllib.request.urlretrieve(args.url, z, _progress)
        print("\n解压…")
        with zipfile.ZipFile(z) as zf:
            zf.extractall(tmp / "x")
        for exe in ("ffmpeg.exe", "ffprobe.exe"):
            hits = list((tmp / "x").rglob(exe))
            if not hits:
                sys.exit(f"错误: 压缩包内未找到 {exe}")
            shutil.copy2(hits[0], dest / exe)
            print(f"→ {dest / exe}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
