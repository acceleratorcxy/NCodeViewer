# -*- coding: utf-8 -*-
"""nc_viewer.viewer 界面结构测试

针对"界面去重"回归: 已去掉文件栏中重复的"打开文件…"按钮,
统一由顶部工具条作为文件入口。
"""
import os
import tkinter as tk

import pytest

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