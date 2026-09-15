"""生成 drawtext 滤镜（filter_complex_script 文件内容）。

核心时间映射：
    wall = start_epoch + frame_pts - video_start
    text = %{pts:gmtime:<start_epoch>:<strftime 格式>}

gmtime 按给定 epoch 直接格式化（不做时区换算），因此 epoch 用
"起始本地时间对应的 UTC epoch" 即可让画面显示原本地时间。
strftime 格式中的 ':' 必须转义为 '\:'（它是 pts 函数的参数分隔符）。
"""
from dataclasses import dataclass
from pathlib import Path

POSITIONS = {
    "top-left":     "x={dx}:y={dy}",
    "top-center":   "x=(w-tw)/2+{dx}:y={dy}",
    "top-right":    "x=w-tw-{dx}:y={dy}",
    "middle-left":  "x={dx}:y=(h-th)/2+{dy}",
    "center":       "x=(w-tw)/2+{dx}:y=(h-th)/2+{dy}",
    "middle-right": "x=w-tw-{dx}:y=(h-th)/2+{dy}",
    "bottom-left":  "x={dx}:y=h-th-{dy}",
    "bottom-center": "x=(w-tw)/2+{dx}:y=h-th-{dy}",
    "bottom-right": "x=w-tw-{dx}:y=h-th-{dy}",
}

DEFAULT_TEMPLATES = [
    "%Y-%m-%d %H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d",
    "%H:%M:%S",
    "%m-%d %H:%M",
]


@dataclass
class StyleConfig:
    start_epoch: int
    template: str = "%Y-%m-%d %H:%M:%S"
    font_path: Path = Path("C:/Windows/Fonts/msyh.ttc")
    font_size: int = 28
    color: str = "white"
    border_w: int = 2
    border_color: str = "black"
    box_opacity: int = 0        # 0-100，0 表示无底框
    position: str = "bottom-left"
    dx: int = 20
    dy: int = 20


def strftime_to_drawtext(fmt: str) -> str:
    """用户 strftime 模板 → drawtext pts 函数的格式参数。

    FFmpeg 9 的 %{pts} 展开器按原始 ':' 切分参数（不支持 \: 转义），
    因此格式串中不能出现冒号。利用 strftime 复合符规避：
        %H:%M:%S → %T    %H:%M → %R
    转换后仍含 ':' 的模板视为无效，抛出 ValueError。
    """
    f = fmt.replace("%H:%M:%S", "%T").replace("%H:%M", "%R")
    if ":" in f:
        raise ValueError(
            f"模板 {fmt!r} 含不支持的字面冒号；"
            "请使用 %T(时:分:秒) / %R(时:分) 或调整写法"
        )
    return f


def validate_template(fmt: str) -> bool:
    """校验模板可被 strftime 接受且渲染非空（至少含 %Y/%m/%d/%H/%M/%S 之一）。"""
    import time
    try:
        out = time.strftime(fmt)
    except (ValueError, TypeError):
        return False
    if not out:
        return False
    return any(c in fmt for c in ("Y", "m", "d", "H", "M", "S"))


def _q(value: str) -> str:
    """把值包进 ' 引号并按 graph 层规则转义引号（\'），使引号存活到选项层。"""
    return "\\'" + value + "\\'"


def fontfile_expr(font_path: Path) -> str:
    # 引号存活到选项层后，路径中的 ':' 在引号内是安全的
    p = str(font_path).replace("\\", "/")
    return "fontfile=" + _q(p)


def build_drawtext(cfg: StyleConfig, extra_args: dict | None = None) -> str:
    if cfg.position not in POSITIONS:
        raise ValueError(f"未知位置: {cfg.position}")
    fmt = strftime_to_drawtext(cfg.template)
    text = "%{{pts:gmtime:{}:{}}}".format(cfg.start_epoch, fmt)

    parts = [
        fontfile_expr(cfg.font_path),
        "text=" + _q(text),
        f"fontsize={cfg.font_size}",
        f"fontcolor={cfg.color}",
        f"borderw={cfg.border_w}",
        f"bordercolor={cfg.border_color}",
    ]
    if cfg.box_opacity > 0:
        parts += ["box=1", f"boxcolor=black@{cfg.box_opacity / 100:.2f}", "boxborderw=10"]
    parts.append(POSITIONS[cfg.position].format(dx=cfg.dx, dy=cfg.dy))
    if extra_args:
        parts += [f"{k}={v}" for k, v in extra_args.items()]
    return "drawtext=" + ":".join(parts)


def build_graph(cfg: StyleConfig, video_start: float = 0.0) -> str:
    """生成完整 filtergraph 字符串（经 subprocess 直接传给 -filter_complex）。

    video_start：视频流起始时间（秒）。流从非 0 开始时（如 TS），
    需从 epoch 中扣除以对齐墙钟。
    """
    epoch = cfg.start_epoch
    if video_start and 1.0 <= video_start <= 3600:
        epoch -= int(video_start)
    sc = StyleConfig(**{**cfg.__dict__, "start_epoch": epoch})
    return "[0:v]" + build_drawtext(sc) + "[v]"
