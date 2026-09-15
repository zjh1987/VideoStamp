"""集成测试（需要 vendor/ffmpeg 就绪）：真实打戳并做像素级断言。

策略：纯黑视频 + 白色大字打戳 → 抽帧 → signalstats 检查亮度均值显著高于
无字黑帧，证明文字确实渲染进画面。
"""
import re
import subprocess
import unittest
from pathlib import Path

from videostamp.core.ffmpeg_paths import find_tools
from videostamp.core.pipeline import StampPipeline, StampSettings
from videostamp.core.util import popen_kwargs

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / "tests" / "_it_tmp"


def ffmpeg_available() -> bool:
    try:
        find_tools()
        return True
    except FileNotFoundError:
        return False


class TestIntegration(unittest.TestCase):
    ff = fp = None

    @classmethod
    def setUpClass(cls):
        cls.ff, cls.fp = find_tools()
        import shutil
        shutil.rmtree(TMP, ignore_errors=True)
        TMP.mkdir(parents=True)

    def _run(self, cmd):
        return subprocess.run([str(c) for c in cmd], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              **popen_kwargs())

    def _yavg(self, png: Path) -> float:
        r = self._run([self.ff, "-v", "info", "-i", png,
                       "-vf", "signalstats,metadata=mode=print",
                       "-f", "null", "-"])
        m = re.search(r"lavfi\.signalstats\.YAVG=([\d.]+)", r.stderr)
        self.assertIsNotNone(m, f"signalstats 无输出: {r.stderr[:300]}")
        return float(m.group(1))

    def _make_black_video(self, name: str) -> Path:
        src = TMP / name
        r = self._run([
            self.ff, "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=black:s=320x240:d=2:r=25",
            "-f", "lavfi", "-i", "sine=frequency=440:d=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", src,
        ])
        self.assertEqual(r.returncode, 0, r.stderr)
        return src

    def test_stamp_visible_and_audio_copied(self):
        # 1) 生成 2 秒纯黑测试视频（带音轨）
        src = self._make_black_video("black.mp4")

        # 2) 打戳（白色大字、左下角、黑底框提高显著性）
        s = StampSettings(
            time_mode="manual", manual_time="2026-09-11 14:30:00",
            font_size=48, box_opacity=60,
        )
        pipe = StampPipeline(s)
        result = pipe.process_one(src)
        self.assertEqual(result.status, "done",
                         f"打戳失败: {result.error}")
        self.assertTrue(result.out and result.out.exists())
        # 本机可能探测到 NVENC/QSV/AMF，也可能回退 CPU，均为正常
        self.assertIn(result.encoder,
                      ("cpu", "nvenc-h264", "qsv-h264", "amf-h264"))

        # 3) 输出时长与源一致（±0.5s）
        from videostamp.core.probe import probe_video
        info = probe_video(result.out, self.fp)
        self.assertAlmostEqual(info.duration, 2.0, delta=0.5)

        # 4) 像素断言：打戳后帧亮度显著高于纯黑（≈16）
        png_stamped = TMP / "f_stamped.png"
        self._run([self.ff, "-y", "-v", "error", "-ss", "1", "-i",
                   result.out, "-frames:v", "1", png_stamped])
        self.assertGreater(self._yavg(png_stamped), 20.0,
                           "画面中未检测到时间戳文字")

    def test_hevc_source_keeps_hevc(self):
        # hevc 源 → 输出仍为 hevc，分辨率/帧率不变
        from videostamp.core.probe import probe_video
        src = TMP / "hevc_src.mp4"
        r = self._run([
            self.ff, "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=black:s=320x240:d=1:r=25",
            "-c:v", "libx265", "-preset", "ultrafast",
            "-x265-params", "log-level=error", src,
        ])
        self.assertEqual(r.returncode, 0, r.stderr)
        s = StampSettings(time_mode="manual", manual_time="2026-09-11 14:30:00")
        result = StampPipeline(s).process_one(src)
        self.assertEqual(result.status, "done", f"error: {result.error}")
        self.assertEqual(result.out.suffix, ".mp4")
        out_info = probe_video(result.out, self.fp)
        self.assertEqual(out_info.video_codec, "hevc")
        self.assertEqual((out_info.width, out_info.height), (320, 240))

    def test_manual_time_override(self):
        # manual_time 参数覆盖全局时间源（GUI 逐文件设置时间的底层支撑）
        src = self._make_black_video("ovr.mp4")
        s = StampSettings(time_mode="ctime")
        r = StampPipeline(s).process_one(src, manual_time="2020-01-02 03:04:05")
        self.assertEqual(r.status, "done", f"error: {r.error}")
        self.assertEqual(r.start_desc, "手动指定")

    def test_skip_existing_idempotent(self):
        src = self._make_black_video("idem.mp4")   # 自建文件，避免测试顺序依赖
        s = StampSettings(time_mode="manual",
                          manual_time="2026-09-11 14:30:00")
        pipe = StampPipeline(s)
        r1 = pipe.process_one(src)
        self.assertEqual(r1.status, "done", f"error: {r1.error}")
        r2 = pipe.process_one(src)          # 第二次应跳过
        self.assertEqual(r2.status, "skipped")

    def test_cpu_encoder_path(self):
        # 强制 hw=cpu，覆盖 cpu_args 参数路径（无 GPU 环境的必经路径）
        src = self._make_black_video("cpu.mp4")
        s = StampSettings(time_mode="manual", manual_time="2026-09-11 14:30:00",
                          hw="cpu")
        r = StampPipeline(s).process_one(src)
        self.assertEqual(r.status, "done", f"error: {r.error}")
        self.assertEqual(r.encoder, "cpu")
        self.assertTrue(r.out and r.out.exists())

    def test_unsupported_file_rejected(self):
        fake = TMP / "fake.mp4"
        fake.write_bytes(b"not a video")
        pipe = StampPipeline(StampSettings())
        r = pipe.process_one(fake)
        self.assertEqual(r.status, "failed")


if __name__ == "__main__":
    unittest.main()
