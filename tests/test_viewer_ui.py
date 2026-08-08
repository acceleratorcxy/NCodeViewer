# -*- coding: utf-8 -*-
"""nc_viewer.viewer 界面结构测试

针对"界面去重"回归: 已去掉文件栏中重复的"打开文件…"按钮,
统一由顶部工具条作为文件入口。
"""
import os
import re
import tkinter as tk
from tkinter import ttk

import pytest

from nc_viewer import theme
from nc_viewer.viewer import NCViewer, _sample_dir

OPEN_TEXT = "打开文件…"
FILE_LIST_TEXT = "文件列表"


def _find(widget, text):
    """递归查找 text 属性等于 text 的所有控件"""
    found = []
    try:
        if str(widget.cget("text")) == text:
            found.append(widget)
    except tk.TclError:
        pass
    for w in widget.winfo_children():
        found.extend(_find(w, text))
    return found


@pytest.fixture(scope="module")
def app():
    try:
        a = NCViewer()
    except Exception as e:          # 无显示环境(CI)则跳过 GUI 测试
        pytest.skip(f"无法创建 Tk 窗口: {e}")
    a.withdraw()
    yield a
    a.destroy()


def test_open_file_button_exists_once(app):
    """全局应只有一个"打开文件…"按钮(顶部工具条), 不存在重复入口"""
    assert len(_find(app, OPEN_TEXT)) == 1


def test_file_pane_has_no_open_file_button(app):
    """左侧文件栏(含"文件列表"标签)内不应再有打开文件按钮"""
    labels = _find(app, FILE_LIST_TEXT)
    assert labels, "找不到文件列表标签"
    fs_frame = labels[0].master
    assert _find(fs_frame, OPEN_TEXT) == []


def test_file_listbox_wide_enough(app):
    """文件列表应足够宽以容纳长文件名(字符宽度>=30)"""
    assert int(app.file_listbox["width"]) >= 30


def test_top_toolbar_keeps_open_file_entry(app):
    """顶部工具条应保留"打开文件…"入口(统一文件入口)"""
    assert _find(app, OPEN_TEXT), "顶部工具条缺少打开文件入口"


def test_sample_dir_dev_samples_or_home_fallback():
    """开发环境返回项目样例目录; 无样例时(打包环境/纯净克隆)回退到存在的目录"""
    d = _sample_dir()
    assert os.path.isdir(d)
    expected = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "样例文件", "数控程序")
    if os.path.isdir(expected):
        assert d == expected


# ---------- 深色主题 ----------
def test_dark_theme_applied(app):
    """深色主题已应用: ttk 基座为 clam"""
    assert ttk.Style(app).theme_use() == "clam"


def test_canvas_uses_theme_bg(app):
    """画布背景应使用主题常量 CANVAS_BG"""
    assert app.canvas["bg"] == theme.CANVAS_BG


def test_theme_colors_valid():
    """主题颜色常量均为 #rrggbb 格式"""
    colors = [theme.BG, theme.PANEL, theme.INPUT_BG, theme.CANVAS_BG,
              theme.EDITOR_BG, theme.TEXT, theme.TEXT_DIM,
              theme.BORDER, theme.BORDER_LIGHT, theme.ACCENT,
              theme.ACCENT_HOVER, theme.SELECTION]
    for c in colors:
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", c), f"非法颜色值: {c}"


# ---------- 程序统计 / F 曲线 / 图例 ----------
NC_SMALL = "G01X10Y20F1000\nG02X20Y0I5J0F2000\nG0X0Y0\n"


def test_stats_panel_after_load(app, tmp_path):
    """加载程序后统计面板显示预期数值"""
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    assert app.stats_labels["x"]["text"] == "0.000 ~ 20.000"
    assert app.stats_labels["f"]["text"] == "1000 ~ 2000 · 2 档"
    assert app.stats_labels["s"]["text"] == "-"
    assert "G0:1" in app.stats_labels["g"]["text"]


def test_f_curve_data_excludes_g0(app, tmp_path):
    """F 曲线数据: 切削移动 (行号, F) 序列, G0 不参与"""
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    assert app._f_curve_data() == [(1, 1000.0), (2, 2000.0)]


def test_legend_chips_present_after_load(app, tmp_path):
    """加载程序后图例含色块与文字 (横向流式)"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10F1000\nX20F2000\n", encoding="utf-8")
    app.open_file(str(p))
    assert len(app.legend.winfo_children()) >= 4