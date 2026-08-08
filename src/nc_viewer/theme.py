# -*- coding: utf-8 -*-
"""深色现代化主题 (ttk clam 基座, 纯标准库)

定义全局配色/字体常量与 apply_theme(root) 应用函数:
  - 深色调色板 (VS Code 风格), 与深色刀路画布协调
  - ttk clam 主题全面定制; clam 支持 active 状态, 按钮悬停无需手动绑定
  - 兼容 Python 3.8 + Tk 8.6.9 + Windows 7
所有颜色常量为 #rrggbb 格式, 便于单元测试。
"""
from __future__ import annotations

from tkinter import ttk

# ---------- 配色 (VS Code 风格深色) ----------
BG = "#1e1e1e"              # 窗口/面板/代码区背景
PANEL = "#252526"           # 侧栏/文件栏次级背景
INPUT_BG = "#2d2d30"        # 输入框背景
CANVAS_BG = "#1e1e1e"       # 刀路画布背景
TEXT = "#d4d4d4"            # 主文字
TEXT_DIM = "#9d9d9d"        # 次要文字/行号
BORDER = "#3f3f46"          # 边框/分隔线
ACCENT = "#0e639c"          # 强调色 (主按钮/选中)
ACCENT_HOVER = "#1177bb"    # 强调色悬停
SELECTION = "#094771"       # 列表/代码选中背景

# ---------- 字体 ----------
FONT_UI = ("Segoe UI", 10)       # Win7 自带; 中文由系统字体自动回退
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO = ("Consolas", 10)     # 代码/坐标等宽

# ---------- 样式名 ----------
BTN = "TButton"
BTN_ACCENT = "Accent.TButton"    # 主操作按钮 (打开文件…)


def apply_theme(root):
    """把深色主题应用到 root (主窗口) 及其全部 ttk 子控件。"""
    style = ttk.Style(root)
    style.theme_use("clam")

    # 基础容器与文字
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT, font=FONT_UI)
    style.configure("TLabelframe", background=BG, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, font=FONT_UI)
    style.configure("TLabelframe.Label", background=BG, foreground=TEXT, font=FONT_UI)
    style.configure("TSeparator", background=BORDER)
    style.configure("TPanedwindow", background=BG)

    # 按钮: clam 支持 active 状态, 悬停自动高亮
    style.configure(BTN, background=PANEL, foreground=TEXT, bordercolor=BORDER,
                    lightcolor=PANEL, darkcolor=PANEL, focuscolor=ACCENT,
                    font=FONT_UI, padding=(10, 4))
    style.map(BTN,
              background=[("pressed", ACCENT), ("active", ACCENT_HOVER)],
              foreground=[("pressed", "#ffffff"), ("active", "#ffffff")],
              bordercolor=[("pressed", ACCENT), ("active", ACCENT_HOVER)])
    # 主操作按钮: 强调色底
    style.configure(BTN_ACCENT, background=ACCENT, foreground="#ffffff",
                    bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                    focuscolor=ACCENT_HOVER, font=FONT_UI, padding=(12, 4))
    style.map(BTN_ACCENT,
              background=[("pressed", ACCENT_HOVER), ("active", ACCENT_HOVER)],
              bordercolor=[("pressed", ACCENT_HOVER), ("active", ACCENT_HOVER)])

    # 输入框
    style.configure("TEntry", fieldbackground=INPUT_BG, foreground=TEXT,
                    insertcolor=TEXT, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, padding=3)

    # 复选框
    style.configure("TCheckbutton", background=BG, foreground=TEXT,
                    selectcolor=ACCENT, indicatorcolor=PANEL,
                    bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                    focuscolor=ACCENT, font=FONT_UI)

    # 滚动条 (细条)
    style.configure("TScrollbar", background=PANEL, troughcolor=BG,
                    bordercolor=BORDER, arrowcolor=TEXT_DIM,
                    lightcolor=PANEL, darkcolor=PANEL, width=12)

    return style
