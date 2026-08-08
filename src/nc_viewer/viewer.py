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
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from . import theme
from .geometry import (CUR_COLOR, G0_COLOR, SEG_COLOR, VIEW_QUAT,
                       build_palette, color_of_move, compensate_center,
                       move_points_3d, orbit_rotate, project, quat_rotate)
from .parser import compute_stats, parse_nc
from .tool import (TOOL_SPECS, Tool, parse_aptsource_tool, tool_overall_height,
                   tool_profile_points, tool_summary)


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
        theme.apply_theme(self)
        self.configure(bg=theme.BG)
        self.title("NC 刀路查看器")
        self.geometry("1280x820")
        # 布局可缩放下限: 再小则画布/侧栏失去可用性
        self.minsize(960, 560)

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

        # 多文件管理: path -> 解析/缓存数据; fs_paths 为文件栏显示顺序
        self.file_items = {}
        self.fs_paths = []

        # 搜索状态
        self._search_pattern = None
        self._search_hits = []
        self._search_idx = -1

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
                        command=self._view_refresh).pack(side="left")
        self.tool_chk = ttk.Checkbutton(top, text="显示刀具", variable=self.show_tool,
                                        command=self._view_refresh)
        self.tool_chk.pack(side="left", padx=(8, 0))
        self.file_lbl = ttk.Label(top, text="(未打开文件)")
        self.file_lbl.pack(side="left", padx=(12, 0))

        # 主体: 上=画布+侧栏, 下=代码列表
        body = ttk.PanedWindow(self, orient="vertical")
        body.pack(side="top", fill="both", expand=True, padx=6, pady=4)

        upper = ttk.PanedWindow(body, orient="horizontal")
        body.add(upper, weight=3)

        # 左侧文件栏: 一次加载多个文件, 随时切换 (面板样式, 与画布区分)
        fs_frame = ttk.Frame(upper, width=280, padding=6, style="Panel.TFrame")
        upper.add(fs_frame, weight=0)
        fs_frame.columnconfigure(0, weight=1)
        ttk.Label(fs_frame, text="文件列表", font=("", 10, "bold"),
                  style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.file_listbox = tk.Listbox(fs_frame, width=34, exportselection=False,
                                       activestyle="dotbox",
                                       selectmode="browse", relief="flat", highlightthickness=1,
                                       bg=theme.PANEL, fg=theme.TEXT,
                                       selectbackground=theme.SELECTION,
                                       selectforeground="#ffffff",
                                       highlightbackground=theme.BORDER_LIGHT,
                                       highlightcolor=theme.ACCENT)
        self.file_listbox.grid(row=1, column=0, sticky="nsew")
        fsb = ttk.Scrollbar(fs_frame, orient="vertical", command=self.file_listbox.yview)
        fsb.grid(row=1, column=1, sticky="ns")
        self.file_listbox.config(yscrollcommand=fsb.set)
        fs_frame.rowconfigure(1, weight=1)
        self.file_listbox.bind("<<ListboxSelect>>", self._on_file_select)

        # 画布
        cv_frame = ttk.Frame(upper)
        upper.add(cv_frame, weight=3)
        self.canvas = tk.Canvas(cv_frame, bg=theme.CANVAS_BG, highlightthickness=0)
        self.canvas.pack(side="top", fill="both", expand=True)

        # 播放控制条 (逐行运行): 连续播放 / 单步 / 直达 / 演示
        ctl = ttk.Frame(cv_frame, padding=(4, 4), style="Panel.TFrame")
        ctl.pack(side="bottom", fill="x")
        ttk.Button(ctl, text="复位", command=self._reset_line).pack(side="left")
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

        self.status = ttk.Label(cv_frame, text="", anchor="w", padding=(4, 2))
        self.status.pack(side="bottom", fill="x")

        # 侧栏: 图例 + 统计 + 定位 (可滚动容器, 窗口缩小时内容完整可见)
        side = ttk.Frame(upper, width=260, padding=8, style="Panel.TFrame")
        upper.add(side, weight=0)
        side.columnconfigure(0, weight=1)
        side.rowconfigure(0, weight=1)
        self.side_canvas = tk.Canvas(side, bg=theme.PANEL, highlightthickness=0)
        self.side_canvas.grid(row=0, column=0, sticky="nsew")
        self.side_scroll = ttk.Scrollbar(side, orient="vertical",
                                         command=self.side_canvas.yview)
        self.side_scroll.grid(row=0, column=1, sticky="ns")
        self.side_canvas.config(yscrollcommand=self.side_scroll.set)
        inner = ttk.Frame(self.side_canvas, style="Panel.TFrame")
        self._side_inner = inner
        self._side_window = self.side_canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.columnconfigure(0, weight=1)
        inner.bind("<Configure>", lambda e: self.side_canvas.configure(
            scrollregion=self.side_canvas.bbox("all")))
        self.side_canvas.bind("<Configure>",
                              lambda e: self.side_canvas.itemconfigure(
                                  self._side_window, width=e.width))
        self.side_canvas.bind("<MouseWheel>", self._on_side_wheel)
        inner.bind("<MouseWheel>", self._on_side_wheel)

        ttk.Label(inner, text="颜色图例", font=("", 10, "bold"),
                  style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.legend = ttk.Frame(inner, style="Panel.TFrame")
        self.legend.grid(row=1, column=0, sticky="nsew", pady=(4, 8))

        # 程序统计 (仅关键指标; 完整统计在「详情…」二级窗口)
        st = ttk.LabelFrame(inner, text="程序统计", padding=8, style="Panel.TLabelframe")
        st.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        st.columnconfigure(1, weight=1)
        self.stats_labels = {}
        for r, (key, text) in enumerate((("x", "行程 X"), ("y", "行程 Y"),
                                         ("z", "行程 Z"), ("s", "S 转速"),
                                         ("f", "F 进给"), ("g", "G 次数"),
                                         ("tool", "刀具"))):
            ttk.Label(st, text=text, style="Panel.TLabel").grid(row=r, column=0, sticky="w", pady=1)
            lbl = ttk.Label(st, text="-", font=theme.FONT_MONO, style="Panel.TLabel")
            lbl.grid(row=r, column=1, sticky="e", pady=1)
            self.stats_labels[key] = lbl
        btns = ttk.Frame(st, style="Panel.TFrame")
        btns.grid(row=len(("x", "y", "z", "s", "f", "g", "tool")), column=0,
                  columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(btns, text="详情…", style=theme.BTN_ACCENT,
                   command=self.show_details).pack(side="left")
        ttk.Button(btns, text="F 曲线", command=self.show_f_curve).pack(side="left", padx=(8, 0))

        ttk.Separator(inner, orient="horizontal").grid(row=3, column=0, sticky="ew")

        self.pos_lbl = ttk.Label(inner, text="当前位置: -", justify="left",
                                 font=theme.FONT_MONO, style="Panel.TLabel")
        self.pos_lbl.grid(row=4, column=0, sticky="w", pady=(8, 0))

        # 刀具栏 (独立一栏, 位于当前位置信息栏下方)
        tbar = ttk.LabelFrame(inner, text="刀具", padding=8, style="Panel.TLabelframe")
        tbar.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        tbar.columnconfigure(1, weight=1)
        ttk.Label(tbar, text="刀具:", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.tool_lbl = ttk.Label(tbar, text="-", style="Panel.TLabel")
        self.tool_lbl.grid(row=0, column=1, sticky="w", padx=(4, 0))
        self.tool_btn = ttk.Button(tbar, text="剖面图", style=theme.BTN_ACCENT,
                                   command=self.show_tool_profile)
        self.tool_btn.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Button(tbar, text="自定义…", command=self.show_tool_setup).grid(
            row=1, column=1, sticky="w", pady=(4, 0))

        # 代码列表 + 右侧定位/搜索面板 (与上方侧栏同列)
        code_split = ttk.Panedwindow(body, orient="horizontal")
        body.add(code_split, weight=1)
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
        self.code.pack(side="left", fill="both", expand=True)
        ysb.pack(side="right", fill="y")
        xsb.pack(side="bottom", fill="x")
        self.code.configure(state="disabled")
        self.code.bind("<Button-1>", self._on_code_click, add="+")
        self.code.tag_configure("cur", background="#264f78", foreground="#ffffff")
        self.code.tag_configure("ln", foreground=theme.TEXT_DIM, selectbackground=theme.BG)
        self.code.tag_configure("search", background="#3a5a3a")
        self.code.tag_configure("searchcur", background="#7a9a3a", foreground="#ffffff")
        self.code.tag_configure("target", background="#3d3d5c", foreground="#ffffff")
        self.code.tag_raise("search")
        self.code.tag_raise("searchcur")

        # 右侧面板: 按行定位 + 搜索定位 (与上方侧栏同列对齐)
        right = ttk.Frame(code_split, width=380, padding=8, style="Panel.TFrame")
        code_split.add(right, weight=0)
        right.columnconfigure(0, weight=1)
        loc = ttk.LabelFrame(right, text="按行定位", padding=8, style="Panel.TLabelframe")
        loc.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        loc.columnconfigure(1, weight=1)
        ttk.Label(loc, text="行号 / N号:").grid(row=0, column=0, sticky="w")
        self.loc_entry = ttk.Entry(loc)
        self.loc_entry.grid(row=0, column=1, sticky="ew", padx=4)
        self.loc_entry.bind("<Return>", lambda e: self.jump_to_input())
        ttk.Button(loc, text="跳转", command=self.jump_to_input).grid(row=0, column=2)
        ttk.Label(loc, text="例如: 12 或 N6", foreground=theme.TEXT_DIM).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Button(loc, text="上一行", command=lambda: self.step_line(-1)).grid(
            row=2, column=0, pady=(6, 0))
        ttk.Button(loc, text="下一行", command=lambda: self.step_line(1)).grid(
            row=2, column=1, pady=(6, 0), sticky="w")

        sr = ttk.LabelFrame(right, text="搜索定位", padding=8, style="Panel.TLabelframe")
        sr.grid(row=1, column=0, sticky="ew")
        sr.columnconfigure(1, weight=1)
        ttk.Label(sr, text="关键字:").grid(row=0, column=0, sticky="w")
        self._search_entry = ttk.Entry(sr)
        self._search_entry.grid(row=0, column=1, sticky="ew", padx=4)
        self._search_entry.bind("<Return>", lambda e: self.search_nc())
        ttk.Button(sr, text="搜索", command=self.search_nc).grid(row=0, column=2)
        ttk.Button(sr, text="下一个", command=lambda: self.search_nc(next_=True)).grid(
            row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(sr, text="在代码中查找文本并跳转", foreground=theme.TEXT_DIM).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

    def _bind_canvas(self):
        self.canvas.bind("<ButtonPress-1>", self._pan_start)
        self.canvas.bind("<B1-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-1>", self._pan_end)
        self.canvas.bind("<ButtonPress-2>", self._rot_start)
        self.canvas.bind("<B2-Motion>", self._rot_move)
        self.canvas.bind("<ButtonRelease-2>", self._rot_end)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Configure>",
                         lambda e: self._view_refresh() if self.result else None)
        self._pan_data = None
        self._rot_data = None

    def _on_side_wheel(self, e):
        """侧栏滚动容器滚轮事件 (Windows: delta 为 120 的倍数)"""
        self.side_canvas.yview_scroll(-1 * (e.delta // 120), "units")

    # ------------- 文件 -------------
    def open_file_multi(self):
        """弹出文件选择框(可多选), 加载所有选中文件并切换到第一个"""
        initdir = _sample_dir()
        paths = filedialog.askopenfilenames(
            title="选择 NC 文件(可多选)",
            initialdir=initdir,
            filetypes=[("NC/G代码", "*.MPF *.NC *.CNC *.TXT *.mpf *.nc *.cnc *.txt"), ("所有文件", "*.*")],
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
        """解析并缓存多个文件, 刷新文件栏, 切换到第一个新加载文件"""
        new_paths = []
        for path in paths:
            if path in self.file_items:
                continue
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError as e:
                messagebox.showerror("打开失败", str(e))
                continue
            result = parse_nc(text)
            self.file_items[path] = {
                "path": path,
                "text": text,
                "lines": text.splitlines(),
                "result": result,
                "palette": build_palette(result.feeds),
                "move_by_line": {m.line_number: m for m in result.moves},
                # 渲染前一次性离散所有刀路段(含圆弧), 避免旋转时反复重算
                "disp3d": [move_points_3d(m, max_seg=4) for m in result.moves],
                "move_index": {id(m): i for i, m in enumerate(result.moves)},
                # 程序从第一个可解析点开始: 跳过从原点出发的起始进给段
                "lead_skip": _compute_lead_skip(result.moves),
                # 刀具: 从同目录/同名 aptsource 头部解析 (无则 None)
                "tool": self._parse_tool_for(path, text),
            }
            new_paths.append(path)
        if not new_paths:
            return
        self._refresh_file_list()
        self.set_current_file(new_paths[0])

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
        self.fs_paths = list(self.file_items.keys())
        self.file_listbox.delete(0, "end")
        for p in self.fs_paths:
            self.file_listbox.insert("end", os.path.basename(p))

    def _on_file_select(self, e):
        sel = self.file_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        if 0 <= idx < len(self.fs_paths):
            self.set_current_file(self.fs_paths[idx])

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
        self._target_line = None
        self.target_lbl.config(text="目标行: -")
        self.file_lbl.config(text=os.path.basename(path))
        self._fill_code()
        self._fill_legend()
        self._fill_stats()
        self._refresh_tool_ui()
        self.fit_view()
        self.status.config(text=self._status_text())
        # 高亮文件栏当前项
        self.file_listbox.selection_clear(0, "end")
        if path in self.fs_paths:
            idx = self.fs_paths.index(path)
            self.file_listbox.selection_set(idx)
            self.file_listbox.see(idx)

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
        for i, line in enumerate(self.lines, 1):
            self.code.insert("end", f"{i:5d}| ", "ln")
            m = move_by_line.get(i)
            if m is None:
                self.code.insert("end", line + "\n")
            elif m.motion == "G0":
                self.code.insert("end", line + "\n", "g0")
            else:
                self.code.insert("end", line + "\n", self._f_tag(m.feed))
        self.code.configure(state="disabled")

    @staticmethod
    def _f_tag(feed):
        return "f_" + format(feed, ".4f").rstrip("0").rstrip(".")

    def _fill_legend(self):
        for w in self.legend.winfo_children():
            w.destroy()
        items = [(G0_COLOR, "G0 快速移动")] + [
            (self.palette[f], f"F{format(f, '.4f').rstrip('0').rstrip('.')}")
            for f in self.result.feeds
        ]
        # 横向流式排布: 按字体测量宽度, 超出面板宽度自动换行 (多行)
        font = tkfont.Font(font=theme.FONT_UI)
        max_w = max(self.legend.winfo_width(), 260)
        row = 0
        col = 0
        used = 0
        for color, text in items:
            chip_w = 22 + font.measure(text) + 12
            if col > 0 and used + chip_w > max_w:
                row += 1
                col = 0
                used = 0
            sw = tk.Label(self.legend, bg=color, width=2, height=1, relief="flat")
            sw.grid(row=row, column=col * 2, padx=(0, 5), pady=1, sticky="w")
            ttk.Label(self.legend, text=text, style="Panel.TLabel").grid(
                row=row, column=col * 2 + 1, sticky="w", padx=(0, 12))
            col += 1
            used += chip_w

    # ------------- 程序统计 -------------
    def _fill_stats(self):
        """刷新侧栏关键统计 (行程/S/F/G 次数)"""
        st = compute_stats(self.result)
        fmt = lambda v: f"{v:.3f}"
        self.stats_labels["x"].config(text=f"{fmt(st.x_min)} ~ {fmt(st.x_max)}")
        self.stats_labels["y"].config(text=f"{fmt(st.y_min)} ~ {fmt(st.y_max)}")
        self.stats_labels["z"].config(text=f"{fmt(st.z_min)} ~ {fmt(st.z_max)}")
        s_txt = "-" if st.s_min is None else f"{st.s_min:.0f} ~ {st.s_max:.0f}"
        self.stats_labels["s"].config(text=s_txt)
        f_txt = "-" if st.f_min is None else f"{st.f_min:g} ~ {st.f_max:g} · {st.f_count} 档"
        self.stats_labels["f"].config(text=f_txt)
        g = st.g_counts
        g_txt = "  ".join(f"G{i}:{g.get(f'G{i}', 0)}" for i in (0, 1, 2, 3))
        self.stats_labels["g"].config(text=g_txt)
        self.stats_labels["tool"].config(
            text=tool_summary(self.tool) if self.tool else "-")

    def _refresh_tool_ui(self):
        """刷新刀具显示 (图例行 / 统计行 / 3D 模型开关)"""
        has_tool = self.tool is not None
        self.tool_lbl.config(text=tool_summary(self.tool) if has_tool else "-")
        self.tool_btn.config(state="normal" if has_tool else "disabled")
        self.tool_chk.config(state="normal" if has_tool else "disabled")
        if "tool" in self.stats_labels:
            self.stats_labels["tool"].config(
                text=tool_summary(self.tool) if has_tool else "-")

    def show_details(self):
        """二级窗口: 完整程序统计"""
        if not self.result:
            return
        st = compute_stats(self.result)
        win = tk.Toplevel(self)
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
        """切削移动的 (行号, F) 序列 (G0 快移无 F, 不参与)"""
        return [(m.line_number, m.feed) for m in self.result.moves if m.feed is not None]

    def show_f_curve(self):
        """二级窗口: F 进给随行号变化趋势 (按 F 档位着色, 可拉伸)"""
        if not self.result:
            return
        data = self._f_curve_data()
        if not data:
            messagebox.showinfo("F 曲线", "程序中没有切削移动 (G0 快移无 F)")
            return
        win = tk.Toplevel(self)
        win.title(f"F 进给趋势 — {os.path.basename(self._current_path)}")
        win.configure(bg=theme.BG)
        win.geometry("820x460")
        win.minsize(480, 320)
        cv = tk.Canvas(win, bg=theme.EDITOR_BG, highlightthickness=0)
        cv.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        self._curve_job = None
        cv.bind("<Configure>", lambda e: self._curve_redraw(cv, data, e))
        self._draw_f_curve(cv, data, cv.winfo_width() or 804, cv.winfo_height() or 420)

    def _curve_redraw(self, cv, data, e):
        """窗口拉伸时 60ms 防抖重绘"""
        if self._curve_job is not None:
            self.after_cancel(self._curve_job)
        self._curve_job = self.after(60, lambda: self._draw_f_curve(cv, data, e.width, e.height))

    def _draw_f_curve(self, cv, data, W, H):
        """按给定尺寸绘制 F 趋势图 (可独立于窗口尺寸测试/重绘)"""
        cv.delete("all")
        if not data:
            return
        pad_l, pad_r, pad_t, pad_b = 52, 20, 30, 38
        plot_w, plot_h = W - pad_l - pad_r, H - pad_t - pad_b
        if plot_w <= 20 or plot_h <= 20:
            return
        lines = [ln for ln, _ in data]
        feeds = [f for _, f in data]
        fmin, fmax = min(feeds), max(feeds)
        if fmax == fmin:
            fmax = fmin + 1.0
        ln0, ln1 = lines[0], lines[-1]
        if ln1 == ln0:
            ln1 = ln0 + 1
        fmt_axis = lambda v: f"{v:.0f}" if v >= 100 else f"{v:g}"

        def sx(x):
            return pad_l + (x - ln0) / (ln1 - ln0) * plot_w

        def sy(v):
            return pad_t + (1 - (v - fmin) / (fmax - fmin)) * plot_h

        # 网格 + 刻度 (每轴 6 档)
        for i in range(6):
            v = fmin + (fmax - fmin) * i / 5
            y = sy(v)
            cv.create_line(pad_l, y, W - pad_r, y, fill=theme.BORDER, tags="grid")
            cv.create_text(pad_l - 8, y, text=fmt_axis(v), anchor="e",
                           fill=theme.TEXT_DIM, font=theme.FONT_SMALL)
        for i in range(6):
            x = ln0 + (ln1 - ln0) * i / 5
            xx = sx(x)
            cv.create_line(xx, pad_t, xx, H - pad_b, fill=theme.BORDER, tags="grid")
            cv.create_text(xx, H - pad_b + 8, text=f"{x:.0f}", anchor="n",
                           fill=theme.TEXT_DIM, font=theme.FONT_SMALL)
        # 坐标轴与标题
        cv.create_line(pad_l, pad_t, pad_l, H - pad_b, fill=theme.TEXT_DIM)
        cv.create_line(pad_l, H - pad_b, W - pad_r, H - pad_b, fill=theme.TEXT_DIM)
        cv.create_text(W // 2, H - 6, text="行号", fill=theme.TEXT, font=theme.FONT_UI)
        cv.create_text(6, pad_t, text="F 进给", anchor="w", fill=theme.TEXT, font=theme.FONT_UI)
        # min/max F 参考虚线
        for v in (fmin, fmax):
            y = sy(v)
            cv.create_line(pad_l, y, W - pad_r, y, fill=theme.TEXT_DIM, dash=(3, 3))
            cv.create_text(W - pad_r - 4, y - 4, text=f"F{fmt_axis(v)}", anchor="e",
                           fill=theme.TEXT_DIM, font=theme.FONT_SMALL)
        # 折线: 相邻同 F 合并为折线, 按 F 档位着色
        palette = self.palette
        polylines = []  # [(color, [x1, y1, x2, y2, ...])]
        for ln, f in data:
            x, y = sx(ln), sy(f)
            key = palette.get(f, "#ffffff")
            if polylines and polylines[-1][0] == key:
                polylines[-1][1].extend([x, y])
            else:
                polylines.append([key, [x, y]])
        for color, pts in polylines:
            if len(pts) >= 4:
                cv.create_line(pts, fill=color, width=2, joinstyle="round", tags="curve")
            else:
                cv.create_oval(pts[0] - 3, pts[1] - 3, pts[0] + 3, pts[1] + 3,
                               fill=color, outline="", tags="curve")
        # 档位图例 (右上角, 不压数据区)
        feeds_order = self.result.feeds if self.result else []
        ly = pad_t + 10
        for f in feeds_order:
            col = palette.get(f, "#ffffff")
            cv.create_rectangle(W - pad_r - 140, ly, W - pad_r - 128, ly + 10,
                                fill=col, outline="")
            cv.create_text(W - pad_r - 124, ly + 5,
                           text=f"F{format(f, '.4f').rstrip('0').rstrip('.')}",
                           anchor="w", fill=theme.TEXT, font=theme.FONT_SMALL)
            ly += 18

    # ------------- 刀具 3D 模型 -------------
    def _tool_model_points(self, tool):
        """刀具旋转体 3D 模型点集 [(kind, [(x,y,z),...])], 本地坐标:
        刀尖在原点, 刀具轴沿 +Z 向上; body 为半透明实体外轮廓"""
        profile = tool_profile_points(tool)
        l = float(tool.p("l", 30.0))
        h = tool_overall_height(tool)
        r_top = profile[-1][0]
        full = list(profile)
        if h > l + 1e-9:
            full.append((r_top, l))
            full.append((r_top, h))          # 刀柄延伸至总长
        max_r = max(r for r, _ in full)
        pts = []
        # 实体: 右缘(下->上) + 顶部圆 + 左缘(上->下), 闭合于刀尖
        top_circle = [(r_top * math.cos(t), r_top * math.sin(t), h)
                      for t in (i * math.tau / 24 for i in range(24))]
        body = ([(r, 0.0, y) for r, y in full] + top_circle
                + [(-r, 0.0, y) for r, y in reversed(full)])
        pts.append(("body", body))
        # 两条经线 (60°/120°) 作表面细节
        for ang in (math.pi / 3, 2 * math.pi / 3):
            pts.append(("mer", [(r * math.cos(ang), r * math.sin(ang), y)
                                for r, y in full]))
        # 关键截面圆 (最大半径处)
        for y in sorted({y for r, y in full if abs(r - max_r) < 1e-9}):
            circle = [(max_r * math.cos(t), max_r * math.sin(t), y)
                      for t in (i * math.tau / 24 for i in range(24))]
            pts.append(("cir", circle))
        pts.append(("axis", [(0.0, 0.0, 0.0), (0.0, 0.0, h)]))
        pts.append(("tip", [(0.0, 0.0, 0.0)]))
        return pts

    def _draw_tool_model(self):
        """画布内绘制刀具旋转 3D 模型 (半透明实体, 刀尖对刀)"""
        if not self.show_tool.get() or not self.tool or not self.result:
            return
        tool = self.tool
        pos = (self.result.position_at_line(self.current_line)
               if self.current_line else (0.0, 0.0, 0.0))
        h = tool_overall_height(tool)
        # 最小可见尺寸: 投影高 <24px 时以刀尖为锚放大 (刀具相对零件很小)
        a0, b0 = project(pos, self.quat)
        a1, b1 = project((pos[0], pos[1], pos[2] + h), self.quat)
        px_h = abs(b1 - b0) * self.scale
        factor = 1.0
        if 0 < px_h < 24:
            factor = min(24.0 / px_h, 8.0)
        for kind, pts in self._tool_model_points(tool):
            screen = []
            for x, y, z in pts:
                wx = pos[0] + x * factor
                wy = pos[1] + y * factor
                wz = pos[2] + z * factor
                a, b = project((wx, wy, wz), self.quat)
                screen.append(self.world_to_canvas(a, b))
            if kind == "tip":
                x0, y0 = screen[0]
                self.canvas.create_oval(x0 - 4, y0 - 4, x0 + 4, y0 + 4,
                                        fill=CUR_COLOR, outline="#000000",
                                        tags="toolmodel")
            elif kind == "body":
                # 半透明实体 (stipple 抖动填充)
                self.canvas.create_polygon(screen, fill="#9a9aa2",
                                           outline="#c8c8c8", width=1,
                                           stipple="gray50", tags="toolmodel")
            elif len(screen) >= 2:
                kw = {"fill": "#8a8a8a", "tags": "toolmodel"}
                if kind == "axis":
                    kw["fill"] = "#6e6e6e"
                    kw["dash"] = (3, 3)
                self.canvas.create_line(screen, width=1, **kw)

    # ------------- 刀具剖面图 -------------
    def show_tool_profile(self):
        """二级窗口: 刀具直径剖面图 (镜像 + 尺寸标注, 可拉伸)"""
        if not self.tool:
            return
        tool = self.tool
        win = tk.Toplevel(self)
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
        l = max(y for _, y in profile)          # 刃长
        h = tool_overall_height(tool)           # 总长 (含刀柄)
        r_top = profile[-1][0]
        full = list(profile)
        if h > l + 1e-9:
            full.append((r_top, l))
            full.append((r_top, h))             # 刀柄延伸
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
        self._dim_v(cv, sx(max_r) + 20, sy(0), sx(max_r) + 20, sy(l), f"L{l:g}", "right")
        if h > l + 1e-9:
            self._dim_v(cv, sx(0) - 20, sy(0), sx(0) - 20, sy(h), f"H{h:g}", "left")
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

    def _dim_h(self, cv, x1, y, x2, y2, text, side):
        """水平尺寸线 + 45° 端刻线 + 标注"""
        yy = y + 26 if side == "bottom" else y - 26
        cv.create_line(x1, yy, x2, yy, fill=theme.TEXT_DIM)
        for xx in (x1, x2):
            cv.create_line(xx, yy - 5, xx, yy + 5, fill=theme.TEXT_DIM)
            cv.create_line(xx, yy - 5, xx - 4, yy, fill=theme.TEXT_DIM)
            cv.create_line(xx, yy + 5, xx - 4, yy, fill=theme.TEXT_DIM)
        cv.create_text((x1 + x2) / 2, yy - 12 if side == "bottom" else yy + 12,
                       text=text, fill=theme.TEXT, font=theme.FONT_MONO)

    def _dim_v(self, cv, x, y1, y2, y3, text, side):
        """垂直尺寸线 + 端刻线 + 标注"""
        xx = x + 20 if side == "right" else x - 20
        cv.create_line(xx, y1, xx, y2, fill=theme.TEXT_DIM)
        for yy in (y1, y2):
            cv.create_line(xx - 5, yy, xx + 5, yy, fill=theme.TEXT_DIM)
            cv.create_line(xx - 5, yy, xx, yy - 4, fill=theme.TEXT_DIM)
            cv.create_line(xx + 5, yy, xx, yy - 4, fill=theme.TEXT_DIM)
        cv.create_text(xx + 12 if side == "right" else xx - 12, (y1 + y2) / 2,
                       text=text, fill=theme.TEXT, font=theme.FONT_MONO)

    # ------------- 刀具自定义 -------------
    def show_tool_setup(self):
        """自定义窗口: 选择类型 -> 对应规格输入框 -> 应用/恢复自动解析"""
        win = tk.Toplevel(self)
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
    def rotated_bbox(self):
        """当前旋转视角下, 所有刀路点投影后的 2D 包围盒 (a0,b0,a1,b1)。

        排除前导跳过段(从原点出发的起始进给), 使适配聚焦于实际加工区域。
        """
        q = self.quat
        a0 = b0 = float("inf")
        a1 = b1 = float("-inf")
        for i in range(self._lead_skip, len(self._disp3d)):
            for p in self._disp3d[i]:
                a, b = project(p, q)
                if a < a0: a0 = a
                if b < b0: b0 = b
                if a > a1: a1 = a
                if b > b1: b1 = b
        if a0 == float("inf"):
            return (0.0, 0.0, 1.0, 1.0)
        return a0, b0, a1, b1

    def _view_refresh(self):
        """视图变换后的刷新: 轨迹模式重投影已画轨迹, 否则全量渲染"""
        if self._trace_active:
            self._trace_redraw()
        else:
            self.render()

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
        self._view_refresh()

    def render(self):
        self._trace_active = False        # 全量渲染即退出轨迹演示
        self.canvas.delete("all")
        if not self.result or not self.result.moves:
            return
        w, x, y, z = self.quat
        scale, ox, oy = self.scale, self.offset[0], self.offset[1]
        show_g0 = self.show_g0.get()
        palette = self.palette

        # 内联四元数投影 + 相邻同色合并为折线(扁平坐标列表)
        polylines = []  # [(color, [sx,sy, sx,sy, ...])]
        for i, (m, pts3d) in enumerate(zip(self.result.moves, self._disp3d)):
            if i < self._lead_skip:      # 从原点出发的起始进给段不显示
                continue
            if m.motion == "G0" and not show_g0:
                continue
            key = color_of_move(m, palette)
            coords = []
            for px, py, pz in pts3d:
                tx = 2 * (y * pz - z * py)
                ty = 2 * (z * px - x * pz)
                tz = 2 * (x * py - y * px)
                vx = px + w * tx + (y * tz - z * ty)
                vy = py + w * ty + (z * tx - x * tz)
                coords.append(vx * scale + ox)
                coords.append(-vy * scale + oy)
            if polylines and polylines[-1][0] == key:
                polylines[-1][1].extend(coords[2:])
            else:
                polylines.append([key, coords])

        for color, pts in polylines:
            if len(pts) < 4:
                continue
            self.canvas.create_line(pts, fill=color, width=1, joinstyle="round",
                                    capstyle="round", tags="path")

        self._draw_axes()
        self._draw_current()
        self._draw_tool_model()
        self.status.config(text=self._status_text())

    def _draw_axes(self):
        """画出当前视角下 X(红)/Y(绿)/Z(青) 轴方向指示"""
        q = self.quat
        ox, oy = self.world_to_canvas(*project((0, 0, 0), q))
        r = max(self.scale * 8, 30)
        for axis, color, vec in (("X", "#ff5555", (1, 0, 0)),
                                 ("Y", "#55ff55", (0, 1, 0)),
                                 ("Z", "#55ffff", (0, 0, 1))):
            ex, ey = self.world_to_canvas(*project((vec[0] * r, vec[1] * r, vec[2] * r), q))
            self.canvas.create_line(ox, oy, ex, ey, fill=color, width=2, tags="axes")
            self.canvas.create_text(ex, ey, text=axis, fill=color, tags="axes")
        # 原点标记
        self.canvas.create_oval(ox - 4, oy - 4, ox + 4, oy + 4,
                                outline="#ffffff", width=1, tags="axes")

    def _draw_current(self):
        if self.current_line is None:
            return
        q = self.quat
        pos = self.result.position_at_line(self.current_line)
        a, b = project(pos, q)
        cx, cy = self.world_to_canvas(a, b)

        # 当前段高亮 + 加工方向箭头 (前导跳过段不显示高亮)
        m = self.move_by_line.get(self.current_line)
        if m is not None and not (m.motion == "G0" and not self.show_g0.get()):
            idx = self._move_index[id(m)]
            if idx < self._lead_skip:
                m = None
        if m is not None:
            pts = [self.world_to_canvas(*project(p, q)) for p in self._disp3d[idx]]
            if len(pts) >= 2:
                self.canvas.create_line(pts, fill=SEG_COLOR, width=3,
                                        tags="curseg")
                self._draw_move_direction(pts)

        # 十字线
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        self.canvas.create_line(cx, 0, cx, ch, fill=CUR_COLOR, dash=(4, 4), tags="cur")
        self.canvas.create_line(0, cy, cw, cy, fill=CUR_COLOR, dash=(4, 4), tags="cur")
        # 当前点
        self.canvas.create_oval(cx - 6, cy - 6, cx + 6, cy + 6,
                                fill=CUR_COLOR, outline="#000000", width=2, tags="cur")

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

    def _arrow_at(self, x, y, dx, dy, color=CUR_COLOR, size=14):
        """在 (x,y) 处画一个指向 (dx,dy) 方向的实心三角箭头 (带深色描边, 醒目)"""
        L = math.hypot(dx, dy)
        if L < 1e-6:
            return
        ux, uy = dx / L, dy / L
        px, py = -uy, ux                     # 垂直方向(箭头翼)
        size = min(size, L * 0.5)            # 避免过短段上箭头过大
        b1x = x - ux * size + px * size * 0.55
        b1y = y - uy * size + py * size * 0.55
        b2x = x - ux * size - px * size * 0.55
        b2y = y - uy * size - py * size * 0.55
        self.canvas.create_polygon(x, y, b1x, b1y, b2x, b2y,
                                   fill=color, outline="#000000", width=1, tags="cur")

    # ------------- 交互: 平移/缩放 -------------
    def _pan_start(self, e):
        # 记录起点、原始偏移、上一次位置(用于增量位移)
        self._pan_data = (e.x, e.y, self.offset[0], self.offset[1], e.x, e.y)

    def _pan_move(self, e):
        if not self._pan_data:
            return
        sx, sy, ox, oy, px, py = self._pan_data
        dx, dy = e.x - px, e.y - py
        # 增量位移全部图元(原生 C 操作, 远快于重绘), 同时更新偏移供下次缩放重绘
        self.canvas.move("all", dx, dy)
        if self._trace_active:
            # 轨迹存储坐标同步位移, 否则后续追加新点会混用新旧坐标系导致错乱
            for _, _, flat in self._trace_items:
                for i in range(0, len(flat), 2):
                    flat[i] += dx
                    flat[i + 1] += dy
        self.offset = (ox + (e.x - sx), oy + (e.y - sy))
        self._pan_data = (sx, sy, ox, oy, e.x, e.y)

    def _pan_end(self, e):
        self._pan_data = None

    # ------------- 交互: 中键旋转 (轨道球, CATIA 风格) -------------
    PICK_MAX_PX = 12.0   # 点击处吸附模型点的最大像素距离

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
        # 旋转中心 = 点击处真正落在刀路上的点; 否则锚定在点击处反投影点(包围盒 z 中心深度)
        c = self._pick_model_point(e.x, e.y)
        if c is None:
            wx, wy = self.canvas_to_world(e.x, e.y)
            *_, minz, ___, maxz = self.result.bbox
            c = (wx, wy, (minz + maxz) / 2)
        self._rot_center = c
        self._rot_data = (e.x, e.y)   # 记录上一鼠标位置, 用于计算增量拖动

    def _rot_move(self, e):
        if self._rot_data is None:
            return
        px, py = self._rot_data
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
        self._view_refresh()

    def _rot_end(self, e):
        self._rot_data = None

    def _on_wheel(self, e):
        factor = 1.2 if e.delta > 0 else 1 / 1.2
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
        self._highlight_code_line(ln)
        self.loc_entry.delete(0, "end")
        self.loc_entry.insert(0, str(ln))

    def _update_pos_info(self, ln):
        pos = self.result.position_at_line(ln)
        m = self.move_by_line.get(ln)
        parts = [f"当前位置 (行{ln}):",
                 f"  X = {pos[0]:.3f}",
                 f"  Y = {pos[1]:.3f}",
                 f"  Z = {pos[2]:.3f}"]
        if m is not None:
            n_str = f"N{int(m.n_number)} " if m.n_number is not None else ""
            f_str = "G0" if m.motion == "G0" else f"{m.motion} F{format(m.feed, '.4f').rstrip('0').rstrip('.')}"
            parts.append(f"  本行: {n_str}{f_str}")
        else:
            parts.append("  本行: 非移动指令")
        self.pos_lbl.config(text="\n".join(parts))

    def _highlight_code_line(self, ln):
        self.code.tag_remove("cur", "1.0", "end")
        start = f"{ln}.0"
        end = f"{ln}.end"
        self.code.tag_add("cur", start, end)
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
        self._set_target(ln)

    def _set_target(self, ln):
        """点击代码行 = 选中目标行 (执行位置不动, 目标行高亮)"""
        self._target_line = ln
        self.code.tag_remove("target", "1.0", "end")
        self.code.tag_add("target", f"{ln}.0", f"{ln}.end")
        self.code.tag_raise("target")
        self.target_lbl.config(text=f"目标行: {ln}")

    # ------------- 逐行运行 (播放控制条) -------------
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
        base = self.current_line if self.current_line else 0
        if base >= len(self.lines):
            self._stop_playback()
            return
        # 合并跳行: 一次推进 N 行 (轨迹同步合并绘制)
        ln = min(base + self._batch_lines(), len(self.lines))
        self.set_current_line(ln, animate=True)
        self._play_job = self.after(self._play_speed_ms(), self._play_tick)

    def _step_line_ctl(self, delta):
        """单步前进/后退 (轨迹演示模式)"""
        if not self.result:
            return
        self._stop_playback()
        if not self._trace_active:
            self._trace_begin()
            self.current_line = 0
        base = self.current_line if self.current_line else 0
        ln = max(1, min(len(self.lines), base + delta))
        self.set_current_line(ln, animate=True)

    def _reset_line(self):
        if not self.result:
            return
        self._stop_playback()
        if not self._trace_active:
            self._trace_begin()
        self.current_line = 0
        self.set_current_line(1, animate=True)

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
        self._play_job = self.after(self._play_speed_ms(), self._demo_tick)

    # ------------- 轨迹渐进绘制 (播放/演示时刀路逐行画出) -------------
    def _trace_begin(self):
        """开始轨迹演示: 清空已绘刀路, 从第一个可解析点起按行绘制"""
        self.canvas.delete("path")
        self._trace_active = True
        self._trace_drawn = self._lead_skip    # 跳过从原点出发的起始进给段
        self._trace_items = []
        self._move_lines = [m.line_number for m in self.result.moves]
        self._draw_axes()

    def _trace_draw_to_line(self, ln):
        """把轨迹绘制到"执行到 ln 行时"已完成的移动"""
        k = bisect.bisect_right(self._move_lines, ln) - 1
        self._trace_draw_to(k)

    def _trace_redraw(self):
        """用当前四元数/缩放/偏移重绘已画轨迹 (旋转/缩放/适配时不退出轨迹模式)"""
        drawn = self._trace_drawn
        self.canvas.delete("axes", "path", "cur", "curseg", "toolmodel")
        self._draw_axes()
        self._trace_items = []
        self._trace_drawn = self._lead_skip
        self._trace_draw_to(drawn - 1)
        if self.current_line is not None:
            self._draw_current()
            self._draw_tool_model()

    def _trace_draw_to(self, k):
        """增量绘制移动 0..k (前进追加, 后退整段重绘)"""
        if k < self._trace_drawn:
            for _, item, _ in self._trace_items:
                self.canvas.delete(item)
            self._trace_items = []
            self._trace_drawn = self._lead_skip
        moves = self.result.moves
        show_g0 = self.show_g0.get()
        w, x, y, z = self.quat
        scale, ox, oy = self.scale, self.offset[0], self.offset[1]
        for i in range(self._trace_drawn, k + 1):
            m = moves[i]
            if m.motion == "G0" and not show_g0:
                continue
            color = color_of_move(m, self.palette)
            coords = []
            for px, py, pz in self._disp3d[i]:
                tx = 2 * (y * pz - z * py)
                ty = 2 * (z * px - x * pz)
                tz = 2 * (x * py - y * px)
                vx = px + w * tx + (y * tz - z * ty)
                vy = py + w * ty + (z * tx - x * tz)
                coords.append(vx * scale + ox)
                coords.append(-vy * scale + oy)
            if not coords:
                continue
            if self._trace_items and self._trace_items[-1][0] == color:
                item, flat = self._trace_items[-1][1], self._trace_items[-1][2]
                flat.extend(coords[2:])          # 跳过与上条共用的衔接点
                self.canvas.coords(item, *flat)
            else:
                item = self.canvas.create_line(coords, fill=color, width=1,
                                               joinstyle="round", capstyle="round",
                                               tags="path")
                self._trace_items.append([color, item, list(coords)])
        self._trace_drawn = max(self._trace_drawn, k + 1)


def main():
    _enable_dpi_awareness()
    app = NCViewer()
    # 命令行可直接传文件路径
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.after(100, lambda: app.open_file(sys.argv[1]))
    app.mainloop()