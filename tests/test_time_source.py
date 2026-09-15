"""time_source 单元测试。"""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from videostamp.core.time_source import (
    TimeSourceError,
    from_filename,
    parse_creation_time,
    parse_manual,
    resolve,
    to_start_epoch,
)


class TestParseManual(unittest.TestCase):
    def test_standard(self):
        self.assertEqual(parse_manual("2026-09-11 14:30:05"),
                         datetime(2026, 9, 11, 14, 30, 5))

    def test_slash_and_no_seconds(self):
        self.assertEqual(parse_manual("2026/09/11 14:30"),
                         datetime(2026, 9, 11, 14, 30))

    def test_date_only(self):
        self.assertEqual(parse_manual("2026-09-11"), datetime(2026, 9, 11))

    def test_compact(self):
        self.assertEqual(parse_manual("20260911_143005"),
                         datetime(2026, 9, 11, 14, 30, 5))

    def test_invalid(self):
        with self.assertRaises(TimeSourceError):
            parse_manual("not-a-time")
        with self.assertRaises(TimeSourceError):
            parse_manual("")


class TestCreationTime(unittest.TestCase):
    def test_utc_z_to_local(self):
        dt_utc = datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc)
        dt = parse_creation_time("2026-09-01T10:30:00.000000Z")
        # 本地 naive 应等于 UTC + 本地偏移（与时区无关的等价校验）
        offset = datetime.now().astimezone().utcoffset()
        self.assertEqual(dt, (dt_utc + offset).replace(tzinfo=None))
        # 把本地 naive 重新视作本地时区后转回 UTC，应与原 UTC 一致
        self.assertEqual(
            dt.replace(tzinfo=datetime.now().astimezone().tzinfo).astimezone(timezone.utc),
            dt_utc,
        )

    def test_offset_form(self):
        dt = parse_creation_time("2026-09-01T18:30:00+08:00")
        self.assertEqual(dt.hour, 18)
        self.assertEqual(dt.minute, 30)

    def test_invalid(self):
        with self.assertRaises(TimeSourceError):
            parse_creation_time("garbage")


class TestFilename(unittest.TestCase):
    def test_named_groups(self):
        p = Path("VID20260911_143005.mp4")
        pat = r"VID(?P<Y>\d{4})(?P<m>\d{2})(?P<d>\d{2})_(?P<H>\d{2})(?P<M>\d{2})(?P<S>\d{2})"
        self.assertEqual(from_filename(p, pat),
                         datetime(2026, 9, 11, 14, 30, 5))

    def test_partial_groups_defaults(self):
        p = Path("rec-20260911.mp4")
        pat = r"rec-(?P<Y>\d{4})(?P<m>\d{2})(?P<d>\d{2})"
        self.assertEqual(from_filename(p, pat), datetime(2026, 9, 11))

    def test_no_match(self):
        self.assertIsNone(from_filename(Path("abc.mp4"), r"VID(?P<Y>\d{4})"))
        self.assertIsNone(from_filename(Path("abc.mp4"), ""))

    def test_incomplete_groups_raises(self):
        p = Path("20260911-55.mp4")   # 正则可完整命中，但缺 H/M 分组
        pat = r"(?P<Y>\d{4})(?P<m>\d{2})(?P<d>\d{2})-(?P<S>\d{2})"
        with self.assertRaises(TimeSourceError):
            from_filename(p, pat)


class TestEpoch(unittest.TestCase):
    def test_gmtime_semantics(self):
        # gmtime 语义：本地墙钟当作 UTC。14:30 本地(+8) → epoch 等于 14:30 UTC
        dt = datetime(2026, 9, 11, 14, 30, 0)
        utc_same_wall = datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc)
        self.assertEqual(to_start_epoch(dt), int(utc_same_wall.timestamp()))


class TestResolve(unittest.TestCase):
    def test_metadata_fallback_to_ctime(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            p = Path(f.name)
        try:
            dt, desc = resolve("metadata", p, None)
            self.assertIn("回退", desc)
            # ctime 模式与回退结果一致（同一文件系统时间源）
            dt2, _ = resolve("ctime", p, None)
            self.assertEqual(dt, dt2)
        finally:
            p.unlink(missing_ok=True)

    def test_manual(self):
        dt, desc = resolve("manual", Path("x.mp4"), None, "2026-09-11 14:30:00")
        self.assertEqual(desc, "手动指定")
        self.assertEqual(dt, datetime(2026, 9, 11, 14, 30))


if __name__ == "__main__":
    unittest.main()
