# -*- mode: python ; coding: utf-8 -*-
"""VideoStamp PyInstaller 打包配置。

产物：dist/VideoStamp/ 目录
  VideoStamp.exe      GUI 主程序（无控制台窗口）
  VideoStampCLI.exe   命令行批量工具（控制台）
  vendor/ffmpeg/      外置 ffmpeg/ffprobe（可单独替换升级）
"""
FFMPEG_DATAS = [('videostamp/vendor/ffmpeg', 'vendor/ffmpeg')]

gui = Analysis(
    ['run_gui.py'],
    datas=FFMPEG_DATAS,
    excludes=['tkinter', 'matplotlib', 'numpy', 'PIL'],
)
pyz_gui = PYZ(gui.pure)

exe_gui = EXE(
    pyz_gui,
    gui.scripts,
    [],
    exclude_binaries=True,
    name='VideoStamp',
    debug=False,
    upx=False,
    console=False,
)

cli = Analysis(
    ['run_cli.py'],
    datas=FFMPEG_DATAS,
    excludes=['tkinter', 'matplotlib', 'numpy', 'PIL'],
)
pyz_cli = PYZ(cli.pure)

exe_cli = EXE(
    pyz_cli,
    cli.scripts,
    [],
    exclude_binaries=True,
    name='VideoStampCLI',
    debug=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe_gui,
    exe_cli,
    gui.binaries,
    gui.datas,
    cli.binaries,
    cli.datas,
    upx=False,
    name='VideoStamp',
)
