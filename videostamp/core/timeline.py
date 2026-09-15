"""顺序连续时间戳：后一个视频的开始时间 = 前一个视频的结束时间。

纯计算逻辑（无 Qt 依赖），供 GUI 调用并测试。
"""
from datetime import datetime, timedelta

from .time_source import parse_manual


def compute_chained_starts(
    order: list[str],
    durations: dict[str, float],
    anchors: dict[str, str],
    seq_start: datetime,
    sequential: bool,
) -> tuple[dict[str, datetime], list[str]]:
    """计算每个视频的打戳起始时间。

    参数:
        order:      播放顺序（路径字符串列表，即导入顺序）
        durations:  路径 → 时长秒数（未知/探测失败的文件不在此表中）
        anchors:    路径 → 用户手动设定的起始时间 "YYYY-MM-DD HH:MM:SS"（重新锚点）
        seq_start:  第一个视频的起始时间（连续模式使用）
        sequential: 是否连续排列模式

    返回:
        starts: 路径 → 起始时间。未包含的文件按全局时间源处理。
        broken: 连续模式下因链条断裂（前面某文件时长未知）而无法
                自动计算、需回退全局时间源的文件列表。

    规则:
        - 连续模式: start[i] = start[i-1] + duration[i-1]；
          用户锚点处的文件用锚点时间，并从该锚点重新向下传递；
          链条遇到时长未知的文件即断开，其后文件回退全局时间源。
        - 非连续模式: 只有手动锚点的文件生效（原有行为）。
    """
    starts: dict[str, datetime] = {}
    broken: list[str] = []
    prev_end: datetime | None = None

    for i, p in enumerate(order):
        t: datetime | None = None
        a = anchors.get(p)
        if a:
            try:
                t = parse_manual(a)
            except ValueError:
                t = None
        elif sequential:
            if i == 0:
                t = seq_start
            elif prev_end is not None:
                t = prev_end
            else:
                broken.append(p)   # 链条已断，回退全局时间源

        if t is not None:
            starts[p] = t
            d = durations.get(p)
            prev_end = t + timedelta(seconds=d) if d is not None and d > 0 else None
        else:
            prev_end = None
    return starts, broken
