"""filter_builder 单元测试。"""
import unittest
from pathlib import Path

from videostamp.core.filter_builder import (
    POSITIONS,
    StyleConfig,
    build_drawtext,
    build_graph,
    strftime_to_drawtext,
    validate_template,
)


class TestTemplateConvert(unittest.TestCase):
    def test_common_conversions(self):
        self.assertEqual(strftime_to_drawtext("%Y-%m-%d %H:%M:%S"),
                         "%Y-%m-%d %T")
        self.assertEqual(strftime_to_drawtext("%H:%M:%S"), "%T")
        self.assertEqual(strftime_to_drawtext("%H:%M"), "%R")
        self.assertEqual(strftime_to_drawtext("%Y-%m-%d"), "%Y-%m-%d")

    def test_literal_colon_rejected(self):
        with self.assertRaises(ValueError):
            strftime_to_drawtext("%H 点 %M:分")   # 残留字面冒号

    def test_validate(self):
        self.assertTrue(validate_template("%Y-%m-%d %H:%M:%S"))
        self.assertFalse(validate_template("hello"))
        self.assertFalse(validate_template("%Q-bad"))  # strftime 不识别输出原样→含Y? 无→False


class TestBuildDrawtext(unittest.TestCase):
    def cfg(self, **kw) -> StyleConfig:
        base = dict(start_epoch=1789137000, template="%Y-%m-%d %H:%M:%S",
                    font_path=Path("C:/Windows/Fonts/msyh.ttc"))
        base.update(kw)
        return StyleConfig(**base)

    def test_contains_key_parts(self):
        g = build_drawtext(self.cfg())
        self.assertIn("drawtext=", g)
        self.assertIn("fontfile=\\'C:/Windows/Fonts/msyh.ttc\\'", g)
        self.assertIn("text=\\'%{pts:gmtime:1789137000:%Y-%m-%d %T}\\'", g)
        self.assertIn("fontsize=28", g)

    def test_box(self):
        g = build_drawtext(self.cfg(box_opacity=50))
        self.assertIn("box=1", g)
        self.assertIn("boxcolor=black@0.50", g)

    def test_no_box(self):
        g = build_drawtext(self.cfg(box_opacity=0))
        self.assertNotIn("box=1", g)

    def test_all_positions(self):
        for pos in POSITIONS:
            g = build_drawtext(self.cfg(position=pos))
            self.assertIn(f"x=", g)

    def test_invalid_position(self):
        with self.assertRaises(ValueError):
            build_drawtext(self.cfg(position="elsewhere"))

    def test_graph_video_start_offset(self):
        g0 = build_graph(self.cfg(), 0.0)
        g10 = build_graph(self.cfg(), 10.0)   # >1s 的流起始时间会扣减
        self.assertIn("1789136990", g10)
        self.assertIn("1789137000", g0)
        g_small = build_graph(self.cfg(), 0.017)  # <1s 不动
        self.assertIn("1789137000", g_small)


if __name__ == "__main__":
    unittest.main()
