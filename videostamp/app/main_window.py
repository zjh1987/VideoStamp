"""VideoStamp 图形界面（PySide6）。

布局:
  ┌ 时间轴文件表（拖拽/添加/排序/连续时间预览）───────┐
  ├ 参数面板（时间源/样式/输出）┬ 预览画面            ┤
  ├──────────────────────────┴────────────────────┤
  │ 进度条 / 日志                                   │
  └───────────────────────────────────────────────┘

连续时间戳模式（chk_seq）: 按导入顺序，后一个视频的起始时间自动
接续前一个视频的结束时间（开始+时长），只需设定第一个视频的时间；
双击某行可手动微调，其后的视频会从微调处重新顺延。
"""
import sys
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QSettings, Signal, QDateTime
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QColorDialog, QComboBox,
    QDateTimeEdit, QDialog, QFileDialog, QGridLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..cli import export_csv
from ..core import hwprobe
from ..core.ffmpeg_paths import find_tools, vendor_dir
from ..core.filter_builder import DEFAULT_TEMPLATES, POSITIONS
from ..core.pipeline import StampPipeline, StampSettings, TaskResult, collect_inputs
from ..core.probe import probe_video
from ..core.timeline import compute_chained_starts

FONTS_DIR = Path("C:/Windows/Fonts")
APP_TITLE = "VideoStamp - 视频批量加时间戳"


def app_config_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config"
    return Path(__file__).resolve().parent.parent.parent / "config"


def list_fonts() -> list[Path]:
    if not FONTS_DIR.exists():
        return []
    fonts = [f for f in FONTS_DIR.iterdir()
             if f.suffix.lower() in (".ttf", ".ttc", ".otf")]
    return sorted(fonts, key=lambda p: p.name.lower())


def _fmt_hms(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


class StampWorker(QThread):
    """批量打戳工作线程。"""
    sig_log = Signal(str)
    sig_overall = Signal(int)          # 总进度 0-100
    sig_current = Signal(str)
    sig_done = Signal(list)            # list[TaskResult]

    def __init__(self, settings: StampSettings, inputs: list[Path],
                 file_times: dict[str, str] | None = None):
        super().__init__()
        self.settings = settings
        self.inputs = inputs
        self.file_times = file_times or {}   # 路径字符串 → 该文件起始时间
        self.cancel_event = threading.Event()

    def stop(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        files = collect_inputs(self.inputs)
        total = len(files)
        done = 0
        results: list[TaskResult] = []

        def on_file_progress(frac: float) -> None:
            if total > 0:
                self.sig_overall.emit(int((done + frac) / total * 100))

        pipe = StampPipeline(
            self.settings,
            log_cb=self.sig_log.emit,
            progress_cb=on_file_progress,
            cancel_check=self.cancel_event.is_set,
        )
        for i, f in enumerate(files, 1):
            if self.cancel_event.is_set():
                self.sig_log.emit("已取消")
                break
            self.sig_current.emit(f"({i}/{total}) {f.name}")
            results.append(pipe.process_one(
                f, manual_time=self.file_times.get(str(f))))
            done = i
            self.sig_overall.emit(int(done / total * 100))
        self.sig_done.emit(results)


class ProbeWorker(QThread):
    """探测视频时长（连续时间戳计算用）。"""
    sig_one = Signal(str, float, bool)   # (路径, 时长秒, 是否成功)
    sig_done = Signal(list)              # 本批探测失败的路径列表

    def __init__(self, paths: list[str]):
        super().__init__()
        self.paths = paths

    def run(self) -> None:
        missing: list[str] = []
        try:
            _ff, fp = find_tools()
        except FileNotFoundError:
            for p in self.paths:
                self.sig_one.emit(p, 0.0, False)
            self.sig_done.emit(list(self.paths))
            return
        for p in self.paths:
            try:
                info = probe_video(Path(p), fp)
                ok = bool(info.has_video and info.duration and info.duration > 0)
                self.sig_one.emit(p, info.duration or 0.0, ok)
            except Exception:  # noqa: BLE001
                self.sig_one.emit(p, 0.0, False)
                ok = False
            if not ok:
                missing.append(p)
        self.sig_done.emit(missing)


class HWDetectWorker(QThread):
    """启动时后台探测硬件编码器，更新界面提示。"""
    sig_done = Signal(str, bool)       # (名称, 是否硬件)

    def __init__(self) -> None:
        super().__init__()
        self.error: str | None = None

    def run(self) -> None:
        try:
            ff, _fp = find_tools()
            name, _args = hwprobe.detect(ff, "auto", "h264")
        except Exception as e:  # noqa: BLE001
            self.error = repr(e)
            name = "cpu"
        self.sig_done.emit(name, not name.startswith("cpu"))


class PreviewWorker(QThread):
    """单帧样式预览线程。"""
    sig_ok = Signal(str)               # png 路径
    sig_err = Signal(str)

    def __init__(self, settings: StampSettings, src: Path):
        super().__init__()
        self.settings = settings
        self.src = src
        self.png = Path(tempfile.gettempdir()) / "videostamp_preview.png"

    def run(self) -> None:
        try:
            pipe = StampPipeline(self.settings)
            pipe.preview(self.src, self.png)
            self.sig_ok.emit(str(self.png))
        except Exception as e:  # noqa: BLE001
            self.sig_err.emit(str(e))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.worker: StampWorker | None = None
        self.preview_worker: PreviewWorker | None = None
        self.probe_worker: ProbeWorker | None = None
        self.last_results: list[TaskResult] = []
        self.color_hex = "#ffffff"
        self.file_times: dict[str, str] = {}       # 手动锚点: 路径 → 起始时间
        self.durations: dict[str, float] = {}      # 路径 → 时长秒（缓存）
        self._probing: set[str] = set()            # 正在探测时长的文件
        self._probe_missing: set[str] = set()      # 探测失败的文件
        self.effective_starts: dict[str, datetime] = {}   # 计算出的起始时间
        self.broken: list[str] = []                # 连续链条断开的文件

        try:
            find_tools()
        except FileNotFoundError as e:
            QMessageBox.critical(self, APP_TITLE, str(e))
            sys.exit(1)

        self.setWindowTitle(APP_TITLE)
        self.resize(1080, 760)
        self.setAcceptDrops(True)
        self._build_ui()
        self._load_settings()
        # 后台探测硬件编码器，结果展示在 lbl_hw
        self.hw_worker = HWDetectWorker()
        self.hw_worker.sig_done.connect(self._on_hw_detected)
        self.hw_worker.start()

    def _on_hw_detected(self, name: str, is_hw: bool) -> None:
        vendor = name.split("-")[0].upper() if is_hw else ""
        if is_hw:
            self.lbl_hw.setText(f"硬件编码：{vendor}（可用，自动优先使用）")
        else:
            self.lbl_hw.setText(
                "硬件编码：未检测到可用 GPU 编码，将使用 CPU（x264/x265）。\n"
                "如有独立显卡，请更新显卡驱动后重试。")

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        # ---- 时间轴文件表 ----
        gb_files = QGroupBox(
            "视频文件（支持拖入文件/文件夹；双击某行可微调该文件的起始时间）")
        v = QVBoxLayout(gb_files)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["#", "文件名", "开始时间", "结束时间", "时长", "备注"])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for c, w in ((0, 34), (2, 150), (3, 150), (4, 76), (5, 200)):
            self.table.setColumnWidth(c, w)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        v.addWidget(self.table)

        self.lbl_timeline = QLabel("共 0 个视频")
        self.lbl_timeline.setStyleSheet("color:#666;")
        v.addWidget(self.lbl_timeline)

        self.chk_seq = QCheckBox(
            "按顺序连续排列时间戳（后一个视频自动接续前一个的结束时间，只需设置第一个视频的起始时间）")
        self.chk_seq.setChecked(True)
        self.chk_seq.toggled.connect(self._on_seq_toggled)
        v.addWidget(self.chk_seq)

        hb = QHBoxLayout()
        for text, slot in (
            ("添加文件", self.on_add_files), ("添加文件夹", self.on_add_dir),
            ("移除选中", self.on_remove_selected), ("清空", self.on_clear_files),
            ("上移", lambda: self._move_rows(-1)), ("下移", lambda: self._move_rows(1)),
        ):
            b = QPushButton(text)
            b.clicked.connect(slot)
            hb.addWidget(b)
        hb.addStretch(1)
        b_file_time = QPushButton("设置选中文件起始时间")
        b_file_time.clicked.connect(self.on_set_file_time)
        hb.addWidget(b_file_time)
        b_clr_time = QPushButton("清除手动时间")
        b_clr_time.clicked.connect(self.on_clear_file_time)
        hb.addWidget(b_clr_time)
        v.addLayout(hb)
        root.addWidget(gb_files, 2)

        # ---- 参数区（左）+ 预览（右）----
        splitter = QSplitter(Qt.Orientation.Horizontal)

        gb_cfg = QGroupBox("参数设置")
        grid = QGridLayout(gb_cfg)
        grid.setVerticalSpacing(8)

        # 时间源
        grid.addWidget(QLabel("时间源"), 0, 0)
        self.cmb_time_mode = QComboBox()
        self.cmb_time_mode.addItem("视频元数据（推荐）", "metadata")
        self.cmb_time_mode.addItem("文件创建时间", "ctime")
        self.cmb_time_mode.addItem("手动指定", "manual")
        self.cmb_time_mode.addItem("文件名正则", "regex")
        self.cmb_time_mode.currentIndexChanged.connect(self._time_mode_changed)
        grid.addWidget(self.cmb_time_mode, 0, 1, 1, 3)

        grid.addWidget(QLabel("起始时间"), 1, 0)
        self.dt_start = QDateTimeEdit(QDateTime.currentDateTime())
        self.dt_start.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_start.setCalendarPopup(True)
        self.dt_start.dateTimeChanged.connect(lambda: self._recompute_times())
        grid.addWidget(self.dt_start, 1, 1, 1, 3)

        grid.addWidget(QLabel("文件名正则"), 2, 0)
        self.ed_pattern = QLineEdit()
        self.ed_pattern.setPlaceholderText(
            r"如 VID(?P<Y>\d{4})(?P<m>\d{2})(?P<d>\d{2})"
        )
        grid.addWidget(self.ed_pattern, 2, 1, 1, 3)

        # 样式
        grid.addWidget(QLabel("格式模板"), 3, 0)
        self.cmb_template = QComboBox()
        self.cmb_template.setEditable(True)
        self.cmb_template.addItems(DEFAULT_TEMPLATES)
        grid.addWidget(self.cmb_template, 3, 1, 1, 3)

        grid.addWidget(QLabel("字体"), 4, 0)
        self.cmb_font = QComboBox()
        for f in list_fonts():
            self.cmb_font.addItem(f.stem, str(f))
        if FONTS_DIR.joinpath("msyh.ttc").exists():
            self.cmb_font.setCurrentIndex(
                self.cmb_font.findData(str(FONTS_DIR / "msyh.ttc")))
        grid.addWidget(self.cmb_font, 4, 1)

        grid.addWidget(QLabel("字号"), 4, 2)
        self.sp_size = QSpinBox()
        self.sp_size.setRange(8, 200)
        self.sp_size.setValue(28)
        grid.addWidget(self.sp_size, 4, 3)

        grid.addWidget(QLabel("文字颜色"), 5, 0)
        self.btn_color = QPushButton("白")
        self.btn_color.clicked.connect(self.on_pick_color)
        grid.addWidget(self.btn_color, 5, 1)

        grid.addWidget(QLabel("描边宽"), 5, 2)
        self.sp_border = QSpinBox()
        self.sp_border.setRange(0, 20)
        self.sp_border.setValue(2)
        grid.addWidget(self.sp_border, 5, 3)

        grid.addWidget(QLabel("底框不透明度"), 6, 0)
        self.sl_box = QSlider(Qt.Orientation.Horizontal)
        self.sl_box.setRange(0, 100)
        self.sl_box.setValue(0)
        grid.addWidget(self.sl_box, 6, 1)

        grid.addWidget(QLabel("位置"), 6, 2)
        self.cmb_pos = QComboBox()
        self.cmb_pos.addItems(sorted(POSITIONS))
        self.cmb_pos.setCurrentText("bottom-left")
        grid.addWidget(self.cmb_pos, 6, 3)

        grid.addWidget(QLabel("偏移 X / Y"), 7, 0)
        self.sp_dx = QSpinBox()
        self.sp_dx.setRange(0, 4000)
        self.sp_dx.setValue(20)
        self.sp_dy = QSpinBox()
        self.sp_dy.setRange(0, 4000)
        self.sp_dy.setValue(20)
        hxy = QHBoxLayout()
        hxy.addWidget(self.sp_dx)
        hxy.addWidget(self.sp_dy)
        grid.addLayout(hxy, 7, 1)

        # 输出
        grid.addWidget(QLabel("输出目录"), 8, 0)
        self.ed_outdir = QLineEdit()
        self.ed_outdir.setPlaceholderText("留空 = 每个源文件旁 stamped/ 文件夹")
        grid.addWidget(self.ed_outdir, 8, 1, 1, 2)
        b_out = QPushButton("…")
        b_out.setFixedWidth(32)
        b_out.clicked.connect(self.on_pick_outdir)
        grid.addWidget(b_out, 8, 3)

        grid.addWidget(QLabel("文件名后缀"), 9, 0)
        self.ed_suffix = QLineEdit("_stamped")
        grid.addWidget(self.ed_suffix, 9, 1)

        self.chk_overwrite = QCheckBox("覆盖已存在输出")
        grid.addWidget(self.chk_overwrite, 9, 2)

        grid.addWidget(QLabel("编码器"), 9, 3)
        self.cmb_hw = QComboBox()
        self.cmb_hw.addItem("自动（推荐）", "auto")
        self.cmb_hw.addItem("CPU (x264/x265)", "cpu")
        # 放入同一行右侧会越界，换行放置
        grid.addWidget(self.cmb_hw, 10, 1)

        b_preview = QPushButton("预览选中文件样式")
        b_preview.clicked.connect(self.on_preview)
        grid.addWidget(b_preview, 10, 2, 1, 2)

        # 编码器探测状态（启动后台检测后更新）
        self.lbl_hw = QLabel("编码器检测中…")
        grid.addWidget(self.lbl_hw, 11, 1, 1, 3)

        splitter.addWidget(gb_cfg)

        # 预览
        gb_prev = QGroupBox("样式预览（中间帧）")
        vp = QVBoxLayout(gb_prev)
        self.lbl_preview = QLabel("点击\"预览选中文件样式\"")
        self.lbl_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_preview.setMinimumSize(360, 240)
        self.lbl_preview.setStyleSheet("background:#202020;color:#aaa;")
        vp.addWidget(self.lbl_preview)
        splitter.addWidget(gb_prev)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 3)

        # ---- 进度与日志 ----
        hb_run = QHBoxLayout()
        self.btn_start = QPushButton("开始打戳")
        self.btn_start.clicked.connect(self.on_start)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.on_cancel)
        self.btn_csv = QPushButton("导出结果CSV")
        self.btn_csv.setEnabled(False)
        self.btn_csv.clicked.connect(self.on_export_csv)
        hb_run.addWidget(self.btn_start)
        hb_run.addWidget(self.btn_cancel)
        hb_run.addWidget(self.btn_csv)
        self.bar_overall = QProgressBar()
        self.bar_overall.setValue(0)
        hb_run.addWidget(self.bar_overall, 1)
        root.addLayout(hb_run)

        self.lbl_current = QLabel("")
        root.addWidget(self.lbl_current)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(1000)
        root.addWidget(self.log_view, 2)

        self.setCentralWidget(central)
        self._add_tooltips()

    def _add_tooltips(self) -> None:
        """为各控件补充悬浮说明与示例。"""
        self.table.setToolTip(
            "把视频文件或整个文件夹拖到这里，列表顺序即播放/打戳顺序（可用上移/下移调整）。\n"
            "· 连续模式勾选时：开始/结束时间自动计算，后一个视频接续前一个的结束时间\n"
            "· 双击某一行可手动微调该文件的起始时间，其后的文件会自动顺延\n"
            "· \"时长未知\"表示无法读取视频时长，连续排列会在该文件处断开")
        self.lbl_timeline.setToolTip(
            "汇总信息：视频个数、已知总时长，以及连续模式下的时间跨度\n"
            "（第一个视频的开始时间到最后一个视频的结束时间）。")
        self.chk_seq.setToolTip(
            "勾选（推荐用于批量录制片段）：\n"
            "  只需在\"起始时间\"里填第一个视频的开始时间，\n"
            "  后续视频自动 = 前一个视频的结束时间（开始+时长），依次连续排列。\n"
            "  双击列表中某行可微调单个文件，其后文件自动顺延。\n"
            "取消勾选：恢复按\"时间源\"设置逐文件取时间（元数据/创建时间等）。")
        self.cmb_time_mode.setToolTip(
            "各视频从哪个时刻开始计时（未勾选连续模式时生效）：\n"
            "· 视频元数据（推荐）：读取拍摄设备写入的创建时间，缺失时自动改用文件创建时间\n"
            "· 文件创建时间：取 Windows 文件属性里的创建时间（文件被复制过可能失真）\n"
            "· 手动指定：用下方\"起始时间\"作为所有文件的时间\n"
            "· 文件名正则：从文件名里提取时间")
        self.dt_start.setToolTip(
            "· 连续模式：第一个视频的起始时间（如 2026-09-11 14:30:00），\n"
            "  后续视频自动接续\n"
            "· 手动指定模式：所有视频的起始时间\n"
            "也可双击列表某行单独微调某个文件。")
        self.ed_pattern.setToolTip(
            "从文件名提取时间的正则，必须用命名分组 Y/m/d/H/M/S（可只写到分钟）。\n"
            "示例：文件名 VID20260911_143005.mp4 配\n"
            "  VID(?P<Y>\\d{4})(?P<m>\\d{2})(?P<d>\\d{2})_(?P<H>\\d{2})(?P<M>\\d{2})(?P<S>\\d{2})\n"
            "rec-20260911.mp4 配  rec-(?P<Y>\\d{4})(?P<m>\\d{2})(?P<d>\\d{2})\n"
            "注意：分组必须从 Y 开始连续（Y,m,d 可以；Y,d 或 d,H,M 不行）。\n"
            "未命中时自动回退为文件创建时间。")
        self.cmb_template.setToolTip(
            "画面上显示的时间格式（strftime 写法）：\n"
            "· %Y-%m-%d %H:%M:%S → 2026-09-11 14:30:05\n"
            "· %Y/%m/%d %H:%M:%S → 2026/09/11 14:30:05\n"
            "· %Y-%m-%d → 只显示日期；%H:%M:%S → 只显示时间\n"
            "注意：时分秒之间的冒号请用 %T（等价 %H:%M:%S）或 %R（%H:%M）表示，\n"
            "不要自己写 %H:%M:%S 以外的含冒号组合。")
        self.cmb_font.setToolTip("时间文字使用的字体，默认微软雅黑。")
        self.sp_size.setToolTip("字号（像素），常用 24-48。小分辨率视频建议 20-28。")
        self.btn_color.setToolTip(
            "文字颜色，默认白色。深色画面用白/黄，浅色画面用黑/红。")
        self.sp_border.setToolTip(
            "文字描边宽度（像素）。描边可让文字在任何背景下都清晰，建议 2。")
        self.sl_box.setToolTip(
            "文字背后的半透明黑底框浓度：0=无底框，60-80 在杂乱背景下更醒目。")
        self.cmb_pos.setToolTip(
            "时间戳在画面上的位置（九宫格）。常用 bottom-left（左下角）。")
        self.sp_dx.setToolTip("距画面边缘的水平偏移（像素），默认 20。")
        self.sp_dy.setToolTip("距画面边缘的垂直偏移（像素），默认 20。")
        self.ed_outdir.setToolTip(
            "输出保存目录。留空 = 在每个源文件旁边的 stamped\\ 文件夹里。\n"
            "示例：D:\\输出\\打戳后")
        self.ed_suffix.setToolTip(
            "输出文件名后缀。默认 _stamped：video.mp4 → video_stamped.mp4")
        self.chk_overwrite.setToolTip(
            "勾选后同名输出文件会被覆盖重打；\n"
            "不勾选时已存在的输出自动跳过（重复处理同一批文件更安全）。")
        self.cmb_hw.setToolTip(
            "自动（推荐）：优先用显卡硬件编码（NVIDIA NVENC / Intel QSV / AMD AMF），\n"
            "速度快数倍；不可用或失败时自动回退 CPU。\n"
            "CPU (x264/x265)：强制软件编码，兼容性最好。")
        self.lbl_hw.setToolTip("启动时自动探测的结果。显示不可用通常是显卡驱动过旧，更新驱动后重启本程序。")
        self.lbl_preview.setToolTip(
            "点\"预览选中文件样式\"后显示所选视频中间帧的打戳效果。\n"
            "预览显示的是起始时刻的时间文字，仅用于确认样式，不会写输出文件。")
        self.btn_start.setToolTip(
            "按当前参数与时间表处理列表里的全部视频，输出到 stamped\\ 文件夹（或指定目录）。\n"
            "处理信息会显示在下方日志区；结束后可导出 CSV 清单。")
        self.btn_cancel.setToolTip("停止批量任务，当前正在编码的文件会在数秒内中断。")
        self.btn_csv.setToolTip(
            "把本次批量结果（源文件/输出/状态/时间来源/编码器/错误）导出为 CSV，\n"
            "可用 Excel 打开。")

    # -------------------------------------------------------- 拖拽与文件
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls()]
        self._add_paths(paths)

    # ---- 表格基础 -----------------------------------------------------
    def _iter_rows(self) -> list[int]:
        return list(range(self.table.rowCount()))

    def _row_path(self, r: int) -> str:
        return self.table.item(r, 1).data(Qt.ItemDataRole.UserRole)

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectedItems()})

    def _row_paths(self) -> list[str]:
        return [self._row_path(r) for r in self._iter_rows()]

    def _add_paths(self, paths: list[Path]) -> None:
        existing = set(self._row_paths())
        files = collect_inputs(paths)
        added = 0
        new_files: list[str] = []
        for f in files:
            key = str(f)
            if key not in existing:
                r = self.table.rowCount()
                self.table.insertRow(r)
                for c in range(6):
                    self.table.setItem(r, c, QTableWidgetItem())
                self.table.item(r, 1).setData(Qt.ItemDataRole.UserRole, key)
                existing.add(key)
                new_files.append(key)
                added += 1
        if not added:
            return
        self.log_view.appendPlainText(f"已添加 {added} 个视频文件")
        if self.chk_seq.isChecked():
            self.log_view.appendPlainText(
                "连续时间戳已启用：第一个视频使用\"起始时间\"，"
                "后续视频自动接续前一个的结束时间（双击某行可微调）")
        elif added >= 2:
            self.log_view.appendPlainText(
                "提示: 勾选\"按顺序连续排列时间戳\"可让后续视频自动接续前一个的结束时间")
        uncached = [k for k in new_files if k not in self.durations]
        if uncached:
            self._start_probe(uncached)
        self._recompute_times()

    def _start_probe(self, keys: list[str]) -> None:
        self._probing.update(keys)
        self.probe_worker = ProbeWorker(keys)
        self.probe_worker.sig_one.connect(self._on_probe_one)
        self.probe_worker.sig_done.connect(self._on_probe_done)
        self.probe_worker.start()

    def _on_probe_one(self, path: str, dur: float, ok: bool) -> None:
        self._probing.discard(path)
        if ok:
            self.durations[path] = dur
            self._probe_missing.discard(path)
        else:
            self._probe_missing.add(path)
        self._recompute_times()

    def _on_probe_done(self, missing: list) -> None:
        self._recompute_times()
        if not missing:
            return
        names = "\n".join("· " + Path(p).name for p in missing)
        if self.chk_seq.isChecked():
            QMessageBox.warning(
                self, APP_TITLE,
                f"以下 {len(missing)} 个视频无法读取时长（文件损坏或格式异常）：\n"
                f"{names}\n\n"
                "连续时间戳将在这些文件处断开，其后的视频回退使用全局时间源。\n"
                "也可双击这些文件的行，手动指定起始时间。")
        else:
            self.log_view.appendPlainText(
                f"提示: {len(missing)} 个视频无法读取时长:\n{names}")

    def on_add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择视频文件", "",
            "视频文件 (*.mp4 *.mov *.avi *.mkv *.ts *.m2ts *.mts *.flv "
            "*.webm *.wmv *.mpg *.mpeg *.m4v *.3gp *.mxf *.vob)")
        self._add_paths([Path(f) for f in files])

    def on_add_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if d:
            self._add_paths([Path(d)])

    def on_remove_selected(self) -> None:
        for r in reversed(self._selected_rows()):
            self.file_times.pop(self._row_path(r), None)
            self.table.removeRow(r)
        self._recompute_times()

    def on_clear_files(self) -> None:
        self.table.setRowCount(0)
        self.file_times.clear()
        self.effective_starts.clear()
        self.broken.clear()
        self._recompute_times()

    def _move_rows(self, delta: int) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        order = rows if delta < 0 else list(reversed(rows))
        n = self.table.rowCount()
        for r in order:
            nr = r + delta
            if not (0 <= nr < n):
                continue
            for c in range(self.table.columnCount()):
                a = self.table.takeItem(r, c)
                b = self.table.takeItem(nr, c)
                self.table.setItem(nr, c, a)
                self.table.setItem(r, c, b)
        self._recompute_times()

    # -------------------------------------------------------------- 时间
    def _on_seq_toggled(self) -> None:
        self._time_mode_changed()
        self._recompute_times()

    def _time_mode_changed(self) -> None:
        mode = self.cmb_time_mode.currentData()
        seq = self.chk_seq.isChecked()
        self.dt_start.setEnabled(mode == "manual" or seq)
        self.ed_pattern.setEnabled(mode == "regex" and not seq)

    def _recompute_times(self) -> None:
        """按当前顺序/锚点/时长重算每个文件的起始时间并刷新表格。"""
        order = self._row_paths()
        seq_start = self.dt_start.dateTime().toPython()
        starts, broken = compute_chained_starts(
            order, self.durations, self.file_times, seq_start,
            self.chk_seq.isChecked())
        self.effective_starts = starts
        self.broken = broken
        self._refresh_table()

    def _refresh_table(self) -> None:
        total_known = 0.0
        first_start: datetime | None = None
        last_end: datetime | None = None
        for r in self._iter_rows():
            p = self._row_path(r)
            self.table.item(r, 0).setText(str(r + 1))
            it_name = self.table.item(r, 1)
            it_name.setText(Path(p).name)
            it_name.setToolTip(f"完整路径: {p}\n双击本行可微调该文件的起始时间")

            t = self.effective_starts.get(p)
            d = self.durations.get(p)
            end = (t + timedelta(seconds=d)
                   if t is not None and d is not None and d > 0 else None)

            self.table.item(r, 2).setText(_fmt_dt(t))
            self.table.item(r, 3).setText(_fmt_dt(end))
            self.table.item(r, 4).setText(
                _fmt_hms(d) if d is not None and d > 0 else "未知")

            remark: list[str] = []
            if p in self._probe_missing:
                remark.append("⚠ 时长未知")
            elif p in self._probing:
                remark.append("探测中…")
            if p in self.broken:
                remark.append("链条断开→回退全局时间源")
            if p in self.file_times:
                remark.append("手动设定")
            self.table.item(r, 5).setText("；".join(remark))

            if d is not None and d > 0:
                total_known += d
            if t is not None and first_start is None:
                first_start = t
            if end is not None:
                last_end = end

        n = self.table.rowCount()
        txt = f"共 {n} 个视频 · 已知总时长 {_fmt_hms(total_known)}"
        if first_start and last_end:
            span = (f"{first_start:%H:%M:%S} → {last_end:%H:%M:%S}")
            if first_start.date() != last_end.date():
                span += "（跨天）"
            txt += f" · 时间跨度 {span}"
        self.lbl_timeline.setText(txt)

    def _on_cell_double_clicked(self, row: int, _col: int) -> None:
        self.table.selectRow(row)
        self.on_set_file_time()

    def _ask_datetime(self, title: str,
                      initial: QDateTime) -> QDateTime | None:
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        v = QVBoxLayout(dlg)
        dt = QDateTimeEdit(initial)
        dt.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        dt.setCalendarPopup(True)
        v.addWidget(QLabel("该文件的打戳起始时间（连续模式下其后的文件会自动顺延）："))
        v.addWidget(dt)
        hb = QHBoxLayout()
        ok = QPushButton("确定")
        cancel = QPushButton("取消")
        ok.clicked.connect(dlg.accept)
        cancel.clicked.connect(dlg.reject)
        hb.addStretch(1)
        hb.addWidget(ok)
        hb.addWidget(cancel)
        v.addLayout(hb)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dt.dateTime()
        return None

    def on_set_file_time(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(self, APP_TITLE, "请先在列表中选中要设置时间的文件")
            return
        first = self._row_path(rows[0])
        init = QDateTime.fromString(self.file_times.get(first, ""),
                                    "yyyy-MM-dd HH:mm:ss")
        if not init.isValid():
            init = (QDateTime.fromPython(self.effective_starts[first])
                    if first in self.effective_starts
                    else self.dt_start.dateTime())
        dt = self._ask_datetime(
            f"设置起始时间（已选 {len(rows)} 个文件）", init)
        if dt is None:
            return
        s = dt.toString("yyyy-MM-dd HH:mm:ss")
        for r in rows:
            self.file_times[self._row_path(r)] = s
        self.log_view.appendPlainText(
            f"已为 {len(rows)} 个文件设置起始时间 {s}（连续模式下其后的文件自动顺延）")
        self._recompute_times()

    def on_clear_file_time(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        for r in rows:
            self.file_times.pop(self._row_path(r), None)
        self.log_view.appendPlainText("已清除选中文件的手动时间，恢复自动计算/全局时间源")
        self._recompute_times()

    def _selected_or_first(self) -> Path | None:
        rows = self._selected_rows() or ([0] if self.table.rowCount() else [])
        if not rows:
            return None
        return Path(self._row_path(rows[0]))

    # -------------------------------------------------------------- 参数
    def on_pick_color(self) -> None:
        c = QColorDialog.getColor(QColor(self.color_hex), self, "选择文字颜色")
        if c.isValid():
            self.color_hex = c.name()
            self.btn_color.setText(c.name())

    def on_pick_outdir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.ed_outdir.setText(d)

    def _settings_from_ui(self) -> StampSettings:
        outdir = self.ed_outdir.text().strip()
        return StampSettings(
            time_mode=self.cmb_time_mode.currentData(),
            manual_time=self.dt_start.dateTime().toString("yyyy-MM-dd HH:mm:ss"),
            filename_pattern=self.ed_pattern.text(),
            template=self.cmb_template.currentText().strip() or DEFAULT_TEMPLATES[0],
            font_path=Path(self.cmb_font.currentData() or str(FONTS_DIR / "msyh.ttc")),
            font_size=self.sp_size.value(),
            color=self.color_hex.replace("#", "0x"),
            border_w=self.sp_border.value(),
            box_opacity=self.sl_box.value(),
            position=self.cmb_pos.currentText(),
            dx=self.sp_dx.value(),
            dy=self.sp_dy.value(),
            hw=self.cmb_hw.currentData(),
            out_dir=Path(outdir) if outdir else None,
            suffix=self.ed_suffix.text() or "_stamped",
            overwrite=self.chk_overwrite.isChecked(),
        )

    # -------------------------------------------------------------- 预览
    def on_preview(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, APP_TITLE, "正在批量打戳，请等待完成后再预览")
            return
        src = self._selected_or_first()
        if not src:
            QMessageBox.information(self, APP_TITLE, "请先添加并选择一个视频文件")
            return
        self.btn_start.setEnabled(False)
        self.lbl_preview.setText("预览渲染中…")
        self.preview_worker = PreviewWorker(self._settings_from_ui(), src)
        self.preview_worker.sig_ok.connect(self.on_preview_ok)
        self.preview_worker.sig_err.connect(self.on_preview_err)
        self.preview_worker.start()

    def on_preview_ok(self, png: str) -> None:
        self.btn_start.setEnabled(True)
        pix = QPixmap(png)
        self.lbl_preview.setPixmap(pix.scaled(
            self.lbl_preview.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))

    def on_preview_err(self, msg: str) -> None:
        self.btn_start.setEnabled(True)
        QMessageBox.warning(self, APP_TITLE, f"预览失败:\n{msg}")

    # -------------------------------------------------------------- 批量
    def on_start(self) -> None:
        if self.table.rowCount() == 0:
            QMessageBox.information(self, APP_TITLE, "请先添加视频文件")
            return
        inputs = [Path(p) for p in self._row_paths()]
        settings = self._settings_from_ui()
        self._save_settings()
        self._recompute_times()
        if self.chk_seq.isChecked():
            times = {p: _fmt_dt(t) for p, t in self.effective_starts.items()}
            t0 = self.effective_starts.get(str(inputs[0]))
            msg = (f"连续时间戳: 第一个视频从 {_fmt_dt(t0)} 开始，"
                   f"共 {len(times)}/{len(inputs)} 个文件使用指定时间")
            if self.broken:
                msg += (f"；{len(self.broken)} 个文件时长未知回退全局时间源: "
                        + ", ".join(Path(p).name for p in self.broken))
            self.log_view.appendPlainText(msg)
        else:
            times = dict(self.file_times)
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.bar_overall.setValue(0)
        self.log_view.appendPlainText(
            f"开始处理 {len(inputs)} 个文件 | 模板={settings.template} "
            f"| 位置={settings.position} | 编码={settings.hw}")
        self.worker = StampWorker(settings, inputs, times)
        self.worker.sig_log.connect(self.log_view.appendPlainText)
        self.worker.sig_overall.connect(self.bar_overall.setValue)
        self.worker.sig_current.connect(self.lbl_current.setText)
        self.worker.sig_done.connect(self.on_done)
        self.worker.start()

    def on_cancel(self) -> None:
        if self.worker:
            self.worker.stop()
            self.log_view.appendPlainText("正在取消…（当前文件可能需数秒退出）")

    def on_done(self, results: list) -> None:
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.lbl_current.setText("")
        self.last_results = results
        done = sum(1 for r in results if r.status == "done")
        skipped = sum(1 for r in results if r.status == "skipped")
        failed = sum(1 for r in results if r.status == "failed")
        self.log_view.appendPlainText(
            f"汇总: 完成 {done} / 跳过 {skipped} / 失败 {failed}")
        self.btn_csv.setEnabled(bool(results))
        if failed:
            QMessageBox.warning(self, APP_TITLE,
                                f"有 {failed} 个文件失败，详见日志。")

    def on_export_csv(self) -> None:
        if not self.last_results:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出结果清单", "videostamp_report.csv", "CSV (*.csv)")
        if path:
            export_csv(self.last_results, Path(path))
            self.log_view.appendPlainText(f"已导出: {path}")

    # ---------------------------------------------------------- 持久化
    def _settings_path(self) -> Path:
        d = app_config_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d / "settings.ini"

    def _save_settings(self) -> None:
        s = QSettings(str(self._settings_path()), QSettings.Format.IniFormat)
        s.setValue("time_mode", self.cmb_time_mode.currentData())
        s.setValue("template", self.cmb_template.currentText())
        s.setValue("font", self.cmb_font.currentData())
        s.setValue("size", self.sp_size.value())
        s.setValue("color", self.color_hex)
        s.setValue("border", self.sp_border.value())
        s.setValue("box", self.sl_box.value())
        s.setValue("pos", self.cmb_pos.currentText())
        s.setValue("dx", self.sp_dx.value())
        s.setValue("dy", self.sp_dy.value())
        s.setValue("hw", self.cmb_hw.currentData())
        s.setValue("out_dir", self.ed_outdir.text())
        s.setValue("suffix", self.ed_suffix.text())
        s.setValue("overwrite", self.chk_overwrite.isChecked())
        s.setValue("sequential", self.chk_seq.isChecked())
        s.sync()

    def _load_settings(self) -> None:
        s = QSettings(str(self._settings_path()), QSettings.Format.IniFormat)

        def get(key, widget_setter, default="", cast=None):
            # INI 读回的值一律是 str（跨机器/区域行为不一致），必须显式转型
            v = s.value(key, default)
            if v in (None, ""):
                return
            if cast is not None:
                try:
                    v = cast(v)
                except (TypeError, ValueError):
                    return  # 非法值 → 保持控件默认
            widget_setter(v)

        mode = str(s.value("time_mode", "metadata"))
        idx = self.cmb_time_mode.findData(mode)
        if idx >= 0:
            self.cmb_time_mode.setCurrentIndex(idx)
        get("template", lambda v: self.cmb_template.setCurrentText(str(v)),
            DEFAULT_TEMPLATES[0])
        font = str(s.value("font", ""))
        if font:
            i = self.cmb_font.findData(font)
            if i >= 0:
                self.cmb_font.setCurrentIndex(i)
        get("size", self.sp_size.setValue, 28, int)
        color = str(s.value("color", "#ffffff"))
        self.color_hex = color
        self.btn_color.setText(color)
        get("border", self.sp_border.setValue, 2, int)
        get("box", self.sl_box.setValue, 0, int)
        get("pos", self.cmb_pos.setCurrentText, "bottom-left")
        get("dx", self.sp_dx.setValue, 20, int)
        get("dy", self.sp_dy.setValue, 20, int)
        hw = str(s.value("hw", "auto"))
        i = self.cmb_hw.findData(hw)
        if i >= 0:
            self.cmb_hw.setCurrentIndex(i)
        get("out_dir", self.ed_outdir.setText, "")
        get("suffix", self.ed_suffix.setText, "_stamped")
        self.chk_overwrite.setChecked(
            str(s.value("overwrite", "false")).lower() == "true")
        self.chk_seq.setChecked(
            str(s.value("sequential", "true")).lower() == "true")
        self._time_mode_changed()
        self._recompute_times()

    # -------------------------------------------------------------- 关闭
    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker and self.worker.isRunning():
            r = QMessageBox.question(
                self, APP_TITLE, "任务仍在运行，确定退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.worker.stop()
            self.worker.wait(8000)
        for w in (self.probe_worker, self.preview_worker):
            if w and w.isRunning():
                w.wait(3000)
        self._save_settings()
        event.accept()


def gui_main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()
