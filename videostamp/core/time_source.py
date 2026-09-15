"""时间源解析：元数据 / 文件创建时间 / 手动指定 / 文件名正则。

统一约定：内部 datetime 为"本地时区 naive"；to_start_epoch() 转成 UTC epoch，
供 drawtext 的 %{pts:gmtime:epoch:fmt} 使用。
"""
import os
import re
from datetime import datetime
from pathlib import Path

MANUAL_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y%m%d_%H%M%S",
    "%Y%m%d%H%M%S",
)

FILENAME_GROUP_DEFAULTS = {"Y": 2000, "m": 1, "d": 1, "H": 0, "M": 0, "S": 0}
FILENAME_ORDER = ["Y", "m", "d", "H", "M", "S"]


class TimeSourceError(ValueError):
    pass


def parse_manual(s: str) -> datetime:
    s = (s or "").strip()
    for f in MANUAL_FORMATS:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    raise TimeSourceError(f"无法解析手动时间: {s!r}（支持格式如 2026-09-11 14:30:00）")


def parse_creation_time(iso: str) -> datetime:
    """解析 ffprobe 的 creation_time（ISO8601，通常带 Z 表示 UTC），转本地 naive。"""
    s = (iso or "").strip()
    if not s:
        raise TimeSourceError("creation_time 为空")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise TimeSourceError(f"无法解析元数据时间: {iso!r}") from e
    if dt.tzinfo is not None:
        dt = dt.astimezone()          # 转本地时区
        dt = dt.replace(tzinfo=None)  # 去掉 tzinfo 保留本地读数
    return dt


def from_file_ctime(path: Path) -> datetime:
    """Windows 上 getctime 是创建时间；其他平台是 inode 变更时间。"""
    return datetime.fromtimestamp(os.path.getctime(str(path)))


def from_filename(path: Path, pattern: str) -> datetime | None:
    """按正则提取文件名时间，需提供命名分组 Y/m/d/H/M/S（可部分缺省）。"""
    if not pattern:
        return None
    m = re.search(pattern, path.name)
    if not m:
        return None
    gd = m.groupdict()

    def g(k: str) -> int:
        v = gd.get(k)
        return int(v) if v is not None else FILENAME_GROUP_DEFAULTS[k]

    # 完整性校验：给定的分组必须构成从 Y 开始的连续前缀
    # （Y,m,d 可；Y,d、d,H,M、Y,m,d,S 均不可，中间不允许跳过）
    given = [k for k in FILENAME_ORDER if gd.get(k) is not None]
    if given:
        expected = FILENAME_ORDER[:len(given)]
        if given != expected:
            missing = [k for k in expected if k not in given]
            raise TimeSourceError(f"文件名正则分组不完整: 缺少 {missing}")
    return datetime(g("Y"), g("m"), g("d"), g("H"), g("M"), g("S"))


def resolve(mode: str, path: Path, creation_time: str | None,
            manual_time: str = "", pattern: str = "") -> tuple[datetime, str]:
    """根据模式解析起始时间。返回 (本地naive时间, 来源描述)。

    兜底策略：metadata/regex 解析不到时回退文件创建时间。
    """
    if mode == "manual":
        return parse_manual(manual_time), "手动指定"
    if mode == "ctime":
        return from_file_ctime(path), "文件创建时间"
    if mode == "metadata":
        if creation_time:
            try:
                return parse_creation_time(creation_time), "视频元数据"
            except TimeSourceError:
                pass
        return from_file_ctime(path), "元数据缺失→回退文件创建时间"
    if mode == "regex":
        dt = from_filename(path, pattern)
        if dt:
            return dt, "文件名正则"
        return from_file_ctime(path), "正则未命中→回退文件创建时间"
    raise TimeSourceError(f"未知时间源模式: {mode}")


def to_start_epoch(dt: datetime) -> int:
    """本地 naive 时间 → 供 drawtext %{pts:gmtime:...} 使用的 epoch。

    gmtime 按 UTC 规则格式化给定值，因此这里把"本地墙钟当作 UTC"
    计算（calendar.timegm），画面才能显示原本地时间。
    """
    import calendar
    return calendar.timegm(dt.timetuple())
