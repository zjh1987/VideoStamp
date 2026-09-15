"""命令行入口。

用法:
  python -m videostamp                     # 无参数 → 启动 GUI
  python -m videostamp --input <文件/目录> [--选项]   # 命令行批量打戳
"""
import argparse
import csv
import sys
from pathlib import Path

from .core.filter_builder import POSITIONS, DEFAULT_TEMPLATES
from .core.pipeline import StampPipeline, StampSettings, collect_inputs
from .core.time_source import parse_manual


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="videostamp",
        description="给视频批量烧录日期时间戳（FFmpeg 引擎）。无参数启动图形界面。",
    )
    p.add_argument("--input", nargs="+", help="输入视频文件或目录（可多个）")
    p.add_argument("--time-mode", default="metadata",
                   choices=["metadata", "ctime", "manual", "regex"],
                   help="时间源: metadata=容器元数据(默认,缺失回退文件创建时间), "
                        "ctime=文件创建时间, manual=手动指定, regex=文件名正则")
    p.add_argument("--start", help='manual 模式起始时间, 如 "2026-09-11 14:30:00"')
    p.add_argument("--pattern", help="regex 模式的文件名正则（命名分组 Y/m/d/H/M/S）")
    p.add_argument("--template", default="%Y-%m-%d %H:%M:%S",
                   help=f"时间格式模板, 默认 %%Y-%%m-%%d %%H:%%M:%%S；常用: {' / '.join(DEFAULT_TEMPLATES)}")
    p.add_argument("--pos", default="bottom-left", choices=sorted(POSITIONS),
                   help="九宫格位置, 默认 bottom-left")
    p.add_argument("--font", default="C:/Windows/Fonts/msyh.ttc", help="字体文件路径")
    p.add_argument("--size", type=int, default=28, help="字号, 默认 28")
    p.add_argument("--color", default="white", help="文字颜色, 默认 white")
    p.add_argument("--border", type=int, default=2, help="描边宽度, 默认 2")
    p.add_argument("--box", type=int, default=0, help="底框不透明度 0-100, 默认 0(无底框)")
    p.add_argument("--dx", type=int, default=20, help="水平偏移, 默认 20")
    p.add_argument("--dy", type=int, default=20, help="垂直偏移, 默认 20")
    p.add_argument("--hw", default="auto", choices=["auto", "cpu"],
                   help="编码器: auto=自动探测硬件(默认), cpu=libx264")
    p.add_argument("--out-dir", help="输出目录（默认每个源文件旁 stamped/）")
    p.add_argument("--suffix", default="_stamped", help="输出文件名后缀, 默认 _stamped")
    p.add_argument("--overwrite", action="store_true", help="覆盖已存在的输出文件")
    p.add_argument("--csv", help="结果清单导出 CSV 路径")
    p.add_argument("--dry-run", action="store_true",
                   help="只解析时间并显示计划，不执行打戳")
    return p


def settings_from_args(a: argparse.Namespace) -> StampSettings:
    if a.time_mode == "manual":
        if not a.start:
            raise SystemExit("manual 模式需要 --start")
        parse_manual(a.start)  # 提前校验格式
    return StampSettings(
        time_mode=a.time_mode,
        manual_time=a.start or "",
        filename_pattern=a.pattern or "",
        template=a.template,
        font_path=Path(a.font),
        font_size=a.size,
        color=a.color,
        border_w=a.border,
        box_opacity=a.box,
        position=a.pos,
        dx=a.dx,
        dy=a.dy,
        hw=a.hw,
        out_dir=Path(a.out_dir) if a.out_dir else None,
        suffix=a.suffix,
        overwrite=a.overwrite,
    )


def export_csv(results, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["源文件", "输出文件", "状态", "时间来源", "编码器", "错误"])
        for r in results:
            w.writerow([str(r.src), str(r.out or ""), r.status,
                        r.start_desc, r.encoder, r.error.replace("\n", " | ")])


def cli_main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if not a.input:
        print("未指定 --input；启动图形界面…\n")
        from .app.main_window import gui_main
        return gui_main()

    settings = settings_from_args(a)
    pipe = StampPipeline(settings, log_cb=lambda m: print(m, flush=True))

    if a.dry_run:
        files = collect_inputs([Path(x) for x in a.input])
        if not files:
            print("未找到可处理的视频文件")
            return 1
        rc = 0
        for f in files:
            try:
                plan = pipe.describe_one(f)
                print(f"[计划] {f.name} → {plan['out'].name}  "
                      f"起始={plan['start']} ({plan['source']})")
            except Exception as e:  # noqa: BLE001
                print(f"[失败] {f.name}: {e}")
                rc = 1
        return rc

    results = pipe.process_all([Path(x) for x in a.input])

    done = sum(1 for r in results if r.status == "done")
    skipped = sum(1 for r in results if r.status == "skipped")
    failed = sum(1 for r in results if r.status == "failed")
    print(f"\n汇总: 完成 {done} / 跳过 {skipped} / 失败 {failed} / 共 {len(results)}")

    if a.csv:
        export_csv(results, Path(a.csv))
        print(f"结果清单: {a.csv}")

    for r in results:
        if r.status == "failed":
            print(f"[失败] {r.src.name}: {r.error[:200]}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(cli_main())
