"""通用工具：Windows 下隐藏子进程控制台窗口。"""
import os
import subprocess

# 防止 GUI 打包后每次调用 ffmpeg 弹出黑色控制台窗口
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def popen_kwargs() -> dict:
    kw: dict = {}
    if os.name == "nt":
        kw["creationflags"] = CREATE_NO_WINDOW
    return kw
