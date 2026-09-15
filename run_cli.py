"""CLI 打包入口（videostamp.cli 使用相对导入，需包一层）。"""
import sys

from videostamp.cli import cli_main

if __name__ == "__main__":
    sys.exit(cli_main())
