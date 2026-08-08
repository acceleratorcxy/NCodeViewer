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

import math
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import theme
from .geometry import (CUR_COLOR, G0_COLOR, SEG_COLOR, VIEW_QUAT,
                       build_palette, color_of_move, compensate_center,
                       move_points_3d, orbit_rotate, project, quat_rotate)
from .parser import parse_nc


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


# ---------- 主窗口 ----------
class NCViewer(tk.Tk):
    def __init__(self):
        super().__init__()
        theme.apply_theme(self)
        self.configure(bg=theme.BG)
        self.title("NC 刀路查看器")
        self.geometry("1280x820")

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

        self._build_ui()
        self._bind_canvas()

    # ------------- UI -------------
    def _build_ui(self):
        # 顶部工具条
        top = ttk.Frame(self, padding=6)
        top.pack(side="top", fill="x")
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
        ttk.Checkbutton(top, text="显示G0快移", variable=self.show_g0,
                        command=self.render).pack(side="left")
        self.file_lbl = ttk.Label(top, text="(未打开文件)")
        self.file_lbl.pack(side="left", padx=(12, 0))

        # 主体: 上=画布+侧栏, 下=代码列表
        body = ttk.PanedWindow(self, orient="vertical")
        body.pack(side="top", fill="both", expand=True, padx=6, pady=4)

        upper = ttk.PanedWindow(body, orient="horizontal")
        body.add(upper, weight=3)

        # 左侧文件栏: 一次加载多个文件, 随时切换
        fs_frame = ttk.Frame(upper, width=200, padding=6)
        upper.add(fs_frame, weight=0)
        fs_frame.columnconfigure(0, weight=1)
        ttk.Label(fs_frame, text="文件列表", font=("", 10, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.file_listbox = tk.Listbox(fs_frame, exportselection=False, activestyle="dotbox",
                                       selectmode="browse", relief="flat", highlightthickness=1,
                                       bg=theme.PANEL, fg=theme.TEXT,
                                       selectbackground=theme.SELECTION,
                                       selectforeground="#ffffff",
                                       highlightbackground=theme.BORDER,
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
        self.status = ttk.Label(cv_frame, text="", anchor="w", padding=(4, 2))
        self.status.pack(side="bottom", fill="x")

        # 侧栏: 图例 + 定位
        side = ttk.Frame(upper, width=260, padding=8)
        upper.add(side, weight=0)
        side.columnconfigure(0, weight=1)

        ttk.Label(side, text="颜色图例", font=("", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.legend = ttk.Frame(side)
        self.legend.grid(row=1, column=0, sticky="nsew", pady=(4, 12))
        side.rowconfigure(1, weight=1)

        ttk.Separator(side, orient="horizontal").grid(row=2, column=0, sticky="ew")
        loc = ttk.LabelFrame(side, text="按行定位", padding=8)
        loc.grid(row=3, column=0, sticky="ew", pady=8)
        loc.columnconfigure(1, weight=1)
        ttk.Label(loc, text="行号 / N号:").grid(row=0, column=0, sticky="w")
        self.loc_entry = ttk.Entry(loc)
        self.loc_entry.grid(row=0, column=1, sticky="ew", padx=4)
        self.loc_entry.bind("<Return>", lambda e: self.jump_to_input())
        ttk.Button(loc, text="跳转", command=self.jump_to_input).grid(row=0, column=2)
        ttk.Label(loc, text="例如: 12 或 N6", foreground=theme.TEXT_DIM).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Button(loc, text="上一行", command=lambda: self.step_line(-1)).grid(row=2, column=0, pady=(6, 0))
        ttk.Button(loc, text="下一行", command=lambda: self.step_line(1)).grid(row=2, column=1, pady=(6, 0), sticky="w")

        # 搜索定位
        sr = ttk.LabelFrame(side, text="搜索定位", padding=8)
        sr.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        sr.columnconfigure(1, weight=1)
        ttk.Label(sr, text="关键字:").grid(row=0, column=0, sticky="w")
        self._search_entry = ttk.Entry(sr)
        self._search_entry.grid(row=0, column=1, sticky="ew", padx=4)
        self._search_entry.bind("<Return>", lambda e: self.search_nc())
        ttk.Button(sr, text="搜索", command=self.search_nc).grid(row=0, column=2)
        ttk.Button(sr, text="下一个", command=lambda: self.search_nc(next_=True)).grid(row=1, column=1, sticky="w", pady=(6, 0))
        ttk.Label(sr, text="在代码中查找文本并跳转", foreground=theme.TEXT_DIM).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.pos_lbl = ttk.Label(side, text="当前位置: -", justify="left", font=theme.FONT_MONO)
        self.pos_lbl.grid(row=5, column=0, sticky="w", pady=(8, 0))

        # 代码列表
        code_frame = ttk.Frame(body)
        body.add(code_frame, weight=1)
        ttk.Label(code_frame, text="NC 代码 (点击行定位)", padding=(4, 2)).pack(side="top", fill="x")
        cvsb = ttk.Frame(code_frame)
        cvsb.pack(side="top", fill="both", expand=True)
        xsb = ttk.Scrollbar(cvsb, orient="horizontal")
        ysb = ttk.Scrollbar(cvsb, orient="vertical")
        self.code = tk.Text(cvsb, wrap="none", font=theme.FONT_MONO, height=12,
                            xscrollcommand=xsb.set, yscrollcommand=ysb.set,
                            undo=False, cursor="arrow",
                            bg=theme.BG, fg=theme.TEXT, insertbackground=theme.TEXT)
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
        self.code.tag_raise("search")
        self.code.tag_raise("searchcur")

    def _bind_canvas(self):
        self.canvas.bind("<ButtonPress-1>", self._pan_start)
        self.canvas.bind("<B1-Motion>", self._pan_move)
        self.canvas.bind("<ButtonRelease-1>", self._pan_end)
        self.canvas.bind("<ButtonPress-2>", self._rot_start)
        self.canvas.bind("<B2-Motion>", self._rot_move)
        self.canvas.bind("<ButtonRelease-2>", self._rot_end)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Configure>", lambda e: self.render() if self.result else None)
        self._pan_data = None
        self._rot_data = None

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
            }
            new_paths.append(path)
        if not new_paths:
            return
        self._refresh_file_list()
        self.set_current_file(new_paths[0])

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
        self.current_line = None
        # 重置搜索状态(代码内容已重建, 旧命中行号失效)
        self._search_pattern = None
        self._search_hits = []
        self._search_idx = -1
        self.file_lbl.config(text=os.path.basename(path))
        self._fill_code()
        self._fill_legend()
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
        # G0
        self._legend_row(0, G0_COLOR, "G0  快速移动")
        for i, f in enumerate(self.result.feeds, 1):
            self._legend_row(i, self.palette[f], f"F{format(f, '.4f').rstrip('0').rstrip('.')}")

    def _legend_row(self, row, color, text):
        sw = tk.Label(self.legend, bg=color, width=3, height=1, relief="flat")
        sw.grid(row=row, column=0, padx=(0, 6), pady=1, sticky="w")
        ttk.Label(self.legend, text=text).grid(row=row, column=1, sticky="w")

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
        """当前旋转视角下, 所有刀路点投影后的 2D 包围盒 (a0,b0,a1,b1)"""
        q = self.quat
        a0 = b0 = float("inf")
        a1 = b1 = float("-inf")
        for pts3d in self._disp3d:
            for p in pts3d:
                a, b = project(p, q)
                if a < a0: a0 = a
                if b < b0: b0 = b
                if a > a1: a1 = a
                if b > b1: b1 = b
        if a0 == float("inf"):
            return (0.0, 0.0, 1.0, 1.0)
        return a0, b0, a1, b1

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
        self.render()

    def set_view_preset(self, name):
        self.quat = VIEW_QUAT[name]
        self.render()
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
        self.render()

    def render(self):
        self.canvas.delete("all")
        if not self.result or not self.result.moves:
            return
        w, x, y, z = self.quat
        scale, ox, oy = self.scale, self.offset[0], self.offset[1]
        show_g0 = self.show_g0.get()
        palette = self.palette

        # 内联四元数投影 + 相邻同色合并为折线(扁平坐标列表)
        polylines = []  # [(color, [sx,sy, sx,sy, ...])]
        for m, pts3d in zip(self.result.moves, self._disp3d):
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

        # 当前段高亮 + 加工方向箭头
        m = self.move_by_line.get(self.current_line)
        if m is not None and not (m.motion == "G0" and not self.show_g0.get()):
            idx = self._move_index[id(m)]
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

    def _arrow_at(self, x, y, dx, dy, color=CUR_COLOR, size=9):
        """在 (x,y) 处画一个指向 (dx,dy) 方向的实心三角箭头"""
        L = math.hypot(dx, dy)
        if L < 1e-6:
            return
        ux, uy = dx / L, dy / L
        px, py = -uy, ux                     # 垂直方向(箭头翼)
        size = min(size, L * 0.5)            # 避免过短段上箭头过大
        b1x = x - ux * size + px * size * 0.5
        b1y = y - uy * size + py * size * 0.5
        b2x = x - ux * size - px * size * 0.5
        b2y = y - uy * size - py * size * 0.5
        self.canvas.create_polygon(x, y, b1x, b1y, b2x, b2y,
                                   fill=color, outline="", tags="cur")

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
        self.render()

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

    def set_current_line(self, ln):
        self.current_line = ln
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
        self.set_current_line(ln)


def main():
    app = NCViewer()
    # 命令行可直接传文件路径
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.after(100, lambda: app.open_file(sys.argv[1]))
    app.mainloop()