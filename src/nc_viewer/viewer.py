# -*- coding: utf-8 -*-
"""NC 代码刀路可视化查看器 (主窗口)

特性:
  - 解析 NC/G 代码(.MPF/.NC/.CNC/.TXT), 按模态还原刀路
  - 不同的 F 进给用不同颜色绘制, G0 快移用灰色, 颜色图例同步显示
  - 圆弧(G2/G3)按弧段离散绘制
  - XY/XZ/YZ 三视图切换, 鼠标拖动平移、滚轮缩放、一键适配
  - 输入行号(或 N6 形式)定位刀具实际位置: 画布上标出当前位置十字与该段高亮
  - 代码列表按 F 颜色着色, 点击行即定位
  - 左侧文件栏支持一次加载多个文件, 随时切换

依赖: 仅 Python 标准库(Tkinter)
"""
from __future__ import annotations

import bisect
import math
import os
import re
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from . import theme
from .geometry import (CUR_COLOR, CUR_LINE_COLOR, G0_COLOR, SEG_COLOR,
                       VIEW_QUAT, build_palette, color_of_move,
                       compensate_center, move_points_3d, orbit_rotate,
                       point_seg_dist_sq, project, quat_from_axis_angle,
                       quat_mul, quat_normalize, quat_rotate)
from .parser import (compute_lift_plane, compute_machining_time,
                     compute_segments, compute_stats, move_time_sec, parse_nc)
from .tool import (TOOL_SPECS, Tool, parse_aptsource_tool, tool_full_profile,
                   tool_overall_height, tool_profile_points, tool_summary)


_UNSET = object()              # 哨兵: _fill_stats 时间统计未显式指定


def _sample_dir():
    """定位样例文件目录（仅本地开发/测试用，不打包进 EXE）。

    开发环境: src/nc_viewer/viewer.py -> 项目根/样例文件/数控程序
    打包环境: 样例未内置, 回退到用户主目录作为文件对话框初始目录
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    d = os.path.join(root, "样例文件", "数控程序")
    if os.path.isdir(d):
        return d
    return os.path.expanduser("~")


# 刀具 3D 模型固定光源 (世界空间, 右上偏上, 归一化)
_TOOL_LIGHT = (0.4 / math.hypot(0.4, 0.3, 0.8),
               0.3 / math.hypot(0.4, 0.3, 0.8),
               0.8 / math.hypot(0.4, 0.3, 0.8))


def _icon_path():
    """定位窗口图标文件: 打包后为资源目录 (_MEIPASS), 开发时为项目 assets"""
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "NCodeViewer_icon.ico")
    root = os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, "assets", "NCodeViewer_icon.ico")


def _set_icon(win):
    """设置窗口标题栏图标 (主窗口/各级二级窗口, 全局生效); 成功返回 True。

    注: Windows 上 Tk 以图标句柄存储, iconbitmap() 查询返回空, 以
    设置是否抛异常判定成功 (资源缺失时 TclError)。
    """
    try:
        win.iconbitmap(_icon_path())
        return True
    except tk.TclError:
        return False


def _compute_lead_skip(moves):
    """前导跳过数: 连续从原点(0,0,0)出发的起始进给段数量。

    程序起点应为"第一个可解析点"而非机器原点——从原点出发的
    对刀/接近段属于噪声, 显示时跳过。若全部段都从原点出发则
    不跳过 (保留最后一段, 避免画面全空)。
    """
    n = 0
    for m in moves:
        if n >= len(moves) - 1:
            break
        if m.start == (0.0, 0.0, 0.0):
            n += 1
        else:
            break
    return n


def _make_scroll_col(parent):
    """双轴滚动列容器: 返回 (box, canvas, inner)。

    默认宽度随内容 (无需横向滚动); 被挤窄时横向滚动条出现,
    内容高度超出时竖向滚动条生效。
    """
    box = ttk.Frame(parent, style="Panel.TFrame")
    box.columnconfigure(0, weight=1)
    box.rowconfigure(0, weight=1)
    canvas = tk.Canvas(box, bg=theme.PANEL, highlightthickness=0)
    vsb = ttk.Scrollbar(box, orient="vertical", command=canvas.yview)
    hsb = ttk.Scrollbar(box, orient="horizontal", command=canvas.xview)
    canvas.config(xscrollcommand=hsb.set, yscrollcommand=vsb.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")
    inner = ttk.Frame(canvas, style="Panel.TFrame")
    win = canvas.create_window((0, 0), window=inner, anchor="nw")
    inner.bind("<Configure>", lambda e, c=canvas: c.configure(
        scrollregion=c.bbox("all")))                   # 画布窄于内容时横滚
    canvas.bind("<Configure>", lambda e, c=canvas, i=inner, w=win: c.itemconfigure(
        w, width=max(e.width, i.winfo_reqwidth()),
        height=max(e.height, i.winfo_reqheight())))    # 内容/画布宽高较大者为准
    return box, canvas, inner


def _class_first_bindtags(w):
    """让 ttk 类绑定先于实例绑定执行。

    PanedWindow 的 sash 拖动由类绑定处理; 默认实例绑定先跑, 此刻 sash
    尚未移动, 钳制值随即被类绑定覆盖 (最小宽度失效)。交换 bindtags 前
    两项后, sash 先被 ttk 移动, 实例钳制最后生效。
    """
    tags = list(w.bindtags())
    if len(tags) >= 2:
        w.bindtags((tags[1], tags[0]) + tuple(tags[2:]))


def _mpf_program_name(text):
    """MPF 头部程序名: 头部文本中括号内的标识符 (部分系统格式)"""
    m = re.search(r"\(([A-Za-z0-9_\-]{2,})\)", text[:2000])
    return m.group(1) if m else None


def _apt_program_name(text):
    """apt 程序名: PPRINT PROGNAME 行, 或首个 $$ 注释行"""
    m = re.search(r"PPRINT\s+PROGNAME\s+(\S+)", text, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"^\$\$\s*(\S+)", text, re.MULTILINE)
    return m.group(1) if m else None


def _enable_dpi_awareness():
    """开启进程 DPI 感知, 修复高分屏缩放下 Tk 文字模糊。

    Tk 8.6.9 自身非 DPI 感知, Windows 会整体位图放大导致文字发虚;
    开启后 Tk 按真实 DPI 渲染, 文字清晰。Win8.1+ 优先逐显示器感知,
    Win7 回退系统级感知。失败静默 (无显示环境/CI)。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_DPI_AWARE
            return
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()        # Win7: 系统 DPI 感知
    except Exception:
        pass


# ---------- 主窗口 ----------
class NCViewer(tk.Tk):
    def __init__(self):
        _enable_dpi_awareness()
        super().__init__()
        _set_icon(self)                    # 窗口标题栏图标 (各级窗口生效)
        theme.apply_theme(self)
        self.configure(bg=theme.BG)
        self.title("NC 刀路查看器")
        self.geometry("1280x820")
        # 默认最大化打开 (Win7 支持 state zoomed)
        self.state("zoomed")
        # 布局可缩放下限: 低于最低支持分辨率时画布塌缩, 故设 1280x700 保底
        self.minsize(1280, 700)
        # 窗口映射后抬高底部大栏 (稳定期由 Configure 绑定持续保持)

        self.result = None
        self.lines = []
        self.palette = {}
        self.move_by_line = {}

        self.quat = (1.0, 0.0, 0.0, 0.0)   # 朝向四元数 (ArcBall 表现)
        self.show_g0 = tk.BooleanVar(value=True)
        self.scale = 1.0
        self.offset = (0.0, 0.0)
        self.current_line = None
        self._disp3d = None            # 预计算的刀路 3D 离散点缓存

        # 多文件管理: path -> 解析/缓存数据; mp_paths/apt_paths 为双区显示顺序
        self.file_items = {}
        self.mp_paths = []
        self.apt_paths = []

        # 搜索状态
        self._search_pattern = None
        self._search_hits = []
        self._search_idx = -1
        self._code_line_h = None         # 代码行高缓存 (播放居中用)

        self._current_path = None        # 当前文件的完整路径 (供二级窗口标题)

        # 逐行运行状态: None / "play" / "demo"
        self._play_mode = None
        self._play_job = None
        self._target_line = None         # 点击代码行选中的目标行
        # 轨迹渐进显示: 播放/演示时画布从空白起逐行绘制刀路
        self._trace_active = False
        self._trace_drawn = 0            # 已绘制的移动数
        self._trace_items = []           # [(color, item_id, coords_flat)]
        self._move_lines = []            # 每条的 line_number, 供 bisect 定位
        self._lead_skip = 0              # 前导跳过数 (从原点出发的起始进给段)

        # 刀具: 解析自 aptsource, 自定义优先; show_tool 控制 3D 模型显示
        self.tool = None
        self.custom_tool = None
        self._parsed_tool = None
        self.show_tool = tk.BooleanVar(value=True)

        # 分段: 抬刀平面(可用户覆盖) + 段列表 + 段模式
        self._segments = []
        self._seg_of_line = {}           # 行号 -> 段号 (代码区行号槽标注)
        self._lift_plane = 0.0
        self._lift_auto = True
        self._seg_index = None
        self._seg_filter = None            # [(start_idx, end_idx), ...] | [] | None
        self._seg_only = tk.BooleanVar(value=False)
        self._seg_checked = set()          # 勾选段索引集合 (唯一真源)

        # 投影缓存: 四元数/过滤条件不变时缩放只做坐标变换 (提速缩放)
        self._proj_cache = None
        self._bbox_cache = None          # (key, bbox) 适配用
        # 渲染图元复用: 结构(折线颜色/点数序列)不变时仅 coords 更新, 免 delete/create
        self._path_items = []
        self._poly_struct = None
        # 渲染预计算元数据 (visible/colors/merge_prev, 见 _build_render_meta)
        self._render_meta = None
        self._refresh_job = None         # 旋转/滚转刷新合并任务
        self._rot_moved = False
        # 文件加载进度: 超过延时的加载才在顶部栏显示 (快任务防闪烁)
        self._prog = None                # {"lbl","bar"} | None
        self._prog_t0 = None
        self._progress_delay = 0.08
        # 播放进度 (按加工时间): 可见移动时间前缀和 + 移动索引映射
        self._time_prefix = None
        self._time_pos = {}

        self._build_ui()
        self._bind_canvas()

    # ------------- UI -------------
    def _build_ui(self):
        # 顶部工具条 (分隔线先 pack, 占满整行; 后 pack 的按钮在其上方排列)
        top = ttk.Frame(self, padding=6)
        top.pack(side="top", fill="x")
        ttk.Separator(top, orient="horizontal").pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Button(top, text="打开文件…", style=theme.BTN_ACCENT,
                   command=self.open_file_multi).pack(side="left")
        ttk.Button(top, text="适配", command=self.fit_view).pack(side="left", padx=(8, 0))
        ttk.Button(top, text="放大", command=lambda: self.zoom_at(1.25, None)).pack(side="left")
        ttk.Button(top, text="缩小", command=lambda: self.zoom_at(1 / 1.25, None)).pack(side="left")
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(top, text="主视图:").pack(side="left")
        for v in ("XY", "XZ", "YZ"):
            ttk.Button(top, text=v, width=3,
                       command=lambda vv=v: self.set_view_preset(vv)).pack(side="left", padx=1)
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=8)
        # 开关用 _view_refresh: 播放/演示中切换不退出轨迹模式
        ttk.Checkbutton(top, text="显示G0快移", variable=self.show_g0,
                        command=self._on_show_g0_toggle).pack(side="left")
        self.tool_chk = ttk.Checkbutton(top, text="显示刀具", variable=self.show_tool,
                                        command=self._view_refresh)
        self.tool_chk.pack(side="left", padx=(8, 0))
        self.file_lbl = ttk.Label(top, text="(未打开文件)")
        self.file_lbl.pack(side="left", padx=(12, 0))
        # 加载进度内嵌 (顶部栏右上角): 超延时显示, 结束隐藏; 不弹独立窗口
        self._prog_lbl = ttk.Label(top, text="", style="Panel.TLabel")
        self._prog_bar = ttk.Progressbar(top, mode="determinate",
                                         length=220, maximum=100)

        # 主体: 上=画布+侧栏, 下=代码列表
        body = ttk.PanedWindow(self, orient="vertical")
        body.pack(side="top", fill="both", expand=True, padx=6, pady=4)
        self.body_pane = body
        # 启动稳定期(3 秒)内 body 尺寸事件会重置 sash, 期间持续重设默认位置
        self._sash_until = time.time() + 3.0
        body.bind("<Configure>", lambda e: self._maybe_apply_default_sash())
        # 底部大栏最小 25%: 拖动与窗口缩放时均钳制 (画布优先被压缩)
        # 窗口级 Configure 在 body 布局定型后触发, 避免中间态覆盖
        self.bind("<Configure>", self._clamp_bottom_sash)
        body.bind("<B1-Motion>", self._clamp_bottom_sash)
        _class_first_bindtags(body)     # sash 先移动, 钳制后生效

        upper = ttk.PanedWindow(body, orient="horizontal")
        body.add(upper, weight=3)
        self.upper_pane = upper
        # 底部大栏默认抬高: 权重 1 -> 2 (约占 1/3 高度)

        # 左侧文件栏: 一次加载多个文件, 随时切换 (面板样式, 与画布区分)
        fs_frame = ttk.Frame(upper, width=220, padding=6, style="Panel.TFrame")
        upper.add(fs_frame, weight=0)
        fs_frame.columnconfigure(0, weight=1)
        ttk.Label(fs_frame, text="文件列表", font=("", 10, "bold"),
                  style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        # 上区: 数控程序 (MPF)
        ttk.Label(fs_frame, text="数控程序 (MPF)", font=("", 9, "bold"),
                  style="Panel.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.file_listbox = tk.Listbox(fs_frame, width=26, exportselection=False,
                                       activestyle="dotbox",
                                       selectmode="extended", relief="flat", highlightthickness=1,
                                       bg=theme.PANEL, fg=theme.TEXT,
                                       selectbackground=theme.SELECTION,
                                       selectforeground="#ffffff",
                                       highlightbackground=theme.BORDER_LIGHT,
                                       highlightcolor=theme.ACCENT)
        self.file_listbox.grid(row=2, column=0, sticky="nsew")
        fsb = ttk.Scrollbar(fs_frame, orient="vertical", command=self.file_listbox.yview)
        self._fs_vsb = fsb        # 供文件栏最小宽度动态测量
        fsb.grid(row=2, column=1, sticky="ns")
        self.file_listbox.config(yscrollcommand=fsb.set)
        self.file_listbox.bind("<<ListboxSelect>>", self._on_file_select)
        # 右键菜单: 删除所选(多选) / 配对 APT 刀具 (二级菜单列出全部已加载 APT)
        self.file_menu = tk.Menu(self, tearoff=0)
        self.file_menu.add_command(label="删除所选文件", command=self._menu_delete_mpf)
        self.pair_menu = tk.Menu(self.file_menu, tearoff=0)
        self.file_menu.add_cascade(label="配对 APT 刀具", menu=self.pair_menu)
        self.file_listbox.bind("<Button-3>", self._popup_file_menu)
        # 下区: APT 源文件 (仅刀具信息)
        ttk.Label(fs_frame, text="APT 源文件 (刀具)", font=("", 9, "bold"),
                  style="Panel.TLabel").grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.apt_listbox = tk.Listbox(fs_frame, width=26, exportselection=False,
                                      activestyle="dotbox",
                                      selectmode="extended", relief="flat", highlightthickness=1,
                                      bg=theme.PANEL, fg=theme.TEXT,
                                      selectbackground=theme.SELECTION,
                                      selectforeground="#ffffff",
                                      highlightbackground=theme.BORDER_LIGHT,
                                      highlightcolor=theme.ACCENT, height=5)
        self.apt_listbox.grid(row=4, column=0, sticky="nsew")
        asb = ttk.Scrollbar(fs_frame, orient="vertical", command=self.apt_listbox.yview)
        asb.grid(row=4, column=1, sticky="ns")
        self.apt_listbox.config(yscrollcommand=asb.set)
        self.apt_listbox.bind("<<ListboxSelect>>", self._on_apt_select)
        # 右键菜单: 删除所选 (APT 仅刀具信息, 无配对入口)
        self.apt_menu = tk.Menu(self, tearoff=0)
        self.apt_menu.add_command(label="删除所选文件", command=self._menu_delete_apt)
        self.apt_listbox.bind("<Button-3>", self._popup_apt_menu)
        fs_frame.rowconfigure(2, weight=3)
        fs_frame.rowconfigure(4, weight=1)
        # 横向滚动条: 两个列表共享, 长文件名可横向查看
        fxsb = ttk.Scrollbar(fs_frame, orient="horizontal")
        self.file_listbox.config(xscrollcommand=fxsb.set)
        self.apt_listbox.config(xscrollcommand=fxsb.set)
        fxsb.config(command=lambda *a: (self.file_listbox.xview(*a),
                                        self.apt_listbox.xview(*a)))
        fxsb.grid(row=5, column=0, columnspan=2, sticky="ew")

        # 画布
        cv_frame = ttk.Frame(upper)
        upper.add(cv_frame, weight=3)
        self.canvas = tk.Canvas(cv_frame, bg=theme.CANVAS_BG, highlightthickness=0)
        self.canvas.pack(side="top", fill="both", expand=True)
        # 颜色图例: 漂浮在画布右上角 (窗口项, 随画布尺寸锚定, 不随视图平移)
        self.legend = tk.Frame(self.canvas, bg=theme.PANEL,
                               highlightthickness=1,
                               highlightbackground=theme.BORDER_LIGHT)

        # 播放控制条 (逐行运行): 连续播放 / 单步 / 直达 / 演示
        ctl = ttk.Frame(cv_frame, padding=(4, 4), style="Panel.TFrame")
        ctl.pack(side="bottom", fill="x")
        ttk.Button(ctl, text="复位", command=self._reset_line).pack(side="left")
        ttk.Button(ctl, text="绘制到结尾",
                   command=self._draw_all).pack(side="left", padx=(6, 0))
        ttk.Button(ctl, text="◀ 上一步",
                   command=lambda: self._step_line_ctl(-1)).pack(side="left", padx=(6, 0))
        self.play_btn = ttk.Button(ctl, text="▶ 播放", style=theme.BTN_ACCENT,
                                   command=self._play_toggle)
        self.play_btn.pack(side="left", padx=(6, 0))
        ttk.Button(ctl, text="下一步 ▶",
                   command=lambda: self._step_line_ctl(1)).pack(side="left", padx=(6, 0))
        ttk.Separator(ctl, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Label(ctl, text="速度:", style="Panel.TLabel").pack(side="left")
        self.speed_cb = ttk.Combobox(ctl, values=("慢", "中", "快"), width=4, state="readonly")
        self.speed_cb.set("中")
        self.speed_cb.pack(side="left", padx=(2, 0))
        ttk.Label(ctl, text="合并:", style="Panel.TLabel").pack(side="left", padx=(8, 0))
        # 合并跳行: 播放一次推进 N 行 (1-10 + 整十快捷项, 可手输任意 1-100)
        self.batch_cb = ttk.Combobox(ctl, width=4, state="normal",
                                     values=("1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
                                             "20", "30", "40", "50", "60", "70", "80", "90", "100"))
        self.batch_cb.set("1")
        self.batch_cb.pack(side="left", padx=(2, 0))
        ttk.Separator(ctl, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(ctl, text="直达行",
                   command=lambda: self._run_to_target(False)).pack(side="left")
        ttk.Button(ctl, text="演示到行",
                   command=lambda: self._run_to_target(True)).pack(side="left", padx=(6, 0))
        self.target_lbl = ttk.Label(ctl, text="目标行: -", style="Panel.TLabel")
        self.target_lbl.pack(side="left", padx=(10, 0))
        self.target_clear_btn = ttk.Button(ctl, text="清空", state="disabled",
                                           command=self._clear_target)
        self.target_clear_btn.pack(side="left", padx=(6, 0))

        self.status = ttk.Label(cv_frame, text="", anchor="w", padding=(4, 2))
        self.status.pack(side="bottom", fill="x")

        # 侧栏: 滚动容器 (1366x768 小屏自动滚动; 内容不足时刀具栏拉伸贴底)
        side = ttk.Frame(upper, width=260, padding=8, style="Panel.TFrame")
        upper.add(side, weight=0)
        side.columnconfigure(0, weight=1)
        side.rowconfigure(0, weight=1)
        self.side_canvas = tk.Canvas(side, bg=theme.PANEL, highlightthickness=0)
        self.side_canvas.grid(row=0, column=0, sticky="nsew")
        self.side_scroll = ttk.Scrollbar(side, orient="vertical",
                                         command=self.side_canvas.yview)
        self.side_scroll.grid(row=0, column=1, sticky="ns")
        self.side_hsb = ttk.Scrollbar(side, orient="horizontal",
                                      command=self.side_canvas.xview)
        self.side_hsb.grid(row=1, column=0, sticky="ew")
        self.side_canvas.config(yscrollcommand=self.side_scroll.set,
                                xscrollcommand=self.side_hsb.set)
        inner = ttk.Frame(self.side_canvas, style="Panel.TFrame")
        self._side_inner = inner
        self._side_window = self.side_canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.columnconfigure(0, weight=1)
        inner.bind("<Configure>", lambda e: self.side_canvas.configure(
            scrollregion=inner.bbox("all")))   # 覆盖实际渲染范围
        def _fit_side_inner(ev):
            # 宽 = max(视口, 内容): 拉宽时内容吸附填满, 挤窄时横滚
            # 高 = max(视口, 内容): 超出时纵滚, 未超出时贴底拉伸
            self.side_canvas.itemconfigure(
                self._side_window,
                width=max(ev.width, inner.winfo_reqwidth()),
                height=max(ev.height, inner.winfo_reqheight()))
        self.side_canvas.bind("<Configure>", _fit_side_inner)
        # 滚轮: 全局捕获 + 指针位置判断 (子控件上滚动也能带动侧栏)
        self.bind_all("<MouseWheel>", self._on_side_wheel_global)

        # 程序统计 (单列, 值全宽显示; 固定自然高度)
        st = ttk.LabelFrame(inner, text="程序统计", padding=8, style="Panel.TLabelframe")
        st.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        st.columnconfigure(1, weight=1)
        self.stats_labels = {}
        for r, (key, text) in enumerate((("x", "行程 X"), ("y", "行程 Y"),
                                         ("z", "行程 Z"), ("s", "S 转速"),
                                         ("f", "F 进给"), ("g", "G 次数"),
                                         ("tool", "刀具"), ("time", "加工时间"))):
            ttk.Label(st, text=text, style="Panel.TLabel",
                      font=theme.FONT_SMALL).grid(row=r, column=0, sticky="w")
            lbl = ttk.Label(st, text="-", font=theme.FONT_MONO, style="Panel.TLabel")
            lbl.grid(row=r, column=1, sticky="e")
            self.stats_labels[key] = lbl
        btns = ttk.Frame(st, style="Panel.TFrame")
        btns.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        ttk.Button(btns, text="详情…", style=theme.BTN_ACCENT,
                   command=self.show_details).pack(side="left")
        ttk.Button(btns, text="F 曲线", command=self.show_f_curve).pack(side="left", padx=(8, 0))

        # 当前位置: 上下表格形式 (标签行在上, 值行在下)
        #   XYZ(11字符) / SFG(5字符) 同列对齐, 行段平分整行, 本行单行
        posf = ttk.LabelFrame(inner, text="当前位置", padding=8, style="Panel.TLabelframe")
        posf.grid(row=1, column=0, sticky="ew", pady=(0, 2))
        posf.columnconfigure(0, weight=1)

        def _mk_entry(parent, width):
            return tk.Entry(parent, width=width, state="readonly",
                            readonlybackground=theme.INPUT_BG, fg=theme.TEXT,
                            font=theme.FONT_MONO, justify="left",
                            relief="solid", bd=1, highlightthickness=1,
                            highlightbackground=theme.BORDER_LIGHT,
                            highlightcolor=theme.ACCENT)

        self.pos_fields = {}

        def _mk_table(parent, keys, widths, padx=(0, 6)):
            """上下表格组: 第0行标签, 第1行值框; 值框列 weight 等分"""
            for c, k in enumerate(keys):
                col = c * 2 + 1
                parent.columnconfigure(col, weight=1)
                ttk.Label(parent, text=k, style="Panel.TLabel",
                          font=("", 9, "bold")).grid(row=0, column=col - 1,
                                                     columnspan=2, sticky="w")
                ent = _mk_entry(parent, widths[c])
                ent.grid(row=1, column=col, sticky="ew",
                         padx=padx if c < len(keys) - 1 else (0, 0))
                self.pos_fields[k] = ent

        # XYZ: 3 列各 11 字符
        xyz = ttk.Frame(posf, style="Panel.TFrame")
        xyz.grid(row=0, column=0, sticky="ew")
        _mk_table(xyz, ("X", "Y", "Z"), (11, 11, 11))
        # SFG: 3 列各 5 字符, 与 XYZ 同列宽对齐
        sfg = ttk.Frame(posf, style="Panel.TFrame")
        sfg.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        _mk_table(sfg, ("S", "F", "G"), (5, 5, 5))
        # 行段: 2 列平分整行
        hd = ttk.Frame(posf, style="Panel.TFrame")
        hd.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        _mk_table(hd, ("行", "段"), (8, 8))
        # 本行: 标签行 + 值行 (与其他字段一致的上下表格, 值框整行跨满)
        bl = ttk.Frame(posf, style="Panel.TFrame")
        bl.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        bl.columnconfigure(0, weight=1)
        ttk.Label(bl, text="本行", style="Panel.TLabel",
                  font=("", 9, "bold")).grid(row=0, column=0, sticky="w")
        self.pos_fields["本行"] = _mk_entry(bl, 30)
        self.pos_fields["本行"].grid(row=1, column=0, sticky="ew")

        # 刀具栏: 固定自然高度之外的剩余空间全部给刀具 (最小 240)
        tbar = ttk.LabelFrame(inner, text="刀具", padding=8, style="Panel.TLabelframe")
        tbar.grid(row=2, column=0, sticky="nsew")
        inner.rowconfigure(2, weight=1, minsize=240)
        tbar.columnconfigure(1, weight=1)
        tbar.rowconfigure(2, weight=1)
        ttk.Label(tbar, text="刀具:", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.tool_lbl = ttk.Label(tbar, text="-", style="Panel.TLabel")
        self.tool_lbl.grid(row=0, column=1, sticky="w", padx=(4, 0))
        self.tool_btn = ttk.Button(tbar, text="放大", style=theme.BTN_ACCENT,
                                   command=self.show_tool_profile)
        self.tool_btn.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(tbar, text="自定义…", command=self.show_tool_setup).grid(
            row=1, column=1, sticky="w", pady=(4, 0))
        # 剖面图直接内嵌在刀具信息下方 (随刀具栏均分高度伸缩)
        # 显式小请求宽: Canvas 默认宽 10cm(≈567px@144DPI) 会撑爆侧栏最小宽度
        self.tool_cv = tk.Canvas(tbar, bg=theme.PANEL, height=140, width=120,
                                 highlightthickness=0)
        self.tool_cv.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(6, 0))
        self.tool_cv.bind("<Configure>", lambda e: self._draw_tool_profile_inline())

        # 代码列表 + 右侧定位/搜索面板 (与上方侧栏同列)
        code_split = ttk.Panedwindow(body, orient="horizontal")
        body.add(code_split, weight=2)
        self.code_split = code_split
        upper.bind("<B1-Motion>", self._clamp_pane_mins)
        upper.bind("<Configure>", self._clamp_pane_mins, add="+")
        code_split.bind("<B1-Motion>", self._clamp_pane_mins)
        code_split.bind("<Configure>", self._clamp_pane_mins, add="+")
        _class_first_bindtags(upper)        # sash 先移动, 钳制后生效
        _class_first_bindtags(code_split)
        code_frame = ttk.Frame(code_split)
        code_split.add(code_frame, weight=3)
        ttk.Label(code_frame, text="NC 代码 (点击行选中目标行)", padding=(4, 2)).pack(side="top", fill="x")
        cvsb = ttk.Frame(code_frame)
        cvsb.pack(side="top", fill="both", expand=True)
        xsb = ttk.Scrollbar(cvsb, orient="horizontal")
        ysb = ttk.Scrollbar(cvsb, orient="vertical")
        self.code = tk.Text(cvsb, wrap="none", font=theme.FONT_MONO, height=12,
                            xscrollcommand=xsb.set, yscrollcommand=ysb.set,
                            undo=False, cursor="arrow",
                            bg=theme.EDITOR_BG, fg=theme.TEXT,
                            insertbackground=theme.TEXT,
                            highlightthickness=1,
                            highlightbackground=theme.BORDER_LIGHT,
                            highlightcolor=theme.ACCENT)
        xsb.config(command=self.code.xview)
        ysb.config(command=self.code.yview)
        # grid 布局: 横向滚动条铺满代码区宽度 (pack 会被挤到左下角)
        cvsb.rowconfigure(0, weight=1)
        cvsb.columnconfigure(0, weight=1)
        self.code.grid(row=0, column=0, sticky="nsew")
        ysb.grid(row=0, column=1, sticky="ns")
        xsb.grid(row=1, column=0, sticky="ew")
        self.code.configure(state="disabled")
        self.code.bind("<Button-1>", self._on_code_click, add="+")
        self.code.tag_configure("cur", background="#264f78", foreground="#ffffff")
        self.code.tag_configure("ln", foreground=theme.TEXT_DIM, selectbackground=theme.BG)
        self.code.tag_configure("segno", foreground="#4ec9b0")
        self.code.tag_configure("search", background="#3a5a3a")
        self.code.tag_configure("searchcur", background="#7a9a3a", foreground="#ffffff")
        self.code.tag_configure("target", background="#3d3d5c", foreground="#ffffff")
        self.code.tag_configure("viewline", background="#574d1f")   # 查看行 (拾取查看)
        self.code.tag_raise("search")
        self.code.tag_raise("searchcur")

        # 右侧面板双列: 左 = 按行定位+搜索定位, 右 = 段控制 (各为双轴滚动容器)
        right = ttk.Frame(code_split, width=460, padding=6, style="Panel.TFrame")
        code_split.add(right, weight=0)
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(0, weight=1)
        lbox, lcv, lin = _make_scroll_col(right)
        lbox.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        lin.columnconfigure(0, weight=1)
        rbox, rcv, rin = _make_scroll_col(right)
        rbox.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        self._rc_canvases = (lcv, rcv)      # 供底部右栏宽度测量/测试
        self._rc_inners = (lin, rin)
        rin.columnconfigure(0, weight=1)
        rin.rowconfigure(0, weight=1)        # 段列表随右栏高度拉伸
        loc = ttk.LabelFrame(lin, text="按行定位", padding=8, style="Panel.TLabelframe")
        loc.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        loc.columnconfigure(1, weight=1)
        ttk.Label(loc, text="行号 / N号:").grid(row=0, column=0, sticky="w")
        self.loc_entry = ttk.Entry(loc, width=12)
        self.loc_entry.grid(row=0, column=1, sticky="ew", padx=4)
        self.loc_entry.bind("<Return>", lambda e: self.jump_to_input())
        ttk.Button(loc, text="跳转", command=self.jump_to_input).grid(row=0, column=2)
        ttk.Label(loc, text="例如: 12 或 N6", foreground=theme.TEXT_DIM).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Button(loc, text="上一行", command=lambda: self.step_line(-1)).grid(
            row=2, column=0, pady=(6, 0))
        ttk.Button(loc, text="下一行", command=lambda: self.step_line(1)).grid(
            row=2, column=1, pady=(6, 0), sticky="w")

        sr = ttk.LabelFrame(lin, text="搜索定位", padding=8, style="Panel.TLabelframe")
        sr.grid(row=1, column=0, sticky="ew")
        sr.columnconfigure(1, weight=1)
        ttk.Label(sr, text="关键字:").grid(row=0, column=0, sticky="w")
        self._search_entry = ttk.Entry(sr, width=12)
        self._search_entry.grid(row=0, column=1, sticky="ew", padx=4)
        self._search_entry.bind("<Return>", lambda e: self.search_nc())
        ttk.Button(sr, text="搜索", command=self.search_nc).grid(row=0, column=2)
        ttk.Button(sr, text="下一个", command=lambda: self.search_nc(next_=True)).grid(
            row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(sr, text="在代码中查找文本并跳转", foreground=theme.TEXT_DIM).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # 右列: 段控制 (抬刀平面/导航/段列表/段模式)
        segf = ttk.LabelFrame(rin, text="按段浏览", padding=8, style="Panel.TLabelframe")
        segf.grid(row=0, column=0, sticky="nsew")
        segf.columnconfigure(1, weight=1)
        segf.rowconfigure(6, weight=1)      # 段列表随底部大栏高度伸缩
        ttk.Label(segf, text="抬刀平面 Z:", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.lift_entry = ttk.Entry(segf, width=8)
        self.lift_entry.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(segf, text="应用", command=self._apply_lift).grid(row=0, column=2)
        ttk.Button(segf, text="自动", command=self._auto_lift).grid(row=0, column=3, padx=(4, 0))
        nav = ttk.Frame(segf, style="Panel.TFrame")
        nav.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        ttk.Button(nav, text="◀ 上一段",
                   command=lambda: self._step_segment(-1)).pack(side="left")
        self.seg_lbl = ttk.Label(nav, text="段 -/-", style="Panel.TLabel")
        self.seg_lbl.pack(side="left", padx=6)
        ttk.Button(nav, text="下一段 ▶",
                   command=lambda: self._step_segment(1)).pack(side="left")
        ttk.Label(segf, text="段号:", style="Panel.TLabel").grid(
            row=2, column=0, sticky="w", pady=(6, 0))
        self.seg_entry = ttk.Entry(segf, width=6)
        self.seg_entry.grid(row=2, column=1, sticky="ew", padx=4, pady=(6, 0))
        self.seg_entry.bind("<Return>", lambda e: self._jump_segment())
        ttk.Button(segf, text="跳转", command=self._jump_segment).grid(
            row=2, column=2, pady=(6, 0))
        self.seg_info = ttk.Label(segf, text="", style="Panel.TLabel", font=theme.FONT_SMALL)
        self.seg_info.grid(row=3, column=0, columnspan=4, sticky="w", pady=(4, 0))
        ttk.Checkbutton(segf, text="仅显示勾选段", variable=self._seg_only,
                        command=self._toggle_seg_only).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(segf, text="取消选择",
                   command=self._clear_seg_selection).grid(
            row=4, column=2, columnspan=2, sticky="w", pady=(4, 0), padx=(4, 0))
        ttk.Label(segf, text="所有段 (点击跳转):", style="Panel.TLabel").grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(6, 0))
        segbox = ttk.Frame(segf, style="Panel.TFrame")
        segbox.grid(row=6, column=0, columnspan=4, sticky="nsew", pady=(2, 0))
        segbox.columnconfigure(0, weight=1)
        segbox.rowconfigure(0, weight=1)
        self.seg_listbox = tk.Listbox(segbox, width=18, exportselection=False,
                                      activestyle="dotbox",
                                      selectmode="multiple", relief="flat",
                                      highlightthickness=1,
                                      bg=theme.PANEL, fg=theme.TEXT,
                                      selectbackground=theme.SELECTION,
                                      selectforeground="#ffffff",
                                      highlightbackground=theme.BORDER_LIGHT,
                                      highlightcolor=theme.ACCENT)
        self.seg_listbox.grid(row=0, column=0, sticky="nsew")
        segsb = ttk.Scrollbar(segbox, orient="vertical", command=self.seg_listbox.yview)
        self._seg_vsb = segsb     # 供全局滚轮分派
        segsb.grid(row=0, column=1, sticky="ns")
        self.seg_listbox.config(yscrollcommand=segsb.set)
        self.seg_listbox.bind("<<ListboxSelect>>", self._on_seg_list_select)

        # ---- 栏宽自适应: 默认 = 内容自然宽(不拥挤), 最小 = 内容地板(最窄不溢出) ----
        # 全部按运行时真实控件测量, 与 DPI/字体缩放无关
        self.update_idletasks()     # 先完成几何协商, 容器类控件请求宽才有效
        _mf = tkfont.Font(font=theme.FONT_MONO)
        vsb_w = self._fs_vsb.winfo_reqwidth()
        # 文件栏 >= 文件列表自然宽 + 竖滚动条 + 内边距 (文件名不被过度截断)
        self._min_fs = self.file_listbox.winfo_reqwidth() + vsb_w + 20
        # 统计侧栏 >= 3 个 11 字符单元 (当前位置 XYZ 完整显示)
        self._min_side = 3 * (_mf.measure("0" * 11) + 24) + 44
        # 底部右栏 >= 两列均分: 每列都须容下较宽列的内容 + 竖滚动条/内边距/间隙
        self._min_rc = (2 * max(lin.winfo_reqwidth(), rin.winfo_reqwidth())
                        + 2 * vsb_w + 34)
        # 默认宽度直接给内容自然宽 (最小宽度钳制仍兜底)
        fs_frame.config(width=self._min_fs)
        side.config(width=self._min_side)
        right.config(width=self._min_rc)
        # 用户拖动过 sash 后, 加载文件时不再自动改栏宽
        self._upper_touched = False
        self._rc_touched = False
        upper.bind("<B1-Motion>",
                   lambda e: setattr(self, "_upper_touched", True), add="+")
        code_split.bind("<B1-Motion>",
                        lambda e: setattr(self, "_rc_touched", True), add="+")

    def _bind_canvas(self):
        self.canvas.bind("<ButtonPress-1>", self._pan_start)
        self.canvas.bind("<B1-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-1>", self._pan_end)
        self.canvas.bind("<ButtonPress-2>", self._rot_start)
        self.canvas.bind("<B2-Motion>", self._rot_move)
        self.canvas.bind("<ButtonRelease-2>", self._rot_end)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self._pan_data = None
        self._rot_data = None
        self._rot_mode = "orbit"

    def _on_canvas_configure(self, e):
        """画布尺寸变化: 场景投影/缩放/偏移均未变, 刀路无需重绘;
        仅重贴图例 + 重铺十字虚线 (修复窗口拉伸/任务栏还原时的全量重绘卡顿)"""
        self._place_legend()
        if self.result and self.current_line is not None:
            a, b = project(self.result.position_at_line(self.current_line),
                           self.quat)
            self._draw_crosshair(*self.world_to_canvas(a, b))

    def _wheel_scroll_target(self, w):
        """按指针控件沿父链找滚轮滚动目标 (右侧栏 / 底部右栏两列的画布)。

        段列表自身滚轮由其类绑定处理 (返回 None 跳过, 避免双重滚动);
        段列表的滚动条则滚段列表。其余区域返回 None 不干预。
        """
        targets = (self.side_canvas,) + self._rc_canvases
        while w is not None:
            if w is self.seg_listbox:
                return None
            if w is self._seg_vsb:
                return self.seg_listbox
            for cv in targets:
                if w is cv or w is cv.master:
                    return cv
            w = w.master
        return None

    def _on_side_wheel_global(self, e):
        """全局滚轮: 按指针所在区域滚动对应滚动容器 (右侧栏 / 底部右栏两列,
        含各子控件与滚动条), 其余区域 (主画布/代码区等) 不干预"""
        try:
            w = self.winfo_containing(e.x_root, e.y_root)
        except tk.TclError:
            return
        target = self._wheel_scroll_target(w)
        if target is not None:
            target.yview_scroll(-1 * (e.delta // 120), "units")

    def _apply_default_sash(self):
        """默认 sash: 底部大栏自适应 (小屏 25% 保底, 1080p 更高, 大屏 35%)"""
        try:
            h = self.body_pane.winfo_height()
            if h > 300:
                bottom = max(0.25 * h, min(0.35 * h, h - 620.0))
                self.body_pane.sashpos(0, int(h - bottom))
        except tk.TclError:
            pass

    def _maybe_apply_default_sash(self):
        """启动稳定期内保持默认 sash (用户 3 秒后拖动自由)"""
        if time.time() < self._sash_until:
            self._apply_default_sash()

    def _clamp_bottom_sash(self, e):
        """底部大栏最小高度 25%: 拖动 sash 向下超过 75% 位置时钳回"""
        try:
            h = self.body_pane.winfo_height()
            if h > 300:
                pos = self.body_pane.sashpos(0)
                max_pos = int(h * 0.75)
                if pos > max_pos:
                    self.body_pane.sashpos(0, max_pos)
        except tk.TclError:
            pass

    def _clamp_pane_mins(self, e):
        """两侧栏最小宽度 (画布优先被压缩): 文件栏≥列表自然宽, 统计侧栏≥3×11字符, 底部右栏≥两列自然宽"""
        try:
            up = self.upper_pane
            uw = up.winfo_width()
            if uw > 100:
                if up.sashpos(0) < self._min_fs:
                    up.sashpos(0, self._min_fs)
                if uw - up.sashpos(1) < self._min_side:
                    up.sashpos(1, uw - self._min_side)
            cs = self.code_split
            cw = cs.winfo_width()
            if cw > 100 and cw - cs.sashpos(0) < self._min_rc:
                cs.sashpos(0, cw - self._min_rc)
        except tk.TclError:
            pass

    def _fit_pane_widths(self):
        """按内容自然宽设置栏宽 (加载文件后调用; 用户拖过 sash 则保持不动)。

        统计侧栏内容随程序变化 (S/F 档位列表等): 内容变宽超出视口时把
        侧栏加宽到内容自然宽, 避免横向滚动; 上限为上层窗格宽度的 45%。
        """
        try:
            if not self._upper_touched:
                uw = self.upper_pane.winfo_width()
                if uw > 100:
                    vsb_w = self._fs_vsb.winfo_reqwidth()
                    side_w = max(self._min_side,
                                 self._side_inner.winfo_reqwidth() + vsb_w + 24)
                    side_w = min(side_w, int(uw * 0.45))
                    self.upper_pane.sashpos(0, self._min_fs)
                    self.upper_pane.sashpos(1, uw - side_w)
            if not self._rc_touched:
                cw = self.code_split.winfo_width()
                if cw > 100:
                    self.code_split.sashpos(0, cw - self._min_rc)
        except tk.TclError:
            pass


    # ------------- 文件 -------------
    def _progress_begin(self):
        """开始一个可能较长的加载: 超过 _progress_delay 秒未完成则显示内嵌进度"""
        self._prog_t0 = time.time()
        self._prog = None

    def _progress_create(self):
        """显示内嵌加载进度 (顶部栏右上角); 返回 {"lbl", "bar"}"""
        self._prog_bar.pack(side="right")          # 条先 pack 居最右
        self._prog_lbl.pack(side="right", padx=(0, 6))
        self.update_idletasks()
        return {"lbl": self._prog_lbl, "bar": self._prog_bar}

    def _progress_update(self, text, frac):
        """更新加载进度; 首次超延时才显示内嵌进度; 仅重绘不处理输入 (加载原子完成)"""
        if self._prog is None:
            if self._prog_t0 is None:
                return
            if time.time() - self._prog_t0 < self._progress_delay:
                return
            self._prog = self._progress_create()
        self._prog["lbl"].config(text=text)
        self._prog["bar"].config(value=max(0.0, min(1.0, frac)) * 100)
        self.update_idletasks()

    def _progress_end(self):
        """隐藏内嵌加载进度 (幂等)"""
        if self._prog is not None:
            self._prog_lbl.pack_forget()
            self._prog_bar.pack_forget()
            self._prog = None
        self._prog_t0 = None

    def open_file_multi(self):
        """弹出文件选择框(可多选), 加载所有选中文件并切换到第一个"""
        initdir = _sample_dir()
        paths = filedialog.askopenfilenames(
            title="选择 NC 文件(可多选)",
            initialdir=initdir,
            filetypes=[("NC/G代码", "*.MPF *.NC *.CNC *.TXT *.aptsource *.apt *.mpf *.nc *.cnc *.txt *.Aptsource"),
                       ("所有文件", "*.*")],
        )
        if paths:
            self.add_files(paths)

    def open_file(self, path=None):
        """加载单个文件(供命令行/脚本调用)"""
        if path is None:
            self.open_file_multi()
            return
        self.add_files([path])

    def add_files(self, paths):
        """解析并缓存多个文件, 刷新文件栏, 切换到第一个新加载文件。

        分阶段 (读取/解析/离散刀路/载入界面) 驱动顶部栏内嵌进度;
        读取失败的消息在加载结束关闭弹窗后统一弹出 (避免模态抢占)。
        """
        new_paths = []
        errors = []
        paths = [p for p in paths if p not in self.file_items]
        n = len(paths)
        self._progress_begin()
        try:
            for idx, path in enumerate(paths):
                name = os.path.basename(path)
                base = idx / n
                self._progress_update(f"读取 {name} ({idx + 1}/{n})", base)
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        text = fh.read()
                except OSError as e:
                    errors.append(str(e))
                    continue
                self._progress_update(f"解析 {name} ({idx + 1}/{n})",
                                      base + 0.2 / n)
                result = parse_nc(text)
                moves = result.moves
                # 渲染前一次性离散所有刀路段(含圆弧), 避免旋转时反复重算
                disp3d = []
                step = max(1, len(moves) // 8)
                for j, m in enumerate(moves):
                    disp3d.append(move_points_3d(m, max_seg=4))
                    if j % step == 0:
                        self._progress_update(
                            f"离散刀路 {name} ({idx + 1}/{n})",
                            base + (0.2 + 0.7 * j / len(moves)) / n)
                self.file_items[path] = {
                    "path": path,
                    "text": text,
                    "lines": text.splitlines(),
                    "result": result,
                    "palette": build_palette(result.feeds),
                    "move_by_line": {m.line_number: m for m in moves},
                    "disp3d": disp3d,
                    "move_index": {id(m): i for i, m in enumerate(moves)},
                    # 程序从第一个可解析点开始: 跳过从原点出发的起始进给段
                    "lead_skip": _compute_lead_skip(moves),
                    # 刀具: 从同目录/同名 aptsource 头部解析 (无则 None)
                    "tool": self._parse_tool_for(path, text),
                }
                new_paths.append(path)
            if not new_paths:
                return
            self._refresh_file_list()
            # 默认加载第一个数控程序; 仅导入 apt 时只更新刀具信息
            first_mpf = next((p for p in new_paths
                              if not p.lower().endswith((".aptsource", ".apt"))), None)
            if first_mpf:
                self._progress_update(
                    "载入界面 " + os.path.basename(first_mpf), 0.95)
                self.set_current_file(first_mpf)
            else:
                item = self.file_items[new_paths[0]]
                self.tool = (self.custom_tool if self.custom_tool is not None
                             else item["tool"])
                self._refresh_tool_ui()
        finally:
            self._progress_end()
        for err in errors:
            messagebox.showerror("打开失败", err)

    @staticmethod
    def _parse_tool_for(path, text):
        """解析刀具: 文件本身为 aptsource 直接解析; 否则查同目录同名 aptsource"""
        if path.lower().endswith(".aptsource"):
            return parse_aptsource_tool(text)
        d = os.path.dirname(path)
        stem = os.path.splitext(os.path.basename(path))[0]
        for cand in (stem + "_I.aptsource", stem + ".aptsource"):
            p = os.path.join(d, cand)
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8", errors="ignore") as fh:
                        return parse_aptsource_tool(fh.read())
                except OSError:
                    pass
        return None

    def _refresh_file_list(self):
        """重建双区文件列表 (上: 数控程序, 下: APT) 并更新关联"""
        mp = [p for p in self.file_items
              if not p.lower().endswith((".aptsource", ".apt"))]
        ap = [p for p in self.file_items
              if p.lower().endswith((".aptsource", ".apt"))]
        self.mp_paths = mp
        self.apt_paths = ap
        self.file_listbox.delete(0, "end")
        for p in mp:
            self.file_listbox.insert("end", os.path.basename(p))
        self.apt_listbox.delete(0, "end")
        for p in ap:
            self.apt_listbox.insert("end", os.path.basename(p))
        self._associate_files()

    def _on_file_select(self, e):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self.mp_paths):
            path = self.mp_paths[idx]
            self.set_current_file(path)
            # 高亮关联 APT
            self._highlight_partner(self.apt_listbox, self.apt_paths,
                                    self.file_items[path].get("partner"))

    def _on_apt_select(self, e):
        """点击 APT: 仅更新刀具信息 (不切换主视图) + 高亮关联 MPF"""
        sel = self.apt_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self.apt_paths):
            path = self.apt_paths[idx]
            item = self.file_items[path]
            self.tool = (self.custom_tool if self.custom_tool is not None
                         else item["tool"])
            self._refresh_tool_ui()
            self._highlight_partner(self.file_listbox, self.mp_paths,
                                    item.get("partner"))

    # ------------- 文件列表右键菜单 -------------
    def _popup_file_menu(self, e):
        """右键: 未命中选中集时先单选点击项, 重建配对子菜单后弹出"""
        idx = self.file_listbox.nearest(e.y)
        if idx >= 0 and idx not in self.file_listbox.curselection():
            self.file_listbox.selection_clear(0, "end")
            self.file_listbox.selection_set(idx)
        self._rebuild_pair_menu()
        try:
            self.file_menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.file_menu.grab_release()

    def _rebuild_pair_menu(self):
        """重建「配对 APT 刀具」子菜单: 列出当前全部已加载 APT"""
        self.pair_menu.delete(0, "end")
        if not self.apt_paths:
            self.pair_menu.add_command(label="(无已加载 APT)", state="disabled")
            return
        for p in self.apt_paths:
            self.pair_menu.add_command(label=os.path.basename(p),
                                       command=lambda p=p: self._pair_apt_to_mpf(p))

    def _popup_apt_menu(self, e):
        idx = self.apt_listbox.nearest(e.y)
        if idx >= 0 and idx not in self.apt_listbox.curselection():
            self.apt_listbox.selection_clear(0, "end")
            self.apt_listbox.selection_set(idx)
        try:
            self.apt_menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.apt_menu.grab_release()

    def _menu_delete_mpf(self):
        self._remove_files([self.mp_paths[i] for i in self.file_listbox.curselection()
                            if 0 <= i < len(self.mp_paths)])

    def _menu_delete_apt(self):
        self._remove_files([self.apt_paths[i] for i in self.apt_listbox.curselection()
                            if 0 <= i < len(self.apt_paths)])

    def _remove_files(self, paths):
        """从缓存删除文件并刷新; 当前文件被删则切换到剩余首个 MPF (全删则清空视图)"""
        if not paths:
            return
        cur = self._current_path
        for p in paths:
            self.file_items.pop(p, None)
        self._refresh_file_list()
        if cur in paths:
            if self.mp_paths:
                self.set_current_file(self.mp_paths[0])
            else:
                self._clear_view()

    def _clear_view(self):
        """所有文件删除后清空主视图"""
        self.result = None
        self.lines = []
        self.palette = {}
        self.move_by_line = {}
        self._disp3d = None
        self._move_index = {}
        self._current_path = None
        self.current_line = None
        self.tool = None
        self._parsed_tool = None
        self._segments = []
        self._seg_of_line = {}
        self._seg_checked = set()
        self._time_prefix = None
        self._time_pos = {}
        self._render_meta = None       # 无刀路: 元数据清空
        self._stop_playback()
        self._trace_active = False
        self.file_lbl.config(text="(未打开文件)")
        self.render()
        self._fill_code()
        self._refresh_tool_ui()

    def _pair_apt_to_mpf(self, apt_path):
        """手动配对: 把指定 APT 的刀具带给 MPF 列表中选中项"""
        if apt_path not in self.file_items:
            return
        msel = self.file_listbox.curselection()
        if not msel:
            return
        mpf_path = self.mp_paths[msel[0]]
        tool = self.file_items[apt_path]["tool"]
        if tool is None:
            messagebox.showinfo("配对", "该 APT 文件未解析出刀具 (CUTTER/TOOLNO)")
            return
        item = self.file_items[mpf_path]
        item["tool"] = tool
        item["partner"] = apt_path
        self.file_items[apt_path]["partner"] = mpf_path
        # 当前显示程序即被配对项: 立即刷新刀具显示
        if self._current_path == mpf_path:
            self._parsed_tool = tool
            self.tool = (self.custom_tool if self.custom_tool is not None else tool)
            self._refresh_tool_ui()
        # 双向高亮
        self._highlight_partner(self.apt_listbox, self.apt_paths, apt_path)
        self._highlight_partner(self.file_listbox, self.mp_paths, mpf_path)

    @staticmethod
    def _highlight_partner(box, paths, partner):
        """在另一列表中高亮关联文件"""
        box.selection_clear(0, "end")
        if partner and partner in paths:
            idx = paths.index(partner)
            box.selection_set(idx)
            box.see(idx)

    def _associate_files(self):
        """按文件名/程序名建立 mpf<->apt 关联 (双向写入 partner)"""
        for item in self.file_items.values():
            item["partner"] = None
        mpf_names = {}        # 规范化名/程序名 -> path
        for p in self.mp_paths:
            item = self.file_items[p]
            stem = os.path.splitext(os.path.basename(p))[0].lower()
            mpf_names[stem] = p
            prog = _mpf_program_name(item["text"])
            if prog:
                mpf_names.setdefault(prog.lower(), p)
        for p in self.apt_paths:
            item = self.file_items[p]
            stem = os.path.splitext(os.path.basename(p))[0].lower()
            cand = stem[:-2] if stem.endswith("_i") else stem     # 去 _I 后缀
            partner = mpf_names.get(cand)
            if partner is None:
                prog = _apt_program_name(item["text"])
                if prog:
                    pl = prog.lower()
                    for stem2, p2 in mpf_names.items():           # 程序名互为子串
                        if pl in stem2 or stem2 in pl:
                            partner = p2
                            break
            if partner:
                item["partner"] = partner
                self.file_items[partner]["partner"] = p

    def set_current_file(self, path):
        """把某个已加载文件的状态恢复到主视图"""
        item = self.file_items.get(path)
        if item is None:
            return
        self.result = item["result"]
        self.lines = item["lines"]
        self.palette = item["palette"]
        self.move_by_line = item["move_by_line"]
        self._disp3d = item["disp3d"]
        self._move_index = item["move_index"]
        self._lead_skip = item["lead_skip"]
        self._build_render_meta()      # 渲染预计算元数据 (可见性/颜色/合并链)
        self._parsed_tool = item["tool"]
        self.tool = self.custom_tool if self.custom_tool is not None else self._parsed_tool
        self._current_path = path
        self.current_line = None
        # 重置搜索状态(代码内容已重建, 旧命中行号失效)
        self._search_pattern = None
        self._search_hits = []
        self._search_idx = -1
        # 重置逐行运行状态
        self._stop_playback()
        self._trace_active = False
        self._trace_drawn = 0
        self._trace_items = []
        self._clear_target()
        self._seg_checked = set()      # 勾选段随程序重建, 防上个程序的越界索引
        self.file_lbl.config(text=os.path.basename(path))
        self._recompute_segments()     # 段号映射 + 代码区填充 (含段号标注)
        self._build_time_prefix()      # 播放进度时间轴 (按加工时间)
        self._hide_play_progress()
        self._fill_legend()
        self._fill_stats()
        self._refresh_tool_ui()
        self._fit_pane_widths()      # 栏宽按新内容自然宽适配
        self.fit_view()
        # 布局稳定后 (sash/窗口定型) 再自动适配一次, 保证图像大小合适
        self.after(700, lambda: self.fit_view() if self.result else None)
        self.status.config(text=self._status_text())
        # 高亮文件栏当前项
        self.file_listbox.selection_clear(0, "end")
        self.apt_listbox.selection_clear(0, "end")
        if path in self.mp_paths:
            idx = self.mp_paths.index(path)
            self.file_listbox.selection_set(idx)
            self.file_listbox.see(idx)
        elif path in self.apt_paths:
            idx = self.apt_paths.index(path)
            self.apt_listbox.selection_set(idx)
            self.apt_listbox.see(idx)

    def _status_text(self):
        r = self.result
        if not r:
            return ""
        return (f"共 {len(self.lines)} 行  |  刀路段 {len(r.moves)}  |  F 档位 {len(r.feeds)} 个  |  "
                "轨道球视角  |  中键旋转 / 左键平移 / 滚轮缩放")

    # ------------- 代码列表 -------------
    def _fill_code(self):
        self.code.configure(state="normal")
        self.code.delete("1.0", "end")
        # 为每个颜色建一个标签
        self.code.tag_configure("g0", foreground=G0_COLOR)
        for f, col in self.palette.items():
            self.code.tag_configure(self._f_tag(f), foreground=col)
        move_by_line = self.move_by_line
        seg_of_line = self._seg_of_line
        # 单次批量插入 (文本+标签成对), 大程序填充提速 (实测最优)
        parts = []
        for i, line in enumerate(self.lines, 1):
            m = move_by_line.get(i)
            if m is None:
                tag = ""
            elif m.motion == "G0":
                tag = "g0"
            else:
                tag = self._f_tag(m.feed)
            parts.append(f"{i:5d}|")
            parts.append("ln")
            segno = seg_of_line.get(i)       # 行号槽段号标注 (段外行留空)
            if segno:
                parts.append("%-4s" % ("S%d" % segno))
                parts.append("segno")
            else:
                parts.append("    ")
                parts.append("ln")
            parts.append(line + "\n")
            parts.append(tag)
        if parts:
            self.code.insert("end", *parts)
        self.code.configure(state="disabled")

    @staticmethod
    def _f_tag(feed):
        return "f_" + format(feed, ".4f").rstrip("0").rstrip(".")

    def _fill_legend(self):
        for w in self.legend.winfo_children():
            w.destroy()
        # 图例只显示程序实际存在的对照: 无 G0 移动则不显示 G0 项
        has_g0 = any(m.motion == "G0" for m in self.result.moves)
        items = ([(G0_COLOR, "G0 快速移动")] if has_g0 else []) + [
            (self.palette[f], f"F{format(f, '.4f').rstrip('0').rstrip('.')}")
            for f in self.result.feeds
        ]
        # 漂浮图例: 每列最多 6 项, 超出向左开新列 (首列在最右)
        n_cols = (len(items) + 5) // 6
        for i, (color, text) in enumerate(items):
            k = n_cols - 1 - (i // 6)      # 列号: 首列在最右, 新列向左
            row = i % 6
            sw = tk.Label(self.legend, bg=color, width=2, height=1, relief="flat")
            sw.grid(row=row, column=k * 2, padx=(6, 2), pady=2, sticky="w")
            ttk.Label(self.legend, text=text, style="Panel.TLabel").grid(
                row=row, column=k * 2 + 1, sticky="w", padx=(0, 6))

    # ------------- 程序统计 -------------
    @staticmethod
    def _fmt_time(seconds):
        """秒 -> h:mm:ss 显示 (向上取整: 显示至少需要的加工时间)"""
        seconds = max(0, int(math.ceil(seconds)))
        return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60,
                                 seconds % 60)

    def _fill_stats(self, time_override=_UNSET):
        """刷新侧栏关键统计 (S/F 显示具体值; 段模式为勾选段内统计)。
        time_override 为 (start_idx, end_idx) 时加工时间按该段统计
        (画布拾取某刀路), 否则按勾选段/全程序。"""
        st = compute_stats(self.result, move_range=self._seg_filter)
        fmt = lambda v: f"{v:.3f}"
        self.stats_labels["x"].config(text=f"{fmt(st.x_min)} ~ {fmt(st.x_max)}")
        self.stats_labels["y"].config(text=f"{fmt(st.y_min)} ~ {fmt(st.y_max)}")
        self.stats_labels["z"].config(text=f"{fmt(st.z_min)} ~ {fmt(st.z_max)}")
        s_txt = "-" if not st.s_levels else " · ".join(f"{v:g}" for v in st.s_levels)
        self.stats_labels["s"].config(text=s_txt)
        f_txt = "-" if not st.f_levels else " · ".join(f"{v:g}" for v in st.f_levels)
        self.stats_labels["f"].config(text=f_txt)
        g = st.g_counts
        g_txt = "  ".join(f"G{i}:{g.get(f'G{i}', 0)}" for i in (0, 1, 2, 3))
        self.stats_labels["g"].config(text=g_txt)
        self.stats_labels["tool"].config(
            text=tool_summary(self.tool) if self.tool else "-")
        tr = self._seg_filter if time_override is _UNSET else time_override
        # 含全部移动 (程序从第一条起算): 从原点出发的首条可能是真实加工,
        # 前导定位段的显示跳过 (lead_skip) 不适用于时间统计
        self.stats_labels["time"].config(text="-"
            if not self.result else self._fmt_time(
                compute_machining_time(self.result.moves, move_range=tr)))

    def _set_pick_time(self, ln):
        """画布拾取某刀路: 统计加工时间显示该刀路所在段的加工时间"""
        if not self.result:
            return
        seg_idx = self._segment_for_line(ln)
        if 0 < seg_idx <= len(self._segments):
            seg = self._segments[seg_idx - 1]
            self._fill_stats(time_override=(seg.start_idx, seg.end_idx))
        else:
            self._fill_stats(time_override=None)   # 段外行: 全程序/勾选段

    def _reset_pick_time(self):
        """取消拾取: 加工时间恢复默认 (勾选段/全程序)"""
        self._fill_stats()

    def _refresh_tool_ui(self):
        """刷新刀具显示 (图例行 / 统计行 / 3D 模型开关 / 内嵌剖面图)"""
        has_tool = self.tool is not None
        self.tool_lbl.config(text=tool_summary(self.tool) if has_tool else "-")
        self.tool_btn.config(state="normal" if has_tool else "disabled")
        self.tool_chk.config(state="normal" if has_tool else "disabled")
        if "tool" in self.stats_labels:
            self.stats_labels["tool"].config(
                text=tool_summary(self.tool) if has_tool else "-")
        self._draw_tool_profile_inline()

    def _draw_tool_profile_inline(self):
        """刀具栏内直接绘制紧凑剖面图 (含缩颈刀柄, 无尺寸标注线)"""
        cv = self.tool_cv
        cv.delete("all")
        if not self.tool:
            return
        full = tool_full_profile(self.tool)
        if not full:
            return
        max_r = max(r for r, _ in full)
        h = max(y for _, y in full)
        # 用画布实际尺寸 (压缩后可能小于默认, 避免绘制被底部裁切)
        W = cv.winfo_width() or 180
        H = cv.winfo_height() or 140
        pad = 8
        scale = min((W - 2 * pad) / (2 * max_r), (H - 2 * pad) / h)
        if scale <= 0:
            scale = 1.0
        ox = W / 2
        oy = H - pad
        outline = ([(0.0, 0.0)]
                   + [(r, y) for r, y in full]
                   + [(-r, y) for r, y in reversed(full)])
        coords = []
        for r, y in outline:
            coords.append(ox + r * scale)
            coords.append(oy - y * scale)
        cv.create_polygon(coords, fill="#4a4a52", outline="#c8c8c8", width=1)
        cv.create_line(ox, oy, ox, oy - h * scale, fill="#6e6e6e", dash=(3, 3))
        # 紧凑标注: 直径与刃长
        y_max = max(y for r, y in full if abs(r - max_r) < 1e-9)
        d = float(self.tool.p("d", 0.0))
        cv.create_text(ox, oy - y_max * scale + 12,
                       text=f"D{d:g}" if d else "", fill=theme.TEXT,
                       font=theme.FONT_SMALL)
        l = float(self.tool.p("l", 0.0))
        cv.create_text(ox + max_r * scale + 8, oy - l * scale / 2,
                       text=f"L{l:g}" if l else "", fill=theme.TEXT,
                       font=theme.FONT_SMALL)

    def show_details(self):
        """二级窗口: 完整程序统计"""
        if not self.result:
            return
        st = compute_stats(self.result)
        win = tk.Toplevel(self)
        _set_icon(win)              # 二级窗口图标 (全局生效)
        win.title(f"程序详情 — {os.path.basename(self._current_path)}")
        win.configure(bg=theme.BG)
        win.geometry("560x520")
        frm = ttk.Frame(win, padding=12, style="Panel.TFrame")
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        fmt = lambda v: f"{v:.3f}"
        rows = [
            ("行数", str(len(self.lines))),
            ("刀路段数", str(st.moves_total)),
            ("切削段数 (G1/G2/G3)", f"{st.cut_total}  (快移 {st.moves_total - st.cut_total})"),
            ("行程 X", f"{fmt(st.x_min)} ~ {fmt(st.x_max)}"),
            ("行程 Y", f"{fmt(st.y_min)} ~ {fmt(st.y_max)}"),
            ("行程 Z", f"{fmt(st.z_min)} ~ {fmt(st.z_max)}"),
            ("S 转速", "-" if st.s_min is None else f"{st.s_min:.0f} ~ {st.s_max:.0f}"),
            ("F 进给", "-" if st.f_min is None else f"{st.f_min:g} ~ {st.f_max:g} · {st.f_count} 档"),
        ]
        for r, (k, v) in enumerate(rows):
            ttk.Label(frm, text=k, style="Panel.TLabel").grid(row=r, column=0, sticky="w", pady=2)
            ttk.Label(frm, text=v, font=theme.FONT_MONO, style="Panel.TLabel").grid(
                row=r, column=1, sticky="w", pady=2)
        r0 = len(rows)
        ttk.Label(frm, text="各 F 档位段数", style="Panel.TLabel").grid(
            row=r0, column=0, sticky="w", pady=(8, 2))
        for r, f in enumerate(self.result.feeds):
            n = st.f_seg_counts.get(f, 0)
            pct = (n / st.cut_total * 100) if st.cut_total else 0.0
            f_str = f"F{format(f, '.4f').rstrip('0').rstrip('.')}"
            ttk.Label(frm, text=f_str, style="Panel.TLabel").grid(
                row=r0 + 1 + r, column=0, sticky="w", pady=1)
            ttk.Label(frm, text=f"{n} 段 ({pct:.1f}%)", font=theme.FONT_MONO,
                      style="Panel.TLabel").grid(row=r0 + 1 + r, column=1, sticky="w", pady=1)
        g = st.g_counts
        g_txt = "  ".join(f"G{i}: {g.get(f'G{i}', 0)}" for i in (0, 1, 2, 3))
        r_last = r0 + 1 + len(self.result.feeds)
        ttk.Label(frm, text="G 指令次数", style="Panel.TLabel").grid(
            row=r_last, column=0, sticky="w", pady=(8, 2))
        ttk.Label(frm, text=g_txt, font=theme.FONT_MONO, style="Panel.TLabel").grid(
            row=r_last, column=1, sticky="w", pady=(8, 2))

    # ------------- F 进给趋势曲线 -------------
    def _f_curve_data(self):
        """F 曲线数据: 切削移动的 (行号, 累计加工时间秒, F) 序列。
        时间遍历全部移动 (含 G0 快移) 累加, 保证时间横轴连续; G0 无 F 不参与"""
        if not self.result:
            return []
        out = []
        t = 0.0
        for m in self.result.moves:
            t += move_time_sec(m)
            if m.feed is not None:          # 切削移动 (G0 无 F)
                out.append((m.line_number, t, m.feed))
        return out

    def show_f_curve(self):
        """二级窗口: F 进给随加工时间变化趋势 (横轴可切换行号, 可拉伸)"""
        if not self.result:
            return
        data = self._f_curve_data()
        if not data:
            messagebox.showinfo("F 曲线", "程序中没有切削移动 (G0 快移无 F)")
            return
        win = tk.Toplevel(self)
        _set_icon(win)              # 二级窗口图标 (全局生效)
        win.title(f"F 进给趋势 — {os.path.basename(self._current_path)}")
        win.configure(bg=theme.BG)
        win.geometry("860x480")
        win.minsize(520, 340)
        cv = tk.Canvas(win, bg=theme.EDITOR_BG, highlightthickness=0)
        cv.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        # 横向滚动条: 时间轴缩放后平移视口 (拖动查看); 全览时隐藏
        hsb = ttk.Scrollbar(win, orient="horizontal",
                            command=self._curve_hscroll)
        self._curve_hsb = hsb
        # 底部配置栏: 横轴加工时间/行号切换 + 时间单位 (秒/分钟/小时)
        ctl = ttk.Frame(win, style="Panel.TFrame")
        ctl.pack(side="bottom", fill="x", padx=8, pady=(0, 6))
        ttk.Label(ctl, text="横轴:", style="Panel.TLabel").pack(side="left")
        self._curve_axis_var = tk.StringVar(value="time")
        for text, val in (("加工时间", "time"), ("行号", "line")):
            ttk.Radiobutton(ctl, text=text, variable=self._curve_axis_var,
                            value=val, command=self._curve_axis_changed
                            ).pack(side="left", padx=(4, 0))
        ttk.Label(ctl, text="  单位:", style="Panel.TLabel").pack(side="left")
        self._curve_unit_var = tk.StringVar(value="sec")
        self._curve_unit_rbs = []
        for text, val in (("秒", "sec"), ("分钟", "min"), ("小时", "hour")):
            rb = ttk.Radiobutton(ctl, text=text, variable=self._curve_unit_var,
                                 value=val, command=self._curve_axis_changed)
            rb.pack(side="left", padx=(4, 0))
            self._curve_unit_rbs.append(rb)
        self._curve_job = None
        self._curve_cv = cv
        self._curve_data = data
        self._curve_view = None          # 时间轴视口 (t_lo, t_hi), None=全览
        cv.bind("<Configure>", lambda e: self._curve_redraw(cv, data, e))
        cv.bind("<MouseWheel>", self._curve_wheel)
        self._draw_f_curve(cv, data, cv.winfo_width() or 844,
                           cv.winfo_height() or 420)
        self._curve_scroll_update()

    def _curve_axis_changed(self, coarse=False):
        """横轴/单位切换或视口变化: 重绘并同步滚动条; coarse=拖动中轻量帧"""
        self._curve_job = None           # 防抖回调已触发
        self._draw_f_curve(self._curve_cv, self._curve_data,
                           self._curve_cv.winfo_width() or 844,
                           self._curve_cv.winfo_height() or 428,
                           axis=self._curve_axis_var.get(),
                           unit=self._curve_unit_var.get() if hasattr(
                               self, "_curve_unit_var") else "sec",
                           view=self._curve_view if self._curve_axis_var.get()
                           == "time" else None,
                           coarse=coarse)
        self._curve_scroll_update()

    def _curve_finalize(self):
        """拖动停止后恢复精绘 (描边 + 全量抽稀)"""
        self._curve_final_job = None
        self._curve_axis_changed(coarse=False)

    def _curve_redraw(self, cv, data, e):
        """窗口拉伸时 60ms 防抖重绘 (保持当前横轴选择/单位/视口)"""
        if self._curve_job is not None:
            self.after_cancel(self._curve_job)
        axis = getattr(self, "_curve_axis_var", None)
        unit = getattr(self, "_curve_unit_var", None)
        self._curve_job = self.after(
            60, lambda: self._draw_f_curve(
                cv, data, e.width, e.height,
                axis=axis.get() if axis else "time",
                unit=unit.get() if unit else "sec",
                view=self._curve_view if (axis and axis.get() == "time")
                else None))

    def _curve_scroll_update(self):
        """同步横向滚动条: 时间轴视口在全览内的位置; 全览/行号模式隐藏"""
        sb = getattr(self, "_curve_hsb", None)
        if sb is None or not self._curve_data:
            return
        if self._curve_axis_var.get() != "time":
            sb.pack_forget()
            return
        T = self._curve_data[-1][1]
        t_lo, t_hi = self._curve_view or (0.0, T)
        w = t_hi - t_lo
        shown = sb.winfo_manager() == "pack"
        if T <= 0 or w >= T - 1e-9:
            if shown:
                sb.pack_forget()             # 全览: 隐藏滚动条
        else:
            sb.set(t_lo / T, t_hi / T)
            if not shown:                    # 避免重复 pack 触发布局重算
                sb.pack(side="bottom", fill="x", padx=8)

    def _curve_wheel(self, e):
        """滚轮横向缩放 (仅时间轴): 以鼠标 x 位置为锚缩放视口"""
        if self._curve_axis_var.get() != "time" or not self._curve_data:
            return
        cv = self._curve_cv
        W = cv.winfo_width()
        if W < 50:                       # 未映射 (测试) 时回退默认宽度
            W = 640
        plot_w = W - 64 - 36
        if plot_w <= 20:
            return
        T = self._curve_data[-1][1]
        if T <= 0:
            return
        t_lo, t_hi = self._curve_view or (0.0, T)
        frac = min(1.0, max(0.0, (e.x - 64) / plot_w))
        t_at = t_lo + frac * (t_hi - t_lo)
        factor = 1 / 1.25 if e.delta > 0 else 1.25   # 向上滚=放大(视口变窄)
        nw = min(T, max(1.0, (t_hi - t_lo) * factor))
        t_lo = t_at - frac * nw
        t_hi = t_lo + nw
        if t_lo < 0:
            t_lo, t_hi = 0.0, nw
        if t_hi > T:
            t_hi, t_lo = T, T - nw
        if (t_lo, t_hi) == (self._curve_view or (0.0, T)):
            return                       # 视口未变化 (已到极限): 不调度重绘
        self._curve_view = (t_lo, t_hi)
        self._curve_schedule_redraw()

    def _curve_schedule_redraw(self, coarse=False):
        """视口变化防抖重绘: 事件风暴合并为一次重绘 (50ms 跟手)。
        coarse=True (滚动条拖动): 轻量帧, 停止 300ms 后自动恢复精绘"""
        job = getattr(self, "_curve_job", None)
        if job is not None:
            self.after_cancel(job)
        fin = getattr(self, "_curve_final_job", None)
        if fin is not None:
            self.after_cancel(fin)
            self._curve_final_job = None
        if coarse:
            self._curve_job = self.after(
                50, lambda: self._curve_axis_changed(coarse=True))
            self._curve_final_job = self.after(300, self._curve_finalize)
        else:
            self._curve_job = self.after(50, self._curve_axis_changed)

    def _curve_hscroll(self, *a):
        """横向滚动条拖动: 平移时间轴视口 (仅时间模式)"""
        if self._curve_axis_var.get() != "time" or not self._curve_data:
            return
        T = self._curve_data[-1][1]
        if T <= 0:
            return
        t_lo, t_hi = self._curve_view or (0.0, T)
        w = t_hi - t_lo
        if a[0] == "moveto":
            t_lo = float(a[1]) * (T - w)
        elif a[0] == "scroll":
            n = int(a[1])
            t_lo += n * (w * 0.9 if a[2] == "pages" else T / 100.0)
        t_lo = max(0.0, min(T - w, t_lo))
        self._curve_view = (t_lo, t_lo + w)
        self._curve_schedule_redraw(coarse=True)   # 拖动: 轻量帧 + 停止后精绘

    @staticmethod
    def _nice_time_step(span_sec):
        """按视口跨度 (秒) 选刻度间隔: 目标 4-8 档"""
        for step in (1, 2, 5, 10, 30, 60, 120, 300, 600, 1800, 3600,
                     7200, 18000, 36000, 72000, 180000):
            if span_sec / step <= 8:
                return step
        return max(1.0, span_sec / 8)

    @staticmethod
    def _time_unit_div(unit):
        return {"sec": 1.0, "min": 60.0, "hour": 3600.0}.get(unit, 1.0)

    def _draw_f_curve(self, cv, data, W, H, axis="time", unit="sec", view=None,
                      coarse=False):
        """按给定尺寸绘制 F 趋势图 (可独立于窗口尺寸测试/重绘)。
        data: [(行号, 累计加工时间秒, F)]; axis="time" 横轴为加工时间,
        "line" 横轴为行号; unit 为时间单位 (sec/min/hour); view 为时间
        视口 (t_lo, t_hi 秒), None=全览。余量充足 + 双线曲线保证清晰"""
        cv.delete("all")
        if not data:
            return
        pad_l, pad_r, pad_t, pad_b = 64, 36, 40, 46
        plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b
        if plot_w <= 20 or plot_h <= 20:
            return
        feeds = [f for _, _, f in data]
        fmin, fmax = min(feeds), max(feeds)
        if fmax == fmin:
            fmax = fmin + 1.0
        div = self._time_unit_div(unit)
        if axis == "line":
            x0, x1 = data[0][0], data[-1][0]
            fmt_x = lambda v: f"{v:.0f}"
            x_label = "行号"
            ticks = [x0 + (x1 - x0) * i / 7 for i in range(8)]
        else:
            t0, t1 = view if view else (0.0, data[-1][1])
            if t1 <= t0:
                t1 = t0 + 1.0
            x0, x1 = t0 / div, t1 / div          # 单位化坐标
            u_name = {"sec": "秒", "min": "分钟", "hour": "小时"}.get(unit, "秒")
            x_label = f"加工时间 ({u_name})"
            fmt_x = lambda v: f"{v:g}"
            step = self._nice_time_step(t1 - t0) / div
            ticks = []
            k = math.ceil(x0 / step - 1e-9) * step
            while k <= x1 + 1e-9:
                ticks.append(k)
                k += step
        if x1 == x0:
            x1 = x0 + 1.0
        fmt_axis = lambda v: f"{v:.0f}" if v >= 100 else f"{v:g}"

        def sx(x_unit):
            return pad_l + (x_unit - x0) / (x1 - x0) * plot_w

        def sy(v):
            return pad_t + (1 - (v - fmin) / (fmax - fmin)) * plot_h

        # 网格 + 刻度: 纵 6 档; 横按刻度间隔 (视口缩放时自动加密/变稀)
        for i in range(6):
            v = fmin + (fmax - fmin) * i / 5
            y = sy(v)
            cv.create_line(pad_l, y, W - pad_r, y, fill=theme.BORDER, tags="grid")
            cv.create_text(pad_l - 10, y, text=fmt_axis(v), anchor="e",
                           fill=theme.TEXT_DIM, font=theme.FONT_SMALL)
        for x in ticks:
            xx = sx(x)
            cv.create_line(xx, pad_t, xx, H - pad_b, fill=theme.BORDER, tags="grid")
            cv.create_text(xx, H - pad_b + 10, text=fmt_x(x), anchor="n",
                           fill=theme.TEXT_DIM, font=theme.FONT_SMALL)
        # 坐标轴与轴标签
        cv.create_line(pad_l, pad_t, pad_l, H - pad_b, fill=theme.TEXT_DIM)
        cv.create_line(pad_l, H - pad_b, W - pad_r, H - pad_b, fill=theme.TEXT_DIM)
        cv.create_text(W // 2, H - 8, text=x_label, fill=theme.TEXT,
                       font=theme.FONT_UI)
        cv.create_text(8, pad_t - 16, text="F 进给", anchor="w",
                       fill=theme.TEXT, font=theme.FONT_UI)
        # min/max F 参考虚线 + 标注
        for v in (fmin, fmax):
            y = sy(v)
            cv.create_line(pad_l, y, W - pad_r, y, fill=theme.TEXT_DIM, dash=(3, 3))
            cv.create_text(W - pad_r - 4, y - 5, text=f"F{fmt_axis(v)}",
                           anchor="e", fill=theme.TEXT_DIM, font=theme.FONT_SMALL)
        # 抽稀: 每像素最多 1 个数据点 (阶梯线每点生成 2 个坐标点, Tk
        # create_line 大点数极慢; 缩放后视口内点少自然不抽稀保持精确;
        # 拖动粗绘时抽稀加倍, 渲染更快)
        max_pts = max(16, plot_w // (2 if coarse else 1))
        draw = data
        if len(data) > max_pts:
            step = math.ceil(len(data) / max_pts)
            draw = data[::step]
            if draw[0] != data[0]:
                draw = [data[0]] + draw
            if draw[-1] != data[-1]:
                draw = draw + [data[-1]]
        # 阶梯线: F 阶跃变化, 每个 F 值保持水平线段到下一数据点时间,
        # 然后垂直跳变到新 F (无斜线); 精绘双线 (亮色描边 + 主色),
        # 拖动粗绘仅主曲线 (描边是渲染大头, 拖动时省去)
        pts = []
        for i in range(len(draw) - 1):
            _ln, t, f = draw[i]
            _ln2, t2, _f2 = draw[i + 1]
            xv = (_ln if axis == "line" else t / div)
            xv2 = (_ln2 if axis == "line" else t2 / div)
            if i == 0:
                pts.extend([sx(xv), sy(f)])    # 首个水平段起点
            pts.extend([sx(xv2), sy(f)])       # 水平延伸到下一数据点时间
            pts.extend([sx(xv2), sy(_f2)])     # 垂直跳变到新 F
        _ln, t, f = draw[-1]
        xv = (_ln if axis == "line" else t / div)
        pts.extend([sx(xv), sy(f)])            # 末点收尾
        if len(pts) >= 4:
            if not coarse:
                cv.create_line(pts, fill="#7fb3d9", width=5, joinstyle="round",
                               tags="curve")
            cv.create_line(pts, fill=theme.ACCENT, width=2.5, joinstyle="round",
                           tags="curve")
        else:
            cv.create_oval(pts[0] - 3, pts[1] - 3, pts[0] + 3, pts[1] + 3,
                           fill=theme.ACCENT, outline="#7fb3d9", tags="curve")
        # 点数少 (<=200) 时标出数据点
        if 0 < len(draw) <= 200:
            for _ln, t, f in draw:
                xv = (_ln if axis == "line" else t / div)
                x, y = sx(xv), sy(f)
                cv.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5,
                               fill=theme.ACCENT, outline="#7fb3d9",
                               tags="curve")

    # ------------- 刀具 3D 模型 -------------
    def _tool_model_points(self, tool):
        """刀具旋转体 3D 模型点集 [(kind, [(x,y,z),...], nx, ny)], 本地坐标:
        刀尖在原点, 刀具轴沿 +Z 向上。

        band: 侧壁明暗条带 (n_band 段覆盖全圆周, 法向 (nx,ny,0)——绘制层
        按法向·固定光源定灰阶 + 背面剔除 + 同色描边 → **覆盖整个投影区域
        的圆柱渐变立体**); top: 顶面圆盘 (受光亮); bottom: 底部圆盘 (仅
        平底刀, 背光暗, 底部闭合); outline: 外轮廓描边 (不填充);
        tip: 刀尖标记。无经线/截面圆/轴等外框线。
        """
        full = tool_full_profile(tool)       # 切削部分 + 刀柄 (反锥含缩颈)
        h = tool_overall_height(tool)
        r_top = full[-1][0]
        n_band = 128                 # 条带密度: 相邻色差 ~3/255, 渐变绝对无分段
        pts = []
        # 侧壁条带: 两条经线间的轮廓面 (覆盖全圆周, 上下闭合)
        for k in range(n_band):
            t0 = 2 * math.pi * k / n_band
            t1 = 2 * math.pi * (k + 1) / n_band
            tm = (t0 + t1) / 2
            band = ([(r * math.cos(t0), r * math.sin(t0), y) for r, y in full]
                    + [(r * math.cos(t1), r * math.sin(t1), y)
                       for r, y in reversed(full)])
            pts.append(("band", band, math.cos(tm), math.sin(tm)))
        # 顶面圆盘 (斜视角呈椭圆, 法向 +Z 恒受光)
        top_circle = [(r_top * math.cos(t), r_top * math.sin(t), h)
                      for t in (i * math.tau / 24 for i in range(24))]
        pts.append(("top", top_circle, 0.0, 0.0))
        # 底部圆盘 (仅平底刀: 底部为平面; 尖底/圆角底无平面不画)
        bottom_r = max((r for r, y in full if y <= 1e-9), default=0.0)
        if bottom_r > 0.01:
            pts.append(("bottom", [(bottom_r * math.cos(t),
                                    bottom_r * math.sin(t), 0.0)
                                   for t in (i * math.tau / 24
                                             for i in range(24))], 0.0, 0.0))
        pts.append(("tip", [(0.0, 0.0, 0.0)], 0.0, 0.0))
        return pts

    def _draw_tool_model(self):
        """画布内绘制刀具旋转 3D 模型 (实心实体, 刀尖对刀)"""
        if not self.show_tool.get() or not self.tool or not self.result:
            return
        tool = self.tool
        pos = (self.result.position_at_line(self.current_line)
               if self.current_line else (0.0, 0.0, 0.0))
        max_r = max(r for r, _ in tool_full_profile(tool))
        # 最小可见尺寸: 直径投影 <24px 时以刀尖为锚放大。旋转体任意角度
        # 投影宽度 = 直径 (与视角无关), 避免轴向投影趋零时突然放大 8 倍
        px_w = 2 * max_r * self.scale
        factor = 1.0
        if 0 < px_w < 24:
            factor = min(24.0 / px_w, 6.0)
        # 观察方向 (正交投影视线) 与固定光源 (世界空间)
        q = self.quat
        vx, vy, vz = quat_rotate((0.0, 0.0, 1.0), q)
        lx, ly, lz = _TOOL_LIGHT
        model = []
        for kind, pts, nx, ny in self._tool_model_points(tool):
            screen = []                  # 扁平坐标 [x0,y0,x1,y1,...]
            for x, y, z in pts:
                wx = pos[0] + x * factor
                wy = pos[1] + y * factor
                wz = pos[2] + z * factor
                a, b = project((wx, wy, wz), q)
                sx, sy = self.world_to_canvas(a, b)
                screen.append(sx)
                screen.append(sy)
            model.append((kind, screen, nx, ny))
        for kind, screen, nx, ny in model:
            if kind == "tip":
                x0, y0 = screen[0], screen[1]
                self.canvas.create_oval(x0 - 4, y0 - 4, x0 + 4, y0 + 4,
                                        fill=CUR_COLOR, outline="#000000",
                                        tags="toolmodel")
            elif kind == "top":
                # 顶面圆盘: 受光亮面, 斜视角呈椭圆 (法向 +Z 恒受光)
                lum = max(0.0, min(1.0, lz))
                v = int(45 + 150 * lum)
                self.canvas.create_polygon(screen, fill="#%02x%02x%02x"
                                           % (v, v, v + 6),
                                           outline="", tags="toolmodel")
            elif kind == "bottom":
                # 底部圆盘: 背光暗面, 平底刀底部闭合不镂空
                self.canvas.create_polygon(screen, fill="#2d2d33",
                                           outline="", tags="toolmodel")
            else:
                # 面法向旋转到世界空间; 背面剔除 + 法向·光源定灰阶
                nxw, nyw, nzw = quat_rotate((nx, ny, 0.0), q)
                if nxw * vx + nyw * vy + nzw * vz < 0:
                    continue               # 背面 (正交投影无遮挡, 主动剔除)
                lum = max(0.0, min(1.0, nxw * lx + nyw * ly + nzw * lz))
                v = int(45 + 150 * lum)    # 灰阶: 暗 #2d.. 亮 #c3.. (金属对比)
                fill = "#%02x%02x%02x" % (v, v, v + 6)
                # 描边同填充色: 相邻条带共享边被同色覆盖, 无背景细缝
                # (Tk 多边形无抗锯齿, 异色边会透出半透明缝)
                self.canvas.create_polygon(screen, fill=fill, outline=fill,
                                           width=1, tags="toolmodel")

    # ------------- 刀具剖面图 -------------
    def show_tool_profile(self):
        """二级窗口: 刀具直径剖面图 (镜像 + 尺寸标注, 可拉伸)"""
        if not self.tool:
            return
        tool = self.tool
        win = tk.Toplevel(self)
        _set_icon(win)              # 二级窗口图标 (全局生效)
        win.title(f"刀具剖面图 — {tool_summary(tool)}")
        win.configure(bg=theme.BG)
        win.geometry("480x520")
        win.minsize(320, 360)
        cv = tk.Canvas(win, bg=theme.EDITOR_BG, highlightthickness=0)
        cv.pack(fill="both", expand=True, padx=8, pady=8)
        self._curve_job = None
        cv.bind("<Configure>", lambda e: self._profile_redraw(cv, tool, e))
        self._draw_tool_profile(cv, tool, cv.winfo_width() or 464, cv.winfo_height() or 504)

    def _profile_redraw(self, cv, tool, e):
        if self._curve_job is not None:
            self.after_cancel(self._curve_job)
        self._curve_job = self.after(60,
                                     lambda: self._draw_tool_profile(cv, tool, e.width, e.height))

    def _draw_tool_profile(self, cv, tool, W, H):
        """按给定尺寸绘制刀具剖面图 (镜像轮廓 + 尺寸标注, 含刀柄与总长)"""
        cv.delete("all")
        profile = tool_profile_points(tool)
        if not profile:
            return
        full = tool_full_profile(tool)          # 切削部分 + 刀柄 (反锥含缩颈)
        l = float(tool.p("l", 30.0))            # 刃长
        h = tool_overall_height(tool)           # 总长 (含刀柄)
        max_r = max(r for r, _ in full)
        # 画布缩放: 宽度含左右镜像, 高度为总长, 留标注边距
        pad = 64
        scale = min((W - 2 * pad) / (2 * max_r), (H - 2 * pad) / h)
        if scale <= 0:
            scale = 1.0
        ox = W / 2
        oy = H - pad

        def sx(r):
            return ox + r * scale

        def sy(y):
            return oy - y * scale

        # 镜像轮廓
        outline = [(0.0, 0.0)]
        outline += [(r, y) for r, y in full]
        outline += [(-r, y) for r, y in reversed(full)]
        coords = []
        for r, y in outline:
            coords.append(sx(r))
            coords.append(sy(y))
        cv.create_polygon(coords, fill="#4a4a52", outline="#c8c8c8", width=2)
        # 中心轴线 (虚线)
        cv.create_line(sx(0), sy(0), sx(0), sy(h), fill="#6e6e6e", dash=(3, 3))
        # 直径标注 (最大半径处)
        y_max = max(y for r, y in full if abs(r - max_r) < 1e-9)
        self._dim_h(cv, sx(-max_r), sy(y_max), sx(max_r), sy(y_max),
                    f"D{tool_summary(tool).split('D')[1].split()[0]}", "bottom")
        # 刃长标注 (右侧) 与总长标注 (左侧)
        self._dim_v(cv, sx(max_r), sy(0), sy(l), f"L{l:g}", "right")
        if h > l + 1e-9:
            self._dim_v(cv, sx(0), sy(0), sy(h), f"H{h:g}", "left")
        # 特征标注 (圆角/球头/顶角/锥角)
        kind = tool.kind
        if kind == "ball":
            br = float(tool.p("r"))
            cv.create_text(sx(br * 0.55), sy(br * 0.55) + 14, text=f"R{br:g}",
                           fill=theme.TEXT, font=theme.FONT_SMALL)
        elif kind == "flat" and tool.p("r", 0):
            cr = float(tool.p("r"))
            cv.create_text(sx(max_r - cr * 0.5), sy(cr * 0.5) + 14, text=f"R{cr:g}",
                           fill=theme.TEXT, font=theme.FONT_SMALL)
        elif kind in ("drill", "center"):
            cv.create_text(sx(0), sy(l) + 24, text=f"顶角{tool.p('point'):g}°",
                           fill=theme.TEXT, font=theme.FONT_SMALL)
        elif kind in ("invtaper", "taper"):
            cv.create_text(sx(max_r) + 40, sy(l / 2), text=f"θ{tool.p('taper'):g}°",
                           fill=theme.TEXT, font=theme.FONT_SMALL)
        cv.create_text(W / 2, 14, text=tool_summary(tool),
                       fill=theme.TEXT, font=theme.FONT_UI)

    def _dim_arrow(self, cv, x, y, dx, dy, size=7.0):
        """实心三角箭头 (工程制图): 尖端在 (x,y), 沿 (dx,dy) 方向张开 (半角 30°),
        即尖端朝 (dx,dy) 指向端部, 张开端在外侧"""
        ang = math.atan2(dy, dx)
        half = 0.52
        pts = [(x, y),
               (x + size * math.cos(ang - half), y + size * math.sin(ang - half)),
               (x + size * math.cos(ang + half), y + size * math.sin(ang + half))]
        cv.create_polygon(pts, fill=theme.TEXT_DIM, outline=theme.TEXT_DIM)

    def _dim_h(self, cv, x1, y1, x2, y2, text, side):
        """水平尺寸标注 (工程制图): 尺寸界线从轮廓引出, 尺寸线两端实心
        箭头朝外 (左端朝左/右端朝右), 文字在尺寸线上方居中"""
        yy = max(y1, y2) + 26 if side == "bottom" else min(y1, y2) - 26
        cv.create_line(x1, y1, x1, yy, fill=theme.TEXT_DIM)   # 界线
        cv.create_line(x2, y2, x2, yy, fill=theme.TEXT_DIM)
        cv.create_line(x1, yy, x2, yy, fill=theme.TEXT_DIM)   # 尺寸线
        self._dim_arrow(cv, x1, yy, -1, 0)                    # 左端朝左
        self._dim_arrow(cv, x2, yy, 1, 0)                     # 右端朝右
        cv.create_text((x1 + x2) / 2, yy - 10, text=text,
                       fill=theme.TEXT, font=theme.FONT_MONO)

    def _dim_v(self, cv, x, y1, y2, text, side):
        """垂直尺寸标注 (工程制图): 尺寸界线从轮廓引出, 实心箭头朝外
        (上端朝上/下端朝下), 文字在尺寸线左侧"""
        xx = x + 24 if side == "right" else x - 24
        cv.create_line(x, y1, xx, y1, fill=theme.TEXT_DIM)    # 界线
        cv.create_line(x, y2, xx, y2, fill=theme.TEXT_DIM)
        cv.create_line(xx, y1, xx, y2, fill=theme.TEXT_DIM)   # 尺寸线
        self._dim_arrow(cv, xx, y1, 0, -1)                    # 上端朝上
        self._dim_arrow(cv, xx, y2, 0, 1)                     # 下端朝下
        cv.create_text(xx - 10, (y1 + y2) / 2, text=text, anchor="e",
                       fill=theme.TEXT, font=theme.FONT_MONO)

    # ------------- 刀具自定义 -------------
    def show_tool_setup(self):
        """自定义窗口: 选择类型 -> 对应规格输入框 -> 应用/恢复自动解析"""
        win = tk.Toplevel(self)
        _set_icon(win)              # 二级窗口图标 (全局生效)
        win.title("刀具自定义")
        win.configure(bg=theme.BG)
        win.geometry("380x340")
        frm = ttk.Frame(win, padding=12, style="Panel.TFrame")
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        ttk.Label(frm, text="类型:", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        kind_var = tk.StringVar(value=TOOL_SPECS["flat"][0])
        preset = self.custom_tool or self.tool
        if preset:
            kind_var.set(TOOL_SPECS[preset.kind][0])
        kind_cb = ttk.Combobox(frm, textvariable=kind_var, state="readonly",
                               values=[v[0] for v in TOOL_SPECS.values()], width=24)
        kind_cb.grid(row=0, column=1, sticky="ew", padx=4)
        self._setup_win = win
        self._setup_kind_var = kind_var
        self._setup_kind_cb = kind_cb
        self._setup_fields = {}
        self._setup_entries = {}
        kind_cb.bind("<<ComboboxSelected>>",
                     lambda e: self._rebuild_tool_fields(frm, kind_var, preset))
        self._rebuild_tool_fields(frm, kind_var, preset)
        btns = ttk.Frame(frm, style="Panel.TFrame")
        btns.grid(row=99, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        ttk.Button(btns, text="应用", style=theme.BTN_ACCENT,
                   command=lambda: self._apply_tool_setup(win, kind_var)).pack(side="left")
        ttk.Button(btns, text="恢复自动解析",
                   command=lambda: self._clear_custom_tool(win)).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="取消", command=win.destroy).pack(side="left", padx=(8, 0))

    def _rebuild_tool_fields(self, frm, kind_var, preset):
        """按类型重建规格输入行"""
        for w in self._setup_fields.values():
            w.destroy()
        self._setup_fields = {}
        self._setup_entries = {}
        name = kind_var.get()
        kind = next(k for k, v in TOOL_SPECS.items() if v[0] == name)
        p = preset.params if preset and preset.kind == kind else {}
        for i, (key, label, default) in enumerate(TOOL_SPECS[kind][1], 1):
            row = i
            ttk.Label(frm, text=label, style="Panel.TLabel").grid(
                row=row, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=str(p.get(key, default)))
            ent = ttk.Entry(frm, textvariable=var, width=14)
            ent.grid(row=row, column=1, sticky="ew", padx=4)
            self._setup_fields[f"row_{key}"] = ent
            self._setup_entries[key] = var

    def _apply_tool_setup(self, win, kind_var):
        name = kind_var.get()
        kind = next(k for k, v in TOOL_SPECS.items() if v[0] == name)
        params = {}
        for key, var in self._setup_entries.items():
            try:
                params[key] = float(var.get())
            except ValueError:
                messagebox.showinfo("提示", f"字段 {key} 需要数值")
                return
        self.custom_tool = Tool(kind, params)
        self.tool = self.custom_tool
        self._refresh_tool_ui()
        win.destroy()

    def _clear_custom_tool(self, win):
        self.custom_tool = None
        self.tool = self._parsed_tool
        self._refresh_tool_ui()
        win.destroy()

    # ------------- 坐标变换 -------------
    def world_to_canvas(self, wx, wy):
        sx = wx * self.scale + self.offset[0]
        sy = -wy * self.scale + self.offset[1]
        return sx, sy

    def canvas_to_world(self, cx, cy):
        wx = (cx - self.offset[0]) / self.scale
        wy = -(cy - self.offset[1]) / self.scale
        return wx, wy

    # ------------- 渲染 -------------
    # ------------- 渲染预计算元数据 (性能优化: 热循环零字典/属性访问) -------------
    def _build_render_meta(self):
        """构建渲染预计算元数据 (visible/colors/merge_prev)。

        加载/段过滤/G0 开关/lead 变化时重建 (低频, 毫秒级), 热循环
        (render/_trace_draw_to) 每移动一次索引查表:
          visible[i]    : 移动是否参与渲染 (前导跳过/段过滤/隐藏 G0)
          colors[i]     : 预解析颜色串 (G0 灰 / 调色板)
          merge_prev[i] : 与上一个可见移动同色且共享端点 (折线合并,
                          渲染时跳过共享端点重投影)
        """
        self._render_meta = None
        if not self.result:
            return
        moves = self.result.moves
        n = len(moves)
        visible = [False] * n
        colors = [None] * n
        merge_prev = [False] * n
        show_g0 = self.show_g0.get()
        palette = self.palette
        lead = self._lead_skip
        prev_color = None
        prev_i = None
        for i in range(lead, n):
            m = moves[i]
            if m.motion == "G0" and not show_g0:
                continue
            if self._move_filtered(i):
                continue
            c = G0_COLOR if m.motion == "G0" else palette.get(m.feed, "#ffffff")
            visible[i] = True
            colors[i] = c
            if (prev_i is not None and c == prev_color
                    and moves[prev_i].end == m.start):
                merge_prev[i] = True
            prev_i = i
            prev_color = c
        self._render_meta = (visible, colors, merge_prev)

    def _on_show_g0_toggle(self):
        """显示 G0 开关: 可见性变化影响合并链, 重建元数据后刷新"""
        self._build_render_meta()
        self._view_refresh()

    def rotated_bbox(self):
        """当前旋转视角下, 所有刀路点投影后的 2D 包围盒 (a0,b0,a1,b1)。

        排除前导跳过段(从原点出发的起始进给), 使适配聚焦于实际加工区域;
        四元数/过滤未变时复用缓存, 避免重复全量投影。
        """
        q = self.quat
        key = (id(self.result), q, self._seg_filter, self._lead_skip)
        if self._bbox_cache is not None and self._bbox_cache[0] == key:
            return self._bbox_cache[1]
        a0 = b0 = float("inf")
        a1 = b1 = float("-inf")
        for i in range(self._lead_skip, len(self._disp3d)):
            if self._move_filtered(i):
                continue
            for p in self._disp3d[i]:
                a, b = project(p, q)
                if a < a0: a0 = a
                if b < b0: b0 = b
                if a > a1: a1 = a
                if b > b1: b1 = b
        if a0 == float("inf"):
            bbox = (0.0, 0.0, 1.0, 1.0)
        else:
            bbox = (a0, b0, a1, b1)
        self._bbox_cache = (key, bbox)
        return bbox

    def _view_refresh(self):
        """视图变换后的刷新: 轨迹模式重投影已画轨迹, 否则全量渲染"""
        if self._trace_active:
            self._trace_redraw()
        else:
            self.render()

    def _view_refresh_soon(self):
        """合并连续视图刷新请求 (旋转/滚转拖动逐事件全量渲染会积压掉帧)"""
        if self._refresh_job is None:
            def run():
                self._refresh_job = None
                self._view_refresh()
            self._refresh_job = self.after_idle(run)

    def fit_view(self):
        if not self.result or not self.result.moves:
            return
        cw = max(self.canvas.winfo_width(), 100)
        ch = max(self.canvas.winfo_height(), 100)
        a0, b0, a1, b1 = self.rotated_bbox()
        w = (a1 - a0) or 1.0
        h = (b1 - b0) or 1.0
        margin = 40
        scale = min((cw - 2 * margin) / w, (ch - 2 * margin) / h)
        if scale <= 0:
            scale = 1.0
        ox = (cw - (a0 + a1) * scale) / 2
        oy = (ch + (b0 + b1) * scale) / 2
        self.scale = scale
        self.offset = (ox, oy)
        self._view_refresh()

    def set_view_preset(self, name):
        self.quat = VIEW_QUAT[name]
        self.fit_view()

    def zoom_at(self, factor, anchor):
        # anchor = (canvas_x, canvas_y) or None(中心)
        cw = max(self.canvas.winfo_width(), 100)
        ch = max(self.canvas.winfo_height(), 100)
        if anchor is None:
            anchor = (cw / 2, ch / 2)
        mx, my = anchor
        wx, wy = self.canvas_to_world(mx, my)
        new_scale = self.scale * factor
        if new_scale < 1e-6:
            return
        ox = mx - wx * new_scale
        oy = my + wy * new_scale
        self.scale = new_scale
        self.offset = (ox, oy)
        if self.result:
            # 原位缩放全部图元 (C 级操作), 免重投影/重建
            self.canvas.scale("all", mx, my, factor, factor)
            # 同步轨迹存储坐标 (平移/缩放后追加保持一致)
            if self._trace_active:
                for _, _, flat in self._trace_items:
                    for i in range(0, len(flat), 2):
                        flat[i] = mx + (flat[i] - mx) * factor
                        flat[i + 1] = my + (flat[i + 1] - my) * factor
            self._place_legend()
            # 屏幕像素尺寸的标记类图元不随缩放变化: 重绘
            # (方向箭头固定 14px / 当前点 6px / 十字虚线铺满可视区 /
            #  坐标系与左下角指示器固定像素)
            self.canvas.delete("cur", "curseg", "toolmodel", "axes", "axescorner")
            self._draw_current()
            self._draw_tool_model()
            self._draw_axes()

    def render(self):
        self._trace_active = False        # 全量渲染即退出轨迹演示
        if not self.result or not self.result.moves:
            self.canvas.delete("all")
            self._path_items = []
            self._poly_struct = None
            return
        w, x, y, z = self.quat
        scale, ox, oy = self.scale, self.offset[0], self.offset[1]
        show_g0 = self.show_g0.get()
        palette = self.palette
        # 预计算四元数系数 (省去每点 2 倍乘)
        a2, b2, c2 = 2.0 * y, 2.0 * z, 2.0 * x

        key = (id(self.result), w, x, y, z, self._seg_filter, show_g0,
               self._lead_skip)
        if self._proj_cache is not None and self._proj_cache[0] == key:
            # 四元数/过滤未变: 复用缓存投影, 仅重算缩放偏移 (缩放提速)
            polylines = []
            for color, base in self._proj_cache[1]:
                coords = [0.0] * (len(base))
                for i in range(0, len(base), 2):
                    coords[i] = base[i] * scale + ox
                    coords[i + 1] = -base[i + 1] * scale + oy
                polylines.append([color, coords])
        else:
            # 内联四元数投影 + 预计算元数据驱动 (可见性/颜色/合并查表)
            polylines = []
            base_store = []      # 缓存: 不含缩放偏移的世界投影坐标
            moves = self.result.moves
            disp3d = self._disp3d
            n_moves = len(moves)
            seg_filter = self._seg_filter
            lead = self._lead_skip
            meta = self._render_meta
            if meta is None:             # 防御: 未初始化时现算
                self._build_render_meta()
                meta = self._render_meta
            if seg_filter is not None and not seg_filter:
                base_store = []          # 空过滤: 无刀路可画
            else:
                vis, cols, merg = meta
                drag = (self._rot_data is not None)   # 拖动: 不写 base 缓存
                for i in range(lead, n_moves):
                    if not vis[i]:
                        continue
                    color = cols[i]
                    pts3d = disp3d[i]
                    n = len(pts3d)
                    if merg[i] and polylines:
                        # 合并: 共享端点已在折线尾部, 跳过首点重投影
                        poly = polylines[-1][1]
                        base = base_store[-1][1] if not drag else None
                        start = 1
                    else:
                        poly = []
                        polylines.append([color, poly])
                        base = [] if not drag else None
                        if not drag:
                            base_store.append([color, base])
                        start = 0
                    ap = poly.append
                    if drag:
                        for i2 in range(start, n):
                            px, py, pz = pts3d[i2]
                            tx = a2 * pz - b2 * py
                            ty = b2 * px - c2 * pz
                            tz = c2 * py - a2 * px
                            vx = px + w * tx + y * tz - z * ty
                            vy = py + w * ty + z * tx - x * tz
                            ap(vx * scale + ox)
                            ap(-vy * scale + oy)
                    else:
                        bp = base.append
                        for i2 in range(start, n):
                            px, py, pz = pts3d[i2]
                            tx = a2 * pz - b2 * py
                            ty = b2 * px - c2 * pz
                            tz = c2 * py - a2 * px
                            vx = px + w * tx + y * tz - z * ty
                            vy = py + w * ty + z * tx - x * tz
                            ap(vx * scale + ox)
                            ap(-vy * scale + oy)
                            bp(vx)
                            bp(vy)
            # 旋转拖动中 quat 每帧变化, 缓存命中不了, 不写 (省 25k 条坐标拷贝)
            if self._rot_data is None:
                self._proj_cache = (key, base_store)

        polylines = [pl for pl in polylines if len(pl[1]) >= 4]
        # 结构未变 (同文件/过滤/G0/抽稀档位): 复用画布图元仅 coords 更新,
        # 免 delete/create 开销 (25k 段实测 31ms -> 8ms)
        struct = [(c, len(pts)) for c, pts in polylines]
        if struct == self._poly_struct:
            for item, (color, pts) in zip(self._path_items, polylines):
                self.canvas.coords(item, *pts)
        else:
            self.canvas.delete("path")
            items = []
            ap = items.append
            for color, pts in polylines:
                ap(self.canvas.create_line(pts, fill=color, width=1,
                                           joinstyle="round", capstyle="round",
                                           tags="path"))
            self._path_items = items
            self._poly_struct = struct

        # 标记类图元数量小, 删除重建; 旋转拖动中跳过 (每帧重建 ~15ms,
        # 刀路保持完整, 标记在释放后的最终帧恢复)
        self.canvas.delete("axes", "axescorner", "cur", "curseg", "toolmodel")
        if self._rot_data is None:
            self._draw_current()
            self._draw_tool_model()
            self._draw_axes()          # 最后画: 不被十字线/当前行高亮/刀具覆盖
            self.status.config(text=self._status_text())
        # 图例窗口项只在画布尺寸变化时重贴 (_on_canvas_configure),
        # 每次重建 ~12ms 是拖动帧率杀手

    def _place_legend(self):
        """在画布右上角重建漂浮图例窗口项 (先清旧项防重复)"""
        try:
            self.canvas.delete("legend")
            w = self.canvas.winfo_width()
            self.canvas.create_window((w - 10, 10), window=self.legend,
                                      anchor="ne", tags="legend")
        except tk.TclError:
            pass

    def _draw_axes(self):
        """原点 (0,0,0) 大坐标系 (60px) + 画布左下角小指示器 (34px)。

        原点坐标系 (axes): 三轴 + 轴端箭头 + 加粗标签 + 原点白圈, 锚定
        世界原点, 随平移/旋转/缩放变化; 左下角指示器 (axescorner): 三轴 +
        箭头 + 标签, 锚定画布左下角, 只随旋转变化 (模型适配后原点常贴
        画布边缘/出画布, 作保底方向参照)。尺寸固定像素 (60 > 34)。
        """
        q = self.quat
        axes = (("X", "#ff5555", (1, 0, 0)),
                ("Y", "#55ff55", (0, 1, 0)),
                ("Z", "#55ffff", (0, 0, 1)))
        # 原点坐标系: 世界原点投影 + 旋转后单位方向像素偏移
        ox, oy = self.world_to_canvas(*project((0, 0, 0), q))
        for axis, color, vec in axes:
            a, b = project(vec, q)                  # 旋转后单位方向
            ex, ey = ox + a * 90, oy - b * 90
            self.canvas.create_line(ox, oy, ex, ey, fill=color, width=2,
                                    tags="axes")
            self._arrow_at(ex, ey, a * 48, b * 48, color=color, size=12,
                           tags="axes")
            self.canvas.create_text(ex + a * 18, ey - b * 18, text=axis,
                                    fill=color, font=("", 11, "bold"),
                                    tags="axes")
        self.canvas.create_oval(ox - 5, oy - 5, ox + 5, oy + 5,
                                outline="#ffffff", width=1, tags="axes")
        # 左下角指示器: 固定画布左下角, 只随旋转变化 (不随平移/缩放)
        ch = self.canvas.winfo_height()
        cx, cy = 62, ch - 56
        for axis, color, vec in axes:
            a, b = project(vec, q)
            ex, ey = cx + a * 34, cy - b * 34
            self.canvas.create_line(cx, cy, ex, ey, fill=color, width=2,
                                    tags="axescorner")
            self._arrow_at(ex, ey, a * 26, b * 26, color=color, size=9,
                           tags="axescorner")
            self.canvas.create_text(ex + a * 13, ey - b * 13, text=axis,
                                    fill=color, font=("", 10, "bold"),
                                    tags="axescorner")

    def _draw_current(self):
        if self.current_line is None:
            return
        q = self.quat
        pos = self.result.position_at_line(self.current_line)
        a, b = project(pos, q)
        cx, cy = self.world_to_canvas(a, b)

        # 当前段高亮 + 加工方向箭头 (前导跳过段/被段过滤的移动不高亮, 防残留)
        m = self.move_by_line.get(self.current_line)
        idx = None
        if m is not None:
            if m.motion == "G0" and not self.show_g0.get():
                m = None                 # 隐藏 G0 不高亮 (idx 不参与)
            else:
                idx = self._move_index[id(m)]
                if idx < self._lead_skip or self._move_filtered(idx):
                    m = None
        if m is not None:
            pts = [self.world_to_canvas(*project(p, q)) for p in self._disp3d[idx]]
            if len(pts) >= 2:
                self.canvas.create_line(pts, fill=SEG_COLOR, width=3,
                                        tags="curseg")
                self._draw_move_direction(pts)

        # 十字线 (铺满可视区; 平移后由 _draw_crosshair 重铺)
        self._draw_crosshair(cx, cy)
        # 当前点
        self.canvas.create_oval(cx - 6, cy - 6, cx + 6, cy + 6,
                                fill=CUR_COLOR, outline="#000000", width=2, tags="cur")

    def _draw_crosshair(self, cx, cy):
        """当前位置十字虚线: 铺满可视区两端 (平移把图元移出边界, 需重铺)"""
        self.canvas.delete("curx")
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        self.canvas.create_line(cx, 0, cx, ch, fill=CUR_LINE_COLOR,
                                dash=(4, 4), tags=("cur", "curx"))
        self.canvas.create_line(0, cy, cw, cy, fill=CUR_LINE_COLOR,
                                dash=(4, 4), tags=("cur", "curx"))

    def _draw_move_direction(self, pts):
        """沿当前段路径绘制箭头, 表示刀具加工方向。

        pts 为当前段的屏幕坐标点序列。直线段画 1 个箭头, 圆弧/曲线沿路径
        画 3 个箭头, 方向始终指向刀具行进方向。
        """
        n = len(pts)
        if n < 2:
            return
        # 采样位置(0~1 表示沿段的相对位置)
        if n == 2:
            t_vals = [0.6]
        else:
            t_vals = [0.25, 0.5, 0.75]
        samples = []
        for frac in t_vals:
            seg = frac * (n - 1)
            i = int(seg)
            t = seg - i
            j = min(i + 1, n - 1)
            samples.append((pts[i][0] + (pts[j][0] - pts[i][0]) * t,
                            pts[i][1] + (pts[j][1] - pts[i][1]) * t))
        for k, (x, y) in enumerate(samples):
            if k + 1 < len(samples):
                nx, ny = samples[k + 1]
            else:
                nx, ny = pts[-1]
            self._arrow_at(x, y, nx - x, ny - y)

    def _arrow_at(self, x, y, dx, dy, color=CUR_COLOR, size=28, tags="cur"):
        """在 (x,y) 处画一个指向 (dx,dy) 方向的镖形箭头。

        镖形 (尖端 + 双翼 + 尾部内凹) 比纯三角箭头更饱满精致,
        深色描边保证任何背景下醒目; 默认尺寸 28px (刀路方向醒目)。
        """
        L = math.hypot(dx, dy)
        if L < 1e-6:
            return
        ux, uy = dx / L, dy / L
        px, py = -uy, ux                     # 垂直方向(箭头翼)
        size = min(size, L * 0.5)            # 避免过短段上箭头过大
        half = size * 0.62                   # 翼展
        tail = size * 1.0                    # 尾长
        notch = size * 0.32                  # 尾部内凹深度
        self.canvas.create_polygon(
            x, y,                            # 尖端
            x - ux * tail + px * half, y - uy * tail + py * half,
            x - ux * (tail - notch), y - uy * (tail - notch),
            x - ux * tail - px * half, y - uy * tail - py * half,
            fill=color, outline="#000000", width=1.5, tags=tags)

    # ------------- 交互: 平移/缩放 + 点击拾取 -------------
    PICK_MAX_PX = 12.0        # 拾取/旋转吸附模型点的最大像素距离
    CLICK_MAX_MOVE_PX = 4.0   # 按下与释放位移在此内视为单击 (而非拖动平移)

    def _pan_start(self, e):
        # 记录起点、原始偏移、上一次位置(用于增量位移)
        self._pan_data = (e.x, e.y, self.offset[0], self.offset[1], e.x, e.y)
        self._pan_moved = False          # 首次真正移动时再切粗图元 (单击不切)

    def _pan_move(self, e):
        if not self._pan_data:
            return
        sx, sy, ox, oy, px, py = self._pan_data
        dx, dy = e.x - px, e.y - py
        if not self._pan_moved and (dx or dy):
            self._pan_moved = True
        # 增量位移全部图元(原生 C 操作, 远快于重绘), 同时更新偏移供下次缩放重绘
        self.canvas.move("all", dx, dy)
        # 漂浮图例窗口项补偿: 保持画布右上角固定
        self.canvas.move("legend", -dx, -dy)
        # 左下角指示器锚定画布: 反向补偿 (图例同模式)
        self.canvas.move("axescorner", -dx, -dy)
        if self._trace_active:
            # 轨迹存储坐标同步位移, 否则后续追加新点会混用新旧坐标系导致错乱
            for _, _, flat in self._trace_items:
                for i in range(0, len(flat), 2):
                    flat[i] += dx
                    flat[i + 1] += dy
        self.offset = (ox + (e.x - sx), oy + (e.y - sy))
        self._pan_data = (sx, sy, ox, oy, e.x, e.y)
        if self.result and self.current_line is not None:
            # 十字虚线被平移移出可视区: 按当前位置重铺到两端
            a, b = project(self.result.position_at_line(self.current_line),
                           self.quat)
            self._draw_crosshair(*self.world_to_canvas(a, b))

    def _pan_end(self, e):
        moved = self._pan_data is not None and self._pan_data[0] != self._pan_data[1]
        if self._pan_data:
            sx, sy = self._pan_data[0], self._pan_data[1]
            if max(abs(e.x - sx), abs(e.y - sy)) <= self.CLICK_MAX_MOVE_PX:
                self._click_pick(e.x, e.y)     # 单击 (未拖动): 拾取刀路跳转
        self._pan_data = None

    def _click_pick(self, mx, my):
        """画布单击拾取。命中刀路: 轨迹/播放状态仅查看对应行 (代码高亮+
        位置字段, 执行位置不动, 续播从停下处继续), 非轨迹状态跳转到该行。
        未命中: 轨迹状态取消查看 (执行位置保留), 否则清除当前选择。"""
        idx = self._pick_move_index(mx, my)
        if idx is None:
            if self._trace_active:
                self._cancel_view()
            else:
                self._clear_current()
            return
        ln = self.result.moves[idx].line_number
        self._set_pick_time(ln)          # 统计加工时间显示该刀路所在段时间
        if self._trace_active:
            self._view_line(ln)
        else:
            self.set_current_line(ln)

    def _view_line(self, ln):
        """仅查看某行 (不改变执行位置): 代码区查看高亮 + 位置字段显示其值"""
        self.code.tag_remove("viewline", "1.0", "end")
        self.code.tag_add("viewline", "%d.0" % ln, "%d.end" % ln)
        self.code.see("%d.0" % ln)
        self._update_pos_info(ln)

    def _cancel_view(self):
        """取消查看行 (轨迹模式点空白): 清除查看高亮, 字段回到执行位置"""
        self.code.tag_remove("viewline", "1.0", "end")
        self._reset_pick_time()           # 加工时间恢复默认 (勾选段/全程序)
        if self.current_line is not None:
            self._update_pos_info(self.current_line)

    def _clear_current(self):
        """清除当前位置选择: 画布标记 (十字/高亮/箭头) /位置字段/代码高亮"""
        self.current_line = None
        self.canvas.delete("cur", "curseg", "toolmodel")
        self._draw_tool_model()           # 无当前行时刀具模型回退到原点显示
        self.code.tag_remove("cur", "1.0", "end")
        self.code.tag_remove("viewline", "1.0", "end")
        self._reset_pick_time()           # 加工时间恢复默认 (勾选段/全程序)
        for key in ("X", "Y", "Z", "S", "F", "G", "行", "段", "本行"):
            self._set_field(self.pos_fields[key], "-")
        self.loc_entry.delete(0, "end")

    def _pick_move_index(self, mx, my, max_dist=PICK_MAX_PX):
        """拾取距 (mx,my) max_dist 像素内最近的可见刀路移动, 返回移动索引或 None。

        按到折线段的距离判定 (非仅顶点), 长直线段中点也可点中;
        前导跳过段/段过滤/隐藏 G0 不参与拾取; 轨迹模式只能点中已绘制部分
        (画面内没有的刀路不能被选中)。
        """
        if not self.result:
            return None
        w, x, y, z = self.quat
        scale, ox, oy = self.scale, self.offset[0], self.offset[1]
        a2, b2, c2 = 2.0 * y, 2.0 * z, 2.0 * x   # 预计算四元数系数
        show_g0 = self.show_g0.get()
        best = None
        best_sq = max_dist * max_dist
        moves = self.result.moves
        limit = len(self._disp3d)
        if self._trace_active:
            limit = min(limit, self._trace_drawn)   # 仅已绘制部分可点
        for i in range(self._lead_skip, limit):
            if self._move_filtered(i):
                continue
            m = moves[i]
            if m.motion == "G0" and not show_g0:
                continue
            prev_sx = prev_sy = None
            for px, py, pz in self._disp3d[i]:
                tx = a2 * pz - b2 * py
                ty = b2 * px - c2 * pz
                tz = c2 * py - a2 * px
                vx = px + w * tx + y * tz - z * ty
                vy = py + w * ty + z * tx - x * tz
                sx = vx * scale + ox
                sy = -vy * scale + oy
                if prev_sx is not None:
                    d = point_seg_dist_sq(mx, my, prev_sx, prev_sy, sx, sy)
                    if d < best_sq:
                        best_sq = d
                        best = i
                        if d < 0.25:      # 0.5px 内直接命中, 提前结束
                            return best
                prev_sx, prev_sy = sx, sy
        return best

    # ------------- 交互: 中键旋转 (轨道球, CATIA 风格) -------------
    def _pick_model_point(self, mx, my, max_dist=PICK_MAX_PX):
        """拾取鼠标位置 max_dist 像素内最近的模型点; 若无则返回 None。

        仅当点击处确实落在刀路上才返回模型点, 否则回退到点击处锚定,
        避免旋转中心被吸附到远处模型点导致"发飘"。
        """
        q = self.quat
        scale, ox, oy = self.scale, self.offset[0], self.offset[1]
        best = None
        best_sq = max_dist * max_dist
        for pts3d in self._disp3d:
            for p in pts3d:
                x1, y2, _ = quat_rotate(p, q)
                sx = x1 * scale + ox
                sy = -y2 * scale + oy
                dx = sx - mx
                dy = sy - my
                d = dx * dx + dy * dy
                if d < best_sq:
                    best_sq = d
                    best = p
        return best

    def _rot_start(self, e):
        if not self.result:
            return
        # 吸附到刀路点 -> 轨道旋转; 未吸附 (空白处) -> 平面旋转 (滚转, 绕画布中心)
        c = self._pick_model_point(e.x, e.y)
        if c is None:
            cx = max(self.canvas.winfo_width(), 1) / 2
            cy = max(self.canvas.winfo_height(), 1) / 2
            wx, wy = self.canvas_to_world(cx, cy)
            *_, minz, ___, maxz = self.result.bbox
            c = (wx, wy, (minz + maxz) / 2)
            self._rot_mode = "roll"
            self._rot_anchor = (cx, cy)        # 滚转锚点 = 画布中心
        else:
            self._rot_mode = "orbit"
        self._rot_center = c
        self._rot_data = (e.x, e.y)   # 记录上一鼠标位置, 用于计算增量拖动
        self._rot_moved = False

    def _rot_move(self, e):
        if self._rot_data is None:
            return
        px, py = self._rot_data
        if self._rot_mode == "roll":
            self._roll_move(e, px, py)
            return
        dx, dy = e.x - px, e.y - py
        if dx == 0 and dy == 0:
            return
        # 屏幕轴轨道旋转: 前乘视空间增量, 方向天然跟手(右拖右转/下拖下转)
        new_quat = orbit_rotate(self.quat, dx, dy, sens=0.01)
        # 补偿画布偏移, 使旋转中心在屏幕上绝对不动
        self.offset = compensate_center(self._rot_center, self.quat, new_quat,
                                        self.scale, self.offset)
        self.quat = new_quat
        self._rot_data = (e.x, e.y)
        self._rot_moved = True
        self._view_refresh_soon()

    def _roll_move(self, e, px, py):
        """平面旋转 (滚转): 画面绕画布中心跟随鼠标转角转动, 中心固定不动"""
        ax, ay = self._rot_anchor
        v1x, v1y = px - ax, py - ay
        v2x, v2y = e.x - ax, e.y - ay
        if math.hypot(v1x, v1y) < 10 or math.hypot(v2x, v2y) < 10:
            self._rot_data = (e.x, e.y)      # 距锚点太近角度不稳, 只更新位置
            return
        # 屏幕(y 向下)转角取负 -> 视觉同向 (画面跟着鼠标绕锚点转)
        d = -(math.atan2(v2y, v2x) - math.atan2(v1y, v1x))
        if d > math.pi:
            d -= 2 * math.pi
        elif d < -math.pi:
            d += 2 * math.pi
        dq = quat_from_axis_angle(0.0, 0.0, 1.0, d)   # 绕视轴 (屏幕法向)
        new_quat = quat_normalize(quat_mul(dq, self.quat))
        self.offset = compensate_center(self._rot_center, self.quat, new_quat,
                                        self.scale, self.offset)
        self.quat = new_quat
        self._rot_data = (e.x, e.y)
        self._rot_moved = True
        self._view_refresh_soon()

    def _rot_end(self, e):
        moved = self._rot_moved
        self._rot_data = None
        self._rot_moved = False
        if self._refresh_job is not None:
            self.after_cancel(self._refresh_job)
            self._refresh_job = None
        if moved and self.result:
            self._view_refresh()              # 释放后的最终全量帧

    def _on_wheel(self, e):
        factor = 1.2 if e.delta > 0 else 1 / 1.2
        # 滚轮缩放: canvas.scale 原位缩放全部图元 (C 级操作), 刀路保持完整
        self.zoom_at(factor, (e.x, e.y))

    # ------------- 行定位 -------------
    def jump_to_input(self):
        if not self.result:
            return
        txt = self.loc_entry.get().strip()
        if not txt:
            return
        if txt.upper().startswith("N"):
            try:
                n = int(txt[1:])
            except ValueError:
                messagebox.showinfo("提示", "N号格式无效, 例如 N6")
                return
            ln = self.result.line_for_n(n)
            if ln is None:
                messagebox.showinfo("提示", f"未找到 N{n}")
                return
            self.set_current_line(ln)
        else:
            try:
                ln = int(txt)
            except ValueError:
                messagebox.showinfo("提示", "请输入行号(如 12)或 N号(如 N6)")
                return
            ln = max(1, min(ln, len(self.lines)))
            self.set_current_line(ln)

    def step_line(self, delta):
        if not self.result:
            return
        base = self.current_line if self.current_line else 1
        ln = max(1, min(len(self.lines), base + delta))
        self.set_current_line(ln)

    def set_current_line(self, ln, animate=False):
        """定位到指定行。animate=True 时走轻量路径 (轨迹渐进 + 只更新
        当前行标记, 不重画整幅刀路), 供播放/演示逐帧使用。"""
        self.current_line = ln
        if animate:
            if self._trace_active:
                self._trace_draw_to_line(ln)
            self.canvas.delete("cur", "curseg", "toolmodel")
            self._draw_current()
            self._draw_tool_model()
        else:
            self.render()
        self._update_pos_info(ln)
        self._highlight_code_line(ln, center=animate)   # 播放时当前行居中
        self.loc_entry.delete(0, "end")
        self.loc_entry.insert(0, str(ln))

    @staticmethod
    def _set_field(ent, text):
        ent.config(state="normal")
        ent.delete(0, "end")
        ent.insert(0, text)
        ent.config(state="readonly")

    def _update_pos_info(self, ln):
        pos = self.result.position_at_line(ln)
        for axis, v in zip(("X", "Y", "Z"), pos):
            self._set_field(self.pos_fields[axis], f"{v:.3f}")
        m = self.move_by_line.get(ln)
        self._set_field(self.pos_fields["S"],
                        f"{m.s:g}" if m and m.s is not None else "-")
        self._set_field(self.pos_fields["F"],
                        f"{m.feed:g}" if m and m.feed is not None else "-")
        self._set_field(self.pos_fields["G"], m.motion if m else "-")
        self._set_field(self.pos_fields["行"], str(ln))
        seg_no = self._segment_for_line(ln)
        self._set_field(self.pos_fields["段"], str(seg_no) if seg_no else "-")
        line_text = self.lines[ln - 1] if 1 <= ln <= len(self.lines) else ""
        self._set_field(self.pos_fields["本行"], line_text)

    def _highlight_code_line(self, ln, center=False):
        self.code.tag_remove("cur", "1.0", "end")
        self.code.tag_remove("viewline", "1.0", "end")   # 执行位置移动, 查看高亮失效
        start = f"{ln}.0"
        end = f"{ln}.end"
        self.code.tag_add("cur", start, end)
        if center:
            # 播放中当前行保持在代码区中间 (开头/结尾不足半屏时自然贴边)
            if self._code_line_h is None:
                self._code_line_h = tkfont.Font(
                    font=self.code["font"]).metrics("linespace")
            vis = max(1, int(self.code.winfo_height() / self._code_line_h))
            total = max(1, len(self.lines))
            self.code.yview_moveto(max(0.0, (ln - vis / 2.0) / total))
        else:
            self.code.see(start)
        self.code.mark_set("insert", start)

    # ------------- 搜索定位 -------------
    def search_nc(self, next_=False):
        if not self.result:
            return
        pattern = self._search_entry.get().strip()
        if not pattern:
            return
        if pattern != self._search_pattern:
            self._search_pattern = pattern
            self._search_hits = [i for i, line in enumerate(self.lines, 1)
                                 if pattern in line]
            self._search_idx = -1
            self._apply_search_tags()
            if not self._search_hits:
                messagebox.showinfo("搜索", f"未找到包含 “{pattern}” 的行")
                return
        if not self._search_hits:
            return
        if not next_ and self._search_idx >= 0:
            self._search_idx = -1          # 非"下一个"时从头开始
        self._search_idx = (self._search_idx + 1) % len(self._search_hits)
        ln = self._search_hits[self._search_idx]
        self.set_current_line(ln)
        self._highlight_search_current()

    def _apply_search_tags(self):
        self.code.tag_remove("search", "1.0", "end")
        self.code.tag_remove("searchcur", "1.0", "end")
        for ln in self._search_hits:
            self.code.tag_add("search", f"{ln}.0", f"{ln}.end")

    def _highlight_search_current(self):
        self.code.tag_remove("searchcur", "1.0", "end")
        ln = self._search_hits[self._search_idx]
        self.code.tag_add("searchcur", f"{ln}.0", f"{ln}.end")

    def _on_code_click(self, e):
        if not self.result:
            return
        try:
            idx = self.code.index(f"@{e.x},{e.y}")
        except Exception:
            return
        ln = int(idx.split(".")[0])
        ln = max(1, min(ln, len(self.lines)))
        self._toggle_target(ln)

    def _toggle_target(self, ln):
        """点击代码行: 选中目标行; 再点同一行则取消选中"""
        if self._target_line == ln:
            self._clear_target()
        else:
            self._set_target(ln)

    def _set_target(self, ln):
        """点击代码行 = 选中目标行 (执行位置不动, 目标行高亮)"""
        self._target_line = ln
        self.code.tag_remove("target", "1.0", "end")
        self.code.tag_add("target", f"{ln}.0", f"{ln}.end")
        self.code.tag_raise("target")
        self.target_lbl.config(text=f"目标行: {ln}")
        self.target_clear_btn.config(state="normal")

    def _clear_target(self):
        """清空目标行 (再点同一行或「清空」按钮)"""
        self._target_line = None
        self.code.tag_remove("target", "1.0", "end")
        self.target_lbl.config(text="目标行: -")
        self.target_clear_btn.config(state="disabled")

    # ------------- 分段 (按段浏览) -------------
    def _recompute_segments(self):
        """重算分段 (文件加载或抬刀平面修改时), 并重建代码区段号标注"""
        if not self.result:
            self._segments = []
            self._lift_plane = 0.0
            self._seg_index = None
            self._seg_of_line = {}
        else:
            lift = (compute_lift_plane(self.result.moves) if self._lift_auto
                    else self._lift_plane)
            self._lift_plane = lift
            self._segments = compute_segments(self.result.moves, lift)
            if self._segments and self._seg_index is None:
                self._seg_index = 0
            elif self._seg_index is not None and self._seg_index >= len(self._segments):
                self._seg_index = len(self._segments) - 1 if self._segments else None
            seg_of_line = {}
            for n, s in enumerate(self._segments, 1):
                for ln in range(s.start_line, s.end_line + 1):
                    seg_of_line[ln] = n
            self._seg_of_line = seg_of_line
        self._update_seg_ui()
        self._refresh_code_segments()
        self._apply_seg_filter()

    def _refresh_code_segments(self):
        """分段变化后重建代码区 (行号槽段号标注), 恢复当前行/目标行/搜索高亮"""
        if not self.lines:
            return
        self._fill_code()
        if self.current_line is not None:
            self._highlight_code_line(self.current_line)
        if self._target_line is not None:
            self.code.tag_add("target", "%d.0" % self._target_line,
                              "%d.end" % self._target_line)
        if self._search_pattern:
            self._apply_search_tags()
            if self._search_idx >= 0:
                self._highlight_search_current()

    def _seg_line_bounds(self):
        """段模式下勾选段的并集行范围; 无段模式返回全局"""
        if self._seg_filter is not None:
            segs = [self._segments[i] for i in self._checked_segments()
                    if i < len(self._segments)]
            if segs:
                return (min(s.start_line for s in segs),
                        max(s.end_line for s in segs))
            return (1, 1)          # 全不选: 空范围
        return (1, len(self.lines)) if self.result else (1, 1)

    def _move_filtered(self, i):
        """段模式过滤: 返回 True 表示应跳过该移动"""
        if self._seg_filter is None:
            return False
        if not self._seg_filter:          # 空列表: 全部跳过
            return True
        return not any(lo <= i <= hi for lo, hi in self._seg_filter)

    def _checked_segments(self):
        """勾选的段索引列表 (排序, 忽略上个程序残留的越界索引)"""
        return sorted(i for i in self._seg_checked if i < len(self._segments))

    def _sync_checked_from_listbox(self):
        self._seg_checked = set(self.seg_listbox.curselection())

    def _segment_for_line(self, ln):
        """当前行所属段号 (1 基), 无则 0"""
        for i, s in enumerate(self._segments, 1):
            if s.start_line <= ln <= s.end_line:
                return i
        return 0

    def _update_seg_ui(self, scroll_to=True):
        """刷新按段浏览面板 (含所有段列表); scroll_to=False 用于列表点击路径
        (列表保持滚动位置, 不再 see 到当前段)"""
        n = len(self._segments)
        if n and self._seg_index is not None:
            seg = self._segments[self._seg_index]
            self.seg_lbl.config(text=f"段 {self._seg_index + 1}/{n}")
            self.seg_info.config(
                text=f"行 {seg.start_line}~{seg.end_line} · Z 最低 {seg.z_min:g}")
        else:
            self.seg_lbl.config(text="段 -/-")
            self.seg_info.config(text="")
        self.lift_entry.delete(0, "end")
        self.lift_entry.insert(0, f"{self._lift_plane:g}")
        # 所有段列表 (多选勾选, 点击切换勾选; 重建时保留勾选状态)
        self._refresh_seg_list_marks()
        if scroll_to and n and self._seg_index is not None:
            self.seg_listbox.see(self._seg_index)

    def _on_seg_list_select(self, e):
        """段列表点击: 勾选切换; 新勾选的段成为当前段; 段模式实时刷新"""
        self._sync_checked_from_listbox()
        self._refresh_seg_list_marks()
        if self._seg_checked:
            self._seg_index = min(self._seg_checked)
        if self._seg_only.get():
            self._stop_playback()
            self._apply_seg_filter()
            self._update_seg_ui(scroll_to=False)

    def _set_checked(self, idx, on=True):
        """设置段勾选状态"""
        if on:
            self._seg_checked.add(idx)
        else:
            self._seg_checked.discard(idx)
        self._refresh_seg_list_marks()

    def _clear_seg_selection(self):
        """取消所有段的勾选"""
        self._seg_checked.clear()
        self._refresh_seg_list_marks()
        if self._seg_only.get():
            self._stop_playback()
            self._apply_seg_filter()
            self._update_seg_ui(scroll_to=False)

    def _refresh_seg_list_marks(self):
        """按勾选状态重建段列表 [x]/[ ] 标记 (Listbox 无 text itemconfig)。
        重建前后保持滚动位置, 否则点击后列表跳走、再次点击落错行"""
        ytop = self.seg_listbox.yview()[0]
        self.seg_listbox.delete(0, "end")
        for i, s in enumerate(self._segments):
            mark = "[x]" if i in self._seg_checked else "[ ]"
            self.seg_listbox.insert(
                "end", f"{mark} {i + 1}: 行{s.start_line}~{s.end_line} Z{s.z_min:g}")
        for i in self._seg_checked:
            if i < len(self._segments):
                self.seg_listbox.selection_set(i)
        self.seg_listbox.yview_moveto(ytop)

    def _apply_lift(self):
        """用户修改抬刀平面并自动重算分段"""
        if not self.result:
            return
        try:
            lift = float(self.lift_entry.get())
        except ValueError:
            messagebox.showinfo("提示", "抬刀平面需为数值")
            return
        self._lift_auto = False
        self._lift_plane = lift
        self._recompute_segments()

    def _auto_lift(self):
        """恢复自动检测抬刀平面"""
        self._lift_auto = True
        self._recompute_segments()

    def _apply_seg_filter(self, refresh=True):
        """根据「仅显示勾选段」设置渲染过滤 (勾选段并集; 全选=不过滤, 全不选=空)"""
        old_filter = self._seg_filter
        if self._seg_only.get() and self._segments:
            sel = self._checked_segments()
            if not sel:
                self._seg_filter = []             # 全不选: 不显示任何段
            elif len(sel) == len(self._segments):
                self._seg_filter = None           # 全选: 等同于全程序
            else:
                self._seg_filter = [(self._segments[i].start_idx,
                                     self._segments[i].end_idx) for i in sel]
        else:
            self._seg_filter = None
        self._build_time_prefix()      # 段过滤变化后重建播放进度时间轴
        self._build_render_meta()      # 过滤变化: 可见性/合并链重建
        if self.result:
            self._fill_stats()            # 段模式: 统计随勾选段刷新
            if refresh:
                if self._trace_active and self._seg_filter != old_filter:
                    # 段过滤变化使渐进轨迹失效: 全量渲染 (否则新勾选段不显示)
                    self.render()
                else:
                    self._view_refresh()

    def _toggle_seg_only(self):
        self._stop_playback()
        if self._seg_index is not None:
            self._set_segment(self._seg_index)   # 单次渲染 (含过滤+导航)
        else:
            self._apply_seg_filter()

    def _set_segment(self, idx):
        """跳转到指定段: 段模式确保勾选并直接展示勾选段完整刀路"""
        if not (0 <= idx < len(self._segments)):
            return
        self._seg_index = idx
        seg = self._segments[idx]
        self._stop_playback()
        if self._seg_only.get():
            self._set_checked(idx, True)         # 导航段自动勾选
        self._apply_seg_filter(refresh=False)    # 由下方渲染单次生效
        if self._seg_filter is not None:
            # 直接展示勾选段的完整刀路 (全量渲染, 切换段无残留)
            self.set_current_line(seg.end_line)
        else:
            self.set_current_line(seg.start_line)
        self._update_seg_ui()

    def _step_segment(self, delta):
        if not self._segments:
            return
        cur = self._seg_index if self._seg_index is not None else 0
        self._set_segment(max(0, min(len(self._segments) - 1, cur + delta)))

    def _jump_segment(self):
        if not self._segments:
            return
        try:
            n = int(self.seg_entry.get())
        except ValueError:
            return
        if 1 <= n <= len(self._segments):
            self._set_segment(n - 1)

    # ------------- 逐行运行 (播放控制条) -------------
    # ------------- 播放进度 (按加工时间, 顶部栏右上角进度条) -------------
    def _build_time_prefix(self):
        """构建播放进度时间轴: 移动(段过滤外)的加工时间前缀和, 与统计口径一致
        (含从原点出发的首条, 前导定位段的显示跳过不影响时间)。
        _time_prefix[k] = 前 k 个移动累计秒; _time_pos[移动索引] = 序号"""
        self._time_prefix = None
        self._time_pos = {}
        if not self.result or not self.result.moves:
            return
        moves = self.result.moves
        acc = [0.0]
        pos = {}
        first = True
        for i, m in enumerate(moves):
            if self._move_filtered(i):
                continue
            acc.append(acc[-1] + move_time_sec(m, lift_start=first))
            pos[i] = len(acc) - 1          # 该移动完成时的时间轴位置
            first = False
        if len(acc) <= 1:
            return
        self._time_prefix = acc
        self._time_pos = pos

    def _show_play_progress(self):
        """显示右上角播放进度条 (加载进度显示中则不重复 pack)"""
        if self._prog is None and self._prog_bar.winfo_manager() != "pack":
            self._prog_bar.pack(side="right")
            self._prog_lbl.pack(side="right", padx=(0, 6))

    def _hide_play_progress(self):
        """隐藏右上角播放进度条 (不影响加载进度状态)"""
        if self._prog is None:
            self._prog_lbl.pack_forget()
            self._prog_bar.pack_forget()

    def _update_play_progress(self):
        """播放推进时更新右上角进度条: 当前行累计加工时间/总时间 -> 百分比"""
        if self._time_prefix is None or self.current_line is None:
            return
        m = self.move_by_line.get(self.current_line)
        if m is None:
            return
        idx = self._move_index.get(id(m))
        if idx is None or idx not in self._time_pos:
            return
        pos = self._time_pos[idx]
        total = self._time_prefix[-1]
        if total <= 0:
            return
        frac = min(1.0, self._time_prefix[pos] / total)
        self._show_play_progress()
        self._prog_bar.config(value=frac * 100)
        self._prog_lbl.config(text="%.1f%%" % (frac * 100))

    def _stop_playback(self):
        """停止播放/演示 (幂等)"""
        self._play_mode = None
        if self._play_job is not None:
            self.after_cancel(self._play_job)
            self._play_job = None
        if hasattr(self, "play_btn"):
            self.play_btn.config(text="▶ 播放")

    def _play_speed_ms(self):
        return {"慢": 80, "中": 40, "快": 10}[self.speed_cb.get()]

    def _batch_lines(self):
        """播放合并行数: 1-100; 非法输入回退 1"""
        try:
            n = int(self.batch_cb.get())
        except (ValueError, tk.TclError):
            return 1
        return max(1, min(100, n))

    def _play_toggle(self):
        """▶ 播放 / 暂停 切换 (连续播放, 画布从空白起逐行画刀路)"""
        if not self.result:
            return
        if self._play_mode is not None:
            self._stop_playback()
            return
        if not self._trace_active:
            self._trace_begin()
            self.current_line = 0
        self._play_mode = "play"
        self.play_btn.config(text="暂停")
        self._play_tick()

    def _play_tick(self):
        if self._play_mode != "play":
            return
        lo, hi = self._seg_line_bounds()
        base = self.current_line if self.current_line else lo - 1
        if base >= hi:
            self._hide_play_progress()   # 播放到结尾: 隐藏进度条
            self._stop_playback()
            return
        # 合并跳行: 一次推进 N 行 (段模式钳制在段内)
        ln = min(base + self._batch_lines(), hi)
        self.set_current_line(ln, animate=True)
        self._update_play_progress()     # 进度条按加工时间显示当前进度
        self._play_job = self.after(self._play_speed_ms(), self._play_tick)

    def _step_line_ctl(self, delta):
        """单步前进/后退 (轨迹演示模式, 段模式钳制在段内)"""
        if not self.result:
            return
        self._stop_playback()
        if not self._trace_active:
            self._trace_begin()
            self.current_line = 0
        lo, hi = self._seg_line_bounds()
        base = self.current_line if self.current_line else lo - 1
        ln = max(lo, min(hi, base + delta))
        self.set_current_line(ln, animate=True)
        self._update_play_progress()     # 单步推进也按加工时间更新进度

    def _reset_line(self):
        if not self.result:
            return
        self._stop_playback()
        self._hide_play_progress()       # 复位回起点: 隐藏播放进度条
        if not self._trace_active:
            self._trace_begin()
        lo, _ = self._seg_line_bounds()
        self.current_line = lo - 1
        self.set_current_line(lo, animate=True)

    def _draw_all(self):
        """一键绘制全部刀路 (段模式画完本段, 否则整条程序)"""
        if not self.result:
            return
        self._stop_playback()
        if not self._trace_active:
            self._trace_begin()
            self.current_line = 0
        _, hi = self._seg_line_bounds()
        self.set_current_line(hi, animate=True)

    def _run_to_target(self, animated):
        """运行到选中目标行: animated=True 演示(动画逐帧画刀路),
        False 无动态直接后台运算到该行 (轨迹一次性画到目标)"""
        if not self.result:
            return
        target = self._target_line
        if target is None:
            messagebox.showinfo("提示", "请先在代码列表中点击选中目标行")
            return
        self._stop_playback()
        if not self._trace_active:
            self._trace_begin()
            self.current_line = 0
        if not animated:
            self.set_current_line(target, animate=True)   # 无动态: 直接画到该行
            return
        self._demo_target = target                 # 有动态: 逐帧演示推进
        cur = self.current_line if self.current_line else 0
        self._demo_step = self._demo_step_size(target, cur)   # 恒定步长
        self._play_mode = "demo"
        self.play_btn.config(text="暂停")
        self._demo_tick()

    @staticmethod
    def _demo_step_size(target, cur):
        """演示起始步长: 剩余距离的 1/80 向上取整 (至少 1), 全程恒定,
        保证 ≤80 帧精确到达; 已到达/已越过返回 0 (停止)"""
        dist = target - cur
        if dist <= 0:
            return 0
        return max(1, (dist + 79) // 80)

    def _demo_tick(self):
        if self._play_mode != "demo":
            return
        cur = self.current_line if self.current_line else 0
        if cur >= self._demo_target:
            self.set_current_line(self._demo_target, animate=True)
            self._stop_playback()
            return
        ln = min(cur + self._demo_step, self._demo_target)
        self.set_current_line(ln, animate=True)
        self._update_play_progress()     # 演示逐帧推进时同步加工时间进度
        self._play_job = self.after(self._play_speed_ms(), self._demo_tick)

    # ------------- 轨迹渐进绘制 (播放/演示时刀路逐行画出) -------------
    def _trace_base(self):
        """轨迹起始移动索引: 跳过前导进给段; 段模式下从勾选段最前开始"""
        base = self._lead_skip
        if self._seg_filter:
            if self._seg_filter:            # 非空: 勾选段并集起点
                base = max(base, min(lo for lo, _ in self._seg_filter))
            else:                           # 空: 无可画移动
                base = len(self.result.moves)
        return base

    def _trace_begin(self):
        """开始轨迹演示: 清空已绘刀路, 从第一个可解析点起按行绘制"""
        self.canvas.delete("path")
        self._path_items = []             # 渲染图元复用结构失效
        self._poly_struct = None
        self._trace_active = True
        self._trace_drawn = self._trace_base()
        self._trace_items = []
        self._move_lines = [m.line_number for m in self.result.moves]
        self.canvas.delete("axes", "axescorner")   # 清旧坐标系再画, 防图元重复
        self._draw_axes()

    def _trace_draw_to_line(self, ln):
        """把轨迹绘制到"执行到 ln 行时"已完成的移动"""
        k = bisect.bisect_right(self._move_lines, ln) - 1
        self._trace_draw_to(k)

    def _trace_redraw(self):
        """用当前四元数/缩放/偏移重绘已画轨迹 (旋转/缩放/适配时不退出轨迹模式)"""
        drawn = self._trace_drawn
        self.canvas.delete("axes", "axescorner", "path", "cur", "curseg",
                           "toolmodel")
        self._path_items = []             # 渲染图元复用结构失效
        self._poly_struct = None
        self._draw_axes()
        self._trace_items = []
        self._trace_drawn = self._trace_base()
        self._trace_draw_to(drawn - 1)
        if self.current_line is not None:
            self._draw_current()
            self._draw_tool_model()

    def _trace_draw_to(self, k):
        """增量绘制移动 0..k (前进追加, 后退整段重绘)。

        canvas.coords 在循环结束后统一回写: 逐移动回写会把合并折线的全量
        坐标反复传输 ("绘制到结尾"等大跳变为 O(n^2), 大程序明显卡顿)。
        """
        if k < self._trace_drawn:
            for _, item, _ in self._trace_items:
                self.canvas.delete(item)
            self._trace_items = []
            self._trace_drawn = self._trace_base()
        w, x, y, z = self.quat
        scale, ox, oy = self.scale, self.offset[0], self.offset[1]
        vis, cols, merg = self._render_meta
        dirty = set()
        for i in range(self._trace_drawn, k + 1):
            if not vis[i]:
                continue
            color = cols[i]
            pts3d = self._disp3d[i]
            n = len(pts3d)
            if merg[i] and self._trace_items \
                    and self._trace_items[-1][0] == color:
                # 合并: 共享点已在折线尾部, 跳过首点 (防幻影连接线)
                flat = self._trace_items[-1][2]
                for i2 in range(1, n):
                    px, py, pz = pts3d[i2]
                    tx = 2 * (y * pz - z * py)
                    ty = 2 * (z * px - x * pz)
                    tz = 2 * (x * py - y * px)
                    vx = px + w * tx + (y * tz - z * ty)
                    vy = py + w * ty + (z * tx - x * tz)
                    flat.append(vx * scale + ox)
                    flat.append(-vy * scale + oy)
                dirty.add(len(self._trace_items) - 1)
            else:
                coords = []
                ap = coords.append
                for i2 in range(n):
                    px, py, pz = pts3d[i2]
                    tx = 2 * (y * pz - z * py)
                    ty = 2 * (z * px - x * pz)
                    tz = 2 * (x * py - y * px)
                    vx = px + w * tx + (y * tz - z * ty)
                    vy = py + w * ty + (z * tx - x * tz)
                    ap(vx * scale + ox)
                    ap(-vy * scale + oy)
                item = self.canvas.create_line(coords, fill=color, width=1,
                                               joinstyle="round", capstyle="round",
                                               tags="path")
                self._trace_items.append([color, item, coords])
        for idx in dirty:
            self.canvas.coords(self._trace_items[idx][1],
                               *self._trace_items[idx][2])
        self._trace_drawn = max(self._trace_drawn, k + 1)


def main():
    _enable_dpi_awareness()
    app = NCViewer()
    # 命令行可直接传文件路径 (支持多个, 与界面多选等效)
    files = [a for a in sys.argv[1:] if os.path.isfile(a)]
    if files:
        app.after(100, lambda: app.add_files(files))
    app.mainloop()