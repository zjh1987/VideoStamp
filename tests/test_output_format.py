"""输出格式保持逻辑：编码族映射、容器选择、像素格式决策。"""
import unittest
from pathlib import Path

from videostamp.core.pipeline import (
    H264_DROP_EXTS,
    HEVC_KEEP_EXTS,
    decide_output,
    src_family,
)
from videostamp.core.probe import ProbeInfo


def info(codec: str | None, pix: str | None = "yuv420p") -> ProbeInfo:
    return ProbeInfo(has_video=True, video_codec=codec, pix_fmt=pix,
                     width=1920, height=1080)


class TestSrcFamily(unittest.TestCase):
    def test_hevc_variants(self):
        self.assertEqual(src_family("hevc"), "hevc")
        self.assertEqual(src_family("h265"), "hevc")
        self.assertEqual(src_family("HEVC"), "hevc")

    def test_others_to_h264(self):
        for c in ("h264", "mpeg4", "vp9", "av1", "mpeg2video", None):
            self.assertEqual(src_family(c), "h264", c)


class TestDecideOutput(unittest.TestCase):
    def test_h264_keeps_common_ext(self):
        for ext in (".mp4", ".mkv", ".mov", ".ts", ".avi", ".flv"):
            e, fam, pix, cpu = decide_output(Path(f"a{ext}"), info("h264"))
            self.assertEqual((e, fam, pix, cpu), (ext, "h264", "yuv420p", False))

    def test_h264_drops_incompatible_containers(self):
        for ext in H264_DROP_EXTS:
            e, fam, pix, cpu = decide_output(Path(f"a{ext}"), info("h264"))
            self.assertEqual((e, fam, pix, cpu), (".mp4", "h264", "yuv420p", False))

    def test_hevc_keeps_compatible_containers(self):
        for ext in HEVC_KEEP_EXTS:
            e, fam, pix, cpu = decide_output(Path(f"a{ext}"), info("hevc"))
            self.assertEqual((e, fam, pix, cpu), (ext, "hevc", "yuv420p", False))

    def test_hevc_incompatible_container_falls_to_mp4(self):
        e, fam, pix, cpu = decide_output(Path("a.avi"), info("hevc"))
        self.assertEqual((e, fam, pix, cpu), (".mp4", "hevc", "yuv420p", False))

    def test_hevc_10bit_stays_10bit_cpu(self):
        e, fam, pix, cpu = decide_output(Path("a.mkv"), info("hevc", "yuv420p10le"))
        self.assertEqual((e, fam, pix, cpu), (".mkv", "hevc", "yuv420p10le", True))

    def test_h264_10bit_downgrades_to_8bit(self):
        e, fam, pix, cpu = decide_output(Path("a.mp4"), info("h264", "yuv420p10le"))
        self.assertEqual((e, fam, pix, cpu), (".mp4", "h264", "yuv420p", False))

    def test_unknown_source_defaults(self):
        e, fam, pix, cpu = decide_output(Path("a.wmv"), info("wmv3", None))
        self.assertEqual((e, fam, pix, cpu), (".wmv", "h264", "yuv420p", False))


if __name__ == "__main__":
    unittest.main()
