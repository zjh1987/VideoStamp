"""顺序连续时间戳计算测试。"""
import unittest
from datetime import datetime

from videostamp.core.timeline import compute_chained_starts

T0 = datetime(2026, 9, 11, 14, 30, 0)


class TestComputeChainedStarts(unittest.TestCase):
    def test_pure_chain(self):
        starts, broken = compute_chained_starts(
            ["a", "b", "c"], {"a": 10.0, "b": 20.0, "c": 30.0},
            {}, T0, sequential=True)
        self.assertEqual(starts["a"], T0)
        self.assertEqual(starts["b"], datetime(2026, 9, 11, 14, 30, 10))
        self.assertEqual(starts["c"], datetime(2026, 9, 11, 14, 30, 30))
        self.assertEqual(broken, [])

    def test_anchor_restarts_chain(self):
        # b 手动锚定到 15:00:00，c 从 b 的结束时间接续
        starts, broken = compute_chained_starts(
            ["a", "b", "c"], {"a": 10.0, "b": 20.0, "c": 5.0},
            {"b": "2026-09-11 15:00:00"}, T0, sequential=True)
        self.assertEqual(starts["a"], T0)
        self.assertEqual(starts["b"], datetime(2026, 9, 11, 15, 0, 0))
        self.assertEqual(starts["c"], datetime(2026, 9, 11, 15, 0, 20))
        self.assertEqual(broken, [])

    def test_missing_duration_breaks_chain(self):
        # b 时长未知 → b 起点仍是 a 的结束；c 无法接续 → broken
        starts, broken = compute_chained_starts(
            ["a", "b", "c"], {"a": 10.0},
            {}, T0, sequential=True)
        self.assertEqual(starts["a"], T0)
        self.assertEqual(starts["b"], datetime(2026, 9, 11, 14, 30, 10))
        self.assertNotIn("c", starts)
        self.assertEqual(broken, ["c"])

    def test_first_file_no_duration(self):
        # 第一个文件时长未知 → a 有起点（seq_start），其后全部断链
        starts, broken = compute_chained_starts(
            ["a", "b"], {}, {}, T0, sequential=True)
        self.assertEqual(starts["a"], T0)
        self.assertEqual(broken, ["b"])

    def test_non_sequential_only_anchors(self):
        starts, broken = compute_chained_starts(
            ["a", "b"], {"a": 10.0},
            {"b": "2026-09-11 09:00:00"}, T0, sequential=False)
        self.assertEqual(starts, {"b": datetime(2026, 9, 11, 9, 0, 0)})
        self.assertEqual(broken, [])

    def test_fractional_duration(self):
        starts, _ = compute_chained_starts(
            ["a", "b"], {"a": 10.4}, {}, T0, sequential=True)
        self.assertEqual(starts["b"], datetime(2026, 9, 11, 14, 30, 10, 400000))


if __name__ == "__main__":
    unittest.main()
