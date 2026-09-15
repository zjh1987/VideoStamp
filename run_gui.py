"""开发用 GUI 启动入口；打包时 PyInstaller 以此为入口脚本。"""
import sys

from videostamp.app.main_window import gui_main

if __name__ == "__main__":
    sys.exit(gui_main())
