# -*- coding: utf-8 -*-
"""nc_viewer.viewer 界面结构测试

针对"界面去重"回归: 已去掉文件栏中重复的"打开文件…"按钮,
统一由顶部工具条作为文件入口。
"""
import os
import re
import math
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

import pytest

from nc_viewer import theme
from nc_viewer.tool import Tool
from nc_viewer.viewer import NCViewer, _sample_dir
from nc_viewer.geometry import orbit_rotate, project

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


def test_file_list_dual_zones_and_association(app, tmp_path):
    """文件列表双区 + mpf/apt 关联 + apt 点击仅更新刀具"""
    mpf = tmp_path / "prog1.MPF"
    mpf.write_text("G01X10Y20F1000\n", encoding="utf-8")
    apt = tmp_path / "prog1_I.aptsource"
    apt.write_text("PPRINT PROGNAME PROG1\n"
                   "CUTTER/ 20.000000,  3.000000,  7.000000,  3.000000,  0.000000,$\n"
                   "         0.000000, 30.000000\n", encoding="utf-8")
    app.open_file(str(mpf))
    app.add_files([str(apt)])
    assert str(mpf) in app.mp_paths
    assert str(apt) in app.apt_paths
    assert app.file_items[str(apt)]["partner"] == str(mpf)
    assert app.file_items[str(mpf)]["partner"] == str(apt)
    # 点击 apt: 不切换主视图, 只更新刀具 + 高亮关联 MPF
    before = app._current_path
    idx = app.apt_paths.index(str(apt))
    app.apt_listbox.selection_clear(0, "end")
    app.apt_listbox.selection_set(idx)
    app._on_apt_select(None)
    assert app._current_path == before
    assert app.tool is not None and app.tool.kind == "ball"
    assert app.file_listbox.curselection() == (app.mp_paths.index(str(mpf)),)


def test_file_listbox_wide_enough(app):
    """文件列表应足够宽以容纳长文件名(字符宽度>=24)"""
    assert int(app.file_listbox["width"]) >= 24


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


def test_buttons_size_to_text(app):
    """ttk 按钮按文本自然取宽 (修复 Tk 8.6.9 空 width 产生的宽度地板)"""
    frm = ttk.Frame(app)
    b1 = ttk.Button(frm, text="跳转")
    b2 = ttk.Button(frm, text="取消选择")
    b1.grid()
    b2.grid()
    frm.update_idletasks()
    assert b2.winfo_reqwidth() > b1.winfo_reqwidth()
    frm.destroy()


# ---------- 程序统计 / F 曲线 / 图例 ----------
NC_SMALL = "G01X10Y20F1000\nG02X20Y0I5J0F2000\nG0X0Y0\n"


def test_stats_panel_after_load(app, tmp_path):
    """加载程序后统计面板显示预期数值"""
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    assert app.stats_labels["x"]["text"] == "0.000 ~ 20.000"
    assert app.stats_labels["f"]["text"] == "1000 · 2000"
    assert app.stats_labels["s"]["text"] == "-"
    assert "G0:1" in app.stats_labels["g"]["text"]


def test_f_curve_data_excludes_g0(app, tmp_path):
    """F 曲线数据: 切削移动 (行号, 累计加工时间秒, F) 序列, G0 不参与但计入时间"""
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    data = app._f_curve_data()
    assert len(data) == 2
    assert [d[0] for d in data] == [1, 2], "行号递增"
    assert abs(data[0][1] - 1.3416) < 1e-3, "G01 22.36mm@1000 -> 1.34s"
    assert abs(data[1][1] - 2.0117) < 1e-3, "G02 弧长22.34mm@2000 -> 0.67s"
    assert [d[2] for d in data] == [1000.0, 2000.0]


def test_legend_chips_present_after_load(app, tmp_path):
    """加载程序后图例含色块与文字 (横向流式)"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10F1000\nX20F2000\n", encoding="utf-8")
    app.open_file(str(p))
    assert len(app.legend.winfo_children()) >= 4


# ---------- 缩放健壮性 / DPI / F 曲线拉伸 ----------
def test_legend_floats_on_canvas(app, tmp_path):
    """颜色图例漂浮在画布右上角 (画布子窗口项, 不随视图平移)"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y20F1000\nX20F2000\n", encoding="utf-8")
    app.open_file(str(p))
    app.update_idletasks()
    assert app.legend.master is app.canvas
    # 渲染后存在图例窗口项
    items = [i for i in app.canvas.find_withtag("legend") if app.canvas.type(i) == "window"]
    assert items
    # 平移后图例位置不变 (画布右上角)
    before = app.canvas.coords(items[0])
    from types import SimpleNamespace
    app._pan_start(SimpleNamespace(x=0, y=0))
    app._pan_move(SimpleNamespace(x=30, y=10))
    items = [i for i in app.canvas.find_withtag("legend") if app.canvas.type(i) == "window"]
    after = app.canvas.coords(items[0])
    assert before == after


def test_draw_f_curve_any_size(app):
    """F 曲线绘制函数可按任意尺寸运行 (数据: 行号, 累计秒, F)"""
    cv = tk.Canvas(app, width=600, height=400)
    data = [(1, 1.0, 1000.0), (2, 2.0, 2000.0), (3, 3.0, 1000.0)]
    app._draw_f_curve(cv, data, 600, 400)
    assert len(cv.find_all()) > 0
    cv.destroy()


def test_f_curve_unit_switch(app):
    """F 曲线横轴单位切换: 秒/分钟/小时刻度换算, 轴标签随单位"""
    cv = tk.Canvas(app, width=640, height=420)
    data = [(1, 100.0, 1000.0), (2, 200.0, 2000.0), (3, 300.0, 1000.0)]
    app._draw_f_curve(cv, data, 640, 420, axis="time", unit="sec")
    texts = [cv.itemcget(i, "text") for i in cv.find_all()
             if cv.type(i) == "text"]
    assert any("秒" in t for t in texts), "秒单位应有轴标签"
    assert any(t.isdigit() for t in texts), "秒刻度为数值"
    cv.delete("all")
    app._draw_f_curve(cv, data, 640, 420, axis="time", unit="min")
    texts = [cv.itemcget(i, "text") for i in cv.find_all()
             if cv.type(i) == "text"]
    assert any("分钟" in t for t in texts), "分钟单位应有轴标签"
    cv.delete("all")
    app._draw_f_curve(cv, data, 640, 420, axis="time", unit="hour")
    texts = [cv.itemcget(i, "text") for i in cv.find_all()
             if cv.type(i) == "text"]
    assert any("小时" in t for t in texts), "小时单位应有轴标签"
    cv.destroy()


def test_f_curve_zoom_and_scroll(app):
    """F 曲线时间轴: 滚轮以鼠标为锚横向缩放, 横向滚动条平移视口"""
    cv = tk.Canvas(app, width=640, height=420)
    data = [(1, 100.0, 1000.0), (2, 200.0, 2000.0), (3, 300.0, 1000.0),
            (4, 400.0, 2000.0), (5, 500.0, 1000.0)]       # 总 500s
    app._curve_data = data
    app._curve_view = None
    app._curve_axis_var = tk.StringVar(value="time")
    app._curve_cv = cv
    from types import SimpleNamespace
    # 滚轮放大: 以画布中心为锚
    app._curve_wheel(SimpleNamespace(x=320, delta=120))
    assert app._curve_view is not None, "放大后应有视口"
    t_lo, t_hi = app._curve_view
    assert t_hi - t_lo < 500, "滚轮放大后视口变窄"
    assert abs((t_lo + t_hi) / 2 - 250) < 60, "以鼠标位置为锚 (中心不变)"
    # 滚动条平移: 移到开头/结尾
    app._curve_hscroll("moveto", 0.0)
    assert app._curve_view[0] < 1e-6, "滚动到开头"
    app._curve_hscroll("moveto", 1.0)
    assert abs(app._curve_view[1] - 500) < 1e-6, "滚动到结尾"
    # 行号模式: 滚轮/滚动条不生效
    app._curve_axis_var.set("line")
    v0 = app._curve_view
    app._curve_wheel(SimpleNamespace(x=320, delta=120))
    assert app._curve_view == v0, "行号模式滚轮不应缩放"
    cv.destroy()


def test_f_curve_step_shape(app):
    """F 曲线为阶梯线: F 阶跃变化无斜线 (水平保持 + 垂直跳变)"""
    cv = tk.Canvas(app, width=600, height=400)
    data = [(1, 1.0, 1000.0), (2, 2.0, 2000.0), (3, 3.0, 1000.0)]
    app._draw_f_curve(cv, data, 600, 400, axis="time")
    main = [i for i in cv.find_withtag("curve") if cv.type(i) == "line"
            and float(cv.itemcget(i, "width")) == 2.5]
    coords = cv.coords(main[0])
    assert len(coords) == 12, "3 数据点 -> 6 阶梯点 (12 坐标)"
    # 水平段: (t0,F0)-(t1,F0) y 相同 (F 保持)
    assert coords[1] == coords[3], "水平段 F 值保持"
    # 垂直跳变: (t1,F0)-(t1,F1) x 相同, y 变化 (瞬间切换)
    assert coords[2] == coords[4], "跳变在同一时间点"
    assert coords[3] != coords[5], "F 值瞬间变化"
    # 后段同构: (t2,F1)-(t2,F2) 同 x
    assert coords[6] == coords[8]
    cv.destroy()


def test_f_curve_drag_coarse_then_finalize(app):
    """滚动条拖动: 轻量粗绘 (无描边), 停止后恢复精绘 (双线)"""
    cv = tk.Canvas(app, width=640, height=420)
    data = [(1, 1.0, 1000.0), (2, 2.0, 2000.0), (3, 3.0, 1000.0),
            (4, 4.0, 2000.0), (5, 5.0, 1000.0)]
    app._draw_f_curve(cv, data, 640, 420, axis="time", coarse=True)
    lines = [i for i in cv.find_withtag("curve") if cv.type(i) == "line"]
    assert len(lines) == 1, "粗绘只有主曲线 (无描边, 渲染轻量)"
    assert float(cv.itemcget(lines[0], "width")) == 2.5
    cv.delete("all")
    app._draw_f_curve(cv, data, 640, 420, axis="time", coarse=False)
    lines = [i for i in cv.find_withtag("curve") if cv.type(i) == "line"]
    assert len(lines) == 2, "精绘恢复双线 (描边+主曲线)"
    # 滚动条拖动调度粗绘, 且停止后自动精绘
    app._curve_data = data
    app._curve_axis_var = tk.StringVar(value="time")
    app._curve_cv = cv
    app._curve_view = (0.0, 5.0)
    app._curve_job = None
    app._curve_final_job = None
    app._curve_hscroll("moveto", 0.0)
    assert app._curve_final_job is not None, "拖动停止后应调度精绘"
    app.update()
    import time
    time.sleep(0.4)
    app.update()
    assert app._curve_final_job is None, "精绘已执行"
    cv.destroy()


def test_f_curve_decimates_large_data(app):
    """F 曲线大数据抽稀: 全览时每像素 ≤2 点 (create_line 大点数极慢)"""
    cv = tk.Canvas(app, width=800, height=420)
    data = [(i, float(i), 1000.0 + (i % 5) * 100.0) for i in range(1, 20001)]
    app._draw_f_curve(cv, data, 800, 420, axis="time")
    main = [i for i in cv.find_withtag("curve") if cv.type(i) == "line"
            and float(cv.itemcget(i, "width")) == 2.5]
    n = len(cv.coords(main[0])) // 2
    assert n <= (800 * 2 + 4) * 2 + 2, "抽稀后阶梯点数受控 (每像素 ≤2×2)"
    assert n > 100, "仍有足够点保持曲线形状"
    cv.destroy()


def test_f_curve_wheel_debounced(app):
    """滚轮缩放防抖: 连续滚动视口立即更新, 重绘合并为一次"""
    cv = tk.Canvas(app, width=640, height=420)
    data = [(1, 100.0, 1000.0), (2, 200.0, 2000.0), (3, 300.0, 1000.0),
            (4, 400.0, 2000.0), (5, 500.0, 1000.0)]
    app._curve_data = data
    app._curve_view = None
    app._curve_axis_var = tk.StringVar(value="time")
    app._curve_cv = cv
    app._curve_job = None
    from types import SimpleNamespace
    app._curve_wheel(SimpleNamespace(x=320, delta=120))
    v1 = app._curve_view
    assert v1 is not None, "视口立即更新"
    app._curve_wheel(SimpleNamespace(x=320, delta=120))    # 连续第二次
    assert app._curve_view != v1, "第二次滚动继续收窄"
    assert app._curve_job is not None, "防抖重绘任务挂起 (未立即重绘)"
    app.update()
    import time
    time.sleep(0.12)
    app.update()
    assert app._curve_job is None, "防抖任务已执行"
    cv.destroy()


def test_f_curve_axis_time_and_line(app):
    """F 曲线横轴: 默认加工时间 (mm:ss 刻度), 可切换行号 (底部配置)"""
    cv = tk.Canvas(app, width=640, height=420)
    data = [(1, 1.0, 1000.0), (2, 2.0, 2000.0), (3, 3.0, 1000.0)]
    app._draw_f_curve(cv, data, 640, 420, axis="time")
    texts = [cv.itemcget(i, "text") for i in cv.find_all()
             if cv.type(i) == "text"]
    assert any("加工时间" in t for t in texts), "应有横轴标签 (秒)"
    assert any(t == "0" or t == "1" for t in texts), "时间轴数值刻度存在"
    # 切换行号轴
    cv.delete("all")
    app._draw_f_curve(cv, data, 640, 420, axis="line")
    texts = [cv.itemcget(i, "text") for i in cv.find_all()
             if cv.type(i) == "text"]
    assert any(t == "行号" for t in texts), "行号轴应有轴标签"
    cv.destroy()


def test_stats_show_machining_time(app, tmp_path):
    """程序统计新增加工时间行: 100mm @ F1000 -> 6 秒显示 h:mm:ss"""
    app.file_items.clear()
    app._refresh_file_list()
    p = tmp_path / "t.nc"
    p.write_text("G01X100F1000\n", encoding="utf-8")
    app.open_file(str(p))
    assert app.stats_labels["time"]["text"] == "0:00:06"


def test_stats_time_follows_picked_segment(app, tmp_path):
    """画布拾取某段刀路: 统计加工时间显示该段时间; 取消后恢复全程序"""
    app.file_items.clear()
    app._refresh_file_list()
    # 两段: 段1 ≈9s, 段2 ≈15s (F1000 各 3s 下降 + 6s/12s 水平); 抬刀平面区分段
    p = tmp_path / "t.nc"
    p.write_text("G0Z50\n"            # 抬刀平面定位 (0.15s)
                 "G01Z0F1000\n"       # 段1 下降 50mm 3s
                 "G01X100F1000\n"     # 段1 水平 100mm 6s
                 "G0Z50\n"            # 段1 回升 0.15s
                 "G01Z0F1000\n"       # 段2 下降 50mm 3s
                 "G01X300F1000\n"     # 段2 水平 200mm 12s
                 "G0Z50\n", encoding="utf-8")   # 段2 回升 0.15s
    app.open_file(str(p))
    assert app.stats_labels["time"]["text"] == "0:00:25"   # 24.45s 向上取整
    # 拾取段2的移动 (行5) -> 显示段2时间 ≈15s
    app._set_pick_time(5)
    assert app.stats_labels["time"]["text"] == "0:00:16"   # 15.15s 向上取整
    # 取消拾取 -> 恢复全程序
    app._reset_pick_time()
    assert app.stats_labels["time"]["text"] == "0:00:25"


def test_play_progress_by_machining_time(app, tmp_path):
    """播放时右上角进度条按加工时间显示进度百分比; 复位隐藏"""
    app.file_items.clear()
    app._refresh_file_list()
    # 可见切削 2 条各 6s (G0 定位被前导跳过): 播放到第 1 条 -> 50%
    p = tmp_path / "t.nc"
    p.write_text("G0Y10\n"            # 定位 (前导跳过)
                 "G01X100F1000\n"     # 6s
                 "G01X200F1000\n",    # 6s
                 encoding="utf-8")
    app.open_file(str(p))
    app.state("normal")
    app.update()
    app._build_time_prefix()
    app._step_line_ctl(1)               # 行1 (G0 定位)
    app._step_line_ctl(1)               # 行2 (第一条切削)
    app._update_play_progress()
    assert float(app._prog_bar["value"]) == pytest.approx(50.1, abs=0.2), \
        "(0.03+6)s/12.03s ≈ 50.1% (基准含全部移动, 与统计口径一致)"
    assert app._prog_lbl["text"] == "50.1%"
    app._step_line_ctl(1)               # 行3 (第二条) -> 100%
    app._update_play_progress()
    assert float(app._prog_bar["value"]) == 100.0
    assert app._prog_lbl["text"] == "100.0%"
    app._reset_line()                   # 复位 -> 隐藏进度条
    assert app._prog_bar.winfo_manager() != "pack"
    app._stop_playback()


def test_file_list_delete_selected(app, tmp_path):
    """文件列表右键删除: 多选 MPF 删除, 当前文件被删后切换到剩余文件"""
    app.file_items.clear()                 # 共享 fixture 残留清理
    app._refresh_file_list()
    mpf1 = tmp_path / "a.MPF"
    mpf1.write_text("G01X10F1000\n", encoding="utf-8")
    mpf2 = tmp_path / "b.MPF"
    mpf2.write_text("G01X20F2000\n", encoding="utf-8")
    apt = tmp_path / "a_I.aptsource"
    apt.write_text("CUTTER/10.000,5.000,4.000,0.000,0.000,0.000,0.000\n"
                   "TOOLNO/1,50.000\n", encoding="utf-8")
    app.add_files([str(mpf1), str(mpf2), str(apt)])
    assert len(app.file_items) == 3
    # 多选两个 MPF 删除 (当前文件 a 在选中内)
    app.file_listbox.selection_set(0, 1)
    app._menu_delete_mpf()
    assert str(mpf1) not in app.file_items
    assert str(mpf2) not in app.file_items
    assert str(apt) in app.file_items, "APT 不受 MPF 删除影响"
    assert app._current_path is None, "MPF 全删后主视图清空 (APT 不设为主视图)"
    # APT 也删除 -> 主视图清空
    app.apt_listbox.selection_set(0)
    app._menu_delete_apt()
    assert not app.file_items
    assert app.result is None
    assert app.current_line is None


def test_file_list_pair_apt_tool(app, tmp_path):
    """文件列表右键配对: 「配对 APT 刀具」二级菜单列出所有 APT, 点击生效"""
    app.file_items.clear()                 # 共享 fixture 残留清理
    app._refresh_file_list()
    mpf = tmp_path / "b.MPF"                       # 与 APT 不同名, 自动关联找不到
    mpf.write_text("G01X10F1000\n", encoding="utf-8")
    apt = tmp_path / "c_I.aptsource"
    apt.write_text("CUTTER/10.000,5.000,4.000,0.000,0.000,0.000,0.000\n"
                   "TOOLNO/1,50.000\n", encoding="utf-8")
    apt2 = tmp_path / "d_I.aptsource"
    apt2.write_text("CUTTER/20.000,6.000,5.000,0.000,0.000,0.000,0.000\n"
                    "TOOLNO/2,60.000\n", encoding="utf-8")
    app.add_files([str(mpf), str(apt), str(apt2)])
    assert app.file_items[str(mpf)]["tool"] is None, "不同名应无自动关联刀具"
    assert app.file_items[str(mpf)].get("partner") is None
    # 二级菜单列出全部已加载 APT (每次弹出时重建)
    app._rebuild_pair_menu()
    labels = [app.pair_menu.entrycget(i, "label")
              for i in range(app.pair_menu.index("end") + 1)]
    assert labels == ["c_I.aptsource", "d_I.aptsource"], "子菜单应列出所有已加载 APT"
    # MPF 列表选中 b.MPF, 点击子菜单第一项 (c_I.aptsource) 生效
    app.file_listbox.selection_set(0)
    app.pair_menu.invoke(0)
    assert app.file_items[str(mpf)]["tool"] is not None, "配对后 MPF 应获得 APT 刀具"
    assert app.file_items[str(mpf)]["tool"] == app.file_items[str(apt)]["tool"]
    assert app.file_items[str(mpf)]["partner"] == str(apt)
    assert app.tool is not None, "当前显示文件刀具应刷新"
    assert app.tool_lbl["text"] != "-"
    # 无 APT 时子菜单为禁用占位项
    app.file_items.clear()
    app._refresh_file_list()
    app._rebuild_pair_menu()
    assert app.pair_menu.entrycget(0, "state") == "disabled"


def test_legend_hides_g0_when_absent(app, tmp_path):
    """图例只显示程序实际存在的对照: 无 G0 移动则不显示 G0 项"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10F1000\nX20F2000\n", encoding="utf-8")   # 无 G0
    app.open_file(str(p))
    texts = [c.cget("text") for c in app.legend.winfo_children()
             if c.winfo_class() == "TLabel"]
    assert not any("G0" in t for t in texts), "无 G0 程序不应显示 G0 图例"
    p2 = tmp_path / "t2.nc"
    p2.write_text("G0X10\nG01X20F1000\n", encoding="utf-8")     # 有 G0
    app.open_file(str(p2))
    texts = [c.cget("text") for c in app.legend.winfo_children()
             if c.winfo_class() == "TLabel"]
    assert any("G0" in t for t in texts), "有 G0 程序应显示 G0 图例"


def test_f_curve_single_polyline(app):
    """F 曲线为单条连续折线: 不按 F 值分色, 无档位色块图例"""
    cv = tk.Canvas(app, width=600, height=400)
    data = [(1, 1.0, 1000.0), (2, 2.0, 2000.0), (3, 3.0, 500.0),
            (4, 4.0, 2000.0)]                       # 跨多个 F 值
    app._draw_f_curve(cv, data, 600, 400)
    lines = [i for i in cv.find_withtag("curve") if cv.type(i) == "line"]
    assert len(lines) == 2, "双线绘制: 亮描边 + 主曲线 (清晰)"
    main = [i for i in cv.find_withtag("curve") if cv.type(i) == "line"
            and float(cv.itemcget(i, "width")) == 2.5]
    assert len(main) == 1, "主曲线一条 (连续连接)"
    assert len(cv.coords(main[0])) == 16, "4 数据点 -> 8 阶梯点 (16 坐标)"
    rects = [i for i in cv.find_withtag("curve") if cv.type(i) == "rectangle"]
    assert not rects, "不应有按 F 档位的色块图例"
    cv.destroy()


def test_enable_dpi_awareness_idempotent():
    """DPI 感知开启幂等且不抛异常"""
    from nc_viewer.viewer import _enable_dpi_awareness
    _enable_dpi_awareness()
    _enable_dpi_awareness()


# ---------- 逐行运行 (播放控制条) ----------
def test_draw_all_draws_full_path(app, tmp_path):
    """绘制到结尾: 一键画出整条刀路"""
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    app._draw_all()
    assert app.current_line == 3
    assert app._trace_active
    # 前导跳过 1 段, 其余 2 段全部画出
    assert len(app.canvas.find_withtag("path")) == 2


def test_play_batch_advances_multiple_lines(app, tmp_path):
    """合并跳行: 播放一次推进合并行数"""
    p = tmp_path / "t.nc"
    p.write_text("\n".join(f"G01X{i}F100" for i in range(1, 11)), encoding="utf-8")
    app.open_file(str(p))
    app.batch_cb.set("5")
    app._play_mode = "play"
    app.current_line = 0
    app._play_tick()
    assert app.current_line == 5
    app._play_tick()
    assert app.current_line == 10
    app._stop_playback()


def test_play_batch_invalid_clamped(app, tmp_path):
    """合并行数非法输入回退 1, 超界钳制到 100"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10F100\n", encoding="utf-8")
    app.open_file(str(p))
    app.batch_cb.set("abc")
    assert app._batch_lines() == 1
    app.batch_cb.set("500")
    assert app._batch_lines() == 100


def test_direction_arrow_prominent(app):
    """方向箭头增强: 绘制的箭头更大更醒目 (长段上不受尺寸保护压制)"""
    app._arrow_at(100, 100, 100, 0)
    polys = [i for i in app.canvas.find_all() if app.canvas.type(i) == "polygon"]
    assert polys, "未绘制箭头"
    x0, y0, x1, y1 = app.canvas.bbox(polys[-1])
    assert (x1 - x0) >= 10 and (y1 - y0) >= 10


# ---------- 刀具 (aptsource 解析 / 3D 模型 / 剖面图 / 自定义) ----------
def test_tool_parsed_from_sibling_aptsource(app, tmp_path):
    """加载 NC 时从同目录 aptsource 解析刀具并显示在统计行"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y20F1000\n", encoding="utf-8")
    (tmp_path / "t_I.aptsource").write_text(
        "CUTTER/ 20.000000,  3.000000,  7.000000,  3.000000,  0.000000,$\n"
        "         0.000000, 30.000000\n", encoding="utf-8")
    app.open_file(str(p))
    assert app.tool is not None and app.tool.kind == "ball"    # D20R3 圆鼻立铣刀
    assert "D20" in app.stats_labels["tool"]["text"]


def test_tool_model_draws_and_toggles(app, tmp_path):
    """刀具 3D 模型: 渲染出现, 开关关闭后消失"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y20F1000\nX20Y30\n", encoding="utf-8")
    app.open_file(str(p))
    app.tool = Tool("ball", {"d": 10, "r": 5, "l": 30})
    app.show_tool.set(True)
    app.set_current_line(1)
    assert len(app.canvas.find_withtag("toolmodel")) > 0
    app.show_tool.set(False)
    app.render()
    assert len(app.canvas.find_withtag("toolmodel")) == 0


def test_dimension_arrows_point_outward(app):
    """工程制图尺寸标注: 实心三角箭头, 左端朝左张开/右端朝右张开"""
    cv = tk.Canvas(app, width=400, height=200)
    app._dim_h(cv, 100, 100, 300, 100, "D20", "bottom")
    polys = [i for i in cv.find_all() if cv.type(i) == "polygon"]
    assert len(polys) == 2, "水平标注两端应为实心三角箭头"
    left = right = None
    for p in polys:
        xs = cv.coords(p)[::2]
        if any(abs(x - 100) < 1 for x in xs):      # 顶点在左端 x1=100
            left = xs
        elif any(abs(x - 300) < 1 for x in xs):    # 顶点在右端 x2=300
            right = xs
    assert left is not None and min(left) < 100 - 3, "左端箭头应朝左张开"
    assert right is not None and max(right) > 300 + 3, "右端箭头应朝右张开"
    # 垂直标注: 上端朝上张开/下端朝下张开
    cv2 = tk.Canvas(app, width=200, height=400)
    app._dim_v(cv2, 100, 80, 320, "L30", "right")
    polys2 = [i for i in cv2.find_all() if cv2.type(i) == "polygon"]
    assert len(polys2) == 2
    top = bottom = None
    for p in polys2:
        ys = cv2.coords(p)[1::2]
        if any(abs(y - 80) < 1 for y in ys):       # 顶点在上端 y1=80
            top = ys
        elif any(abs(y - 320) < 1 for y in ys):    # 顶点在下端 y2=320
            bottom = ys
    assert top is not None and min(top) < 80 - 3, "上端箭头应朝上张开"
    assert bottom is not None and max(bottom) > 320 + 3, "下端箭头应朝下张开"
    cv.destroy()
    cv2.destroy()
def test_tool_model_solid_body(app, tmp_path):
    """3D 刀具模型: 实体填充 (无 stipple 半透明抖动), 轮廓加粗"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y20F1000\nX20Y30\n", encoding="utf-8")
    app.open_file(str(p))
    app.tool = Tool("ball", {"d": 10, "r": 5, "l": 30})
    app.show_tool.set(True)
    app.set_current_line(1)
    bodies = [i for i in app.canvas.find_withtag("toolmodel")
              if app.canvas.type(i) == "polygon"]
    assert bodies, "应有刀具实体多边形"
    assert app.canvas.itemcget(bodies[0], "stipple") == "", "实体不应半透明"
    assert float(app.canvas.itemcget(bodies[0], "width")) >= 1, "轮廓线可见"


def test_tool_model_no_oversize_when_large(app, tmp_path):
    """3D 刀具模型: 直径投影 ≥24px 时不放大 (任何角度尺寸不超过几何投影),
    不再因轴向投影趋零而突然放大 8 倍"""
    from nc_viewer.geometry import VIEW_QUAT
    app.state("normal")
    app.geometry("1280x800+50+50")
    app.update()
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y20F1000\nX20Y30\n", encoding="utf-8")
    app.open_file(str(p))
    app.tool = Tool("ball", {"d": 10, "r": 5, "l": 30})
    app.show_tool.set(True)
    app.set_current_line(1)
    app.fit_view()
    h_max = 30 * app.scale + 10           # 未放大的最大投影 (模型高)
    for q in (VIEW_QUAT["XY"], VIEW_QUAT["XZ"], VIEW_QUAT["YZ"]):
        app.quat = q
        app.render()
        bb = app.canvas.bbox("toolmodel")
        assert bb, "模型应渲染"
        w, hh = bb[2] - bb[0], bb[3] - bb[1]
        assert max(w, hh) <= h_max, "大刀具不应放大 (实测 %.0f > %.0f)" % (
            max(w, hh), h_max)
    # 截面高光: 比实体浅 (非挖空感)
    body = [i for i in app.canvas.find_withtag("toolmodel")
            if app.canvas.type(i) == "polygon"][0]
    body_fill = app.canvas.itemcget(body, "fill")
    cirs = [i for i in app.canvas.find_withtag("toolmodel")
            if app.canvas.type(i) == "polygon" and
            app.canvas.itemcget(i, "fill") != body_fill]
    for c in cirs:
        assert app.canvas.itemcget(c, "fill") > body_fill, "截面应为高光 (比实体浅)"
    app.quat = (1.0, 0.0, 0.0, 0.0)      # 共享 fixture: 恢复默认视图


def test_tool_model_zoomed_when_tiny(app, tmp_path):
    """3D 刀具模型: 零件巨大使刀具直径投影 <24px 时放大到可见尺寸"""
    app.state("normal")
    app.geometry("1280x800+50+50")
    app.update()
    p = tmp_path / "t.nc"
    p.write_text("G01X1000Y1000F1000\nX2000Y2000\n", encoding="utf-8")
    app.open_file(str(p))
    app.tool = Tool("flat", {"d": 10, "r": 0, "l": 30})
    app.show_tool.set(True)
    app.set_current_line(1)
    app.fit_view()
    bb = app.canvas.bbox("toolmodel")
    assert bb, "模型应渲染"
    assert bb[2] - bb[0] >= 20, "小刀具应放大到可见尺寸"


def test_tool_tip_anchored_at_position(app, tmp_path):
    """刀尖对刀: 模型刀尖投影 ≈ 当前执行位置屏幕坐标"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y20F1000\n", encoding="utf-8")
    app.open_file(str(p))
    app.tool = Tool("flat", {"d": 10, "r": 0, "l": 30})
    app.show_tool.set(True)          # 共享 fixture: 显式重置开关状态
    app.set_current_line(1)
    pos = app.result.position_at_line(1)
    a, b = project(pos, app.quat)
    ex, ey = app.world_to_canvas(a, b)
    tips = [i for i in app.canvas.find_withtag("toolmodel")
            if app.canvas.type(i) == "oval"]
    assert tips, "缺少刀尖标记"
    x0, y0, x1, y1 = app.canvas.bbox(tips[0])
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    assert abs(cx - ex) < 3 and abs(cy - ey) < 3


def test_tool_checkbox_in_toolbar(app):
    """顶部工具条含「显示刀具」开关"""
    assert _find(app, "显示刀具")


def test_sidebar_sections_in_scroll(app):
    """统计/位置/刀具三区在同一滚动容器, 小屏可滚动"""
    inner = app._side_inner
    assert app.stats_labels["x"].master.master is inner
    assert app.pos_fields["X"].master.master.master is inner
    assert app.tool_cv.master.master is inner
    assert app.side_canvas.winfo_exists()


def test_small_screen_sidebar_scrolls(app, tmp_path):
    """1366x768 屏: 侧栏内容超出时滚动可达"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y20F1000\n", encoding="utf-8")
    app.open_file(str(p))
    app.state("normal")                       # 取消最大化再设窗口尺寸
    app.geometry("1366x728")
    app.update()
    vh = app.side_canvas.winfo_height()
    sr = app.side_canvas.cget("scrollregion")     # "x0 y0 x1 y1"
    h_sr = float(sr.split()[-1])
    assert h_sr > vh, "小屏下侧栏应可滚动"
    app.side_canvas.yview_moveto(1.0)             # 滚到底部可达
    assert app.side_canvas.yview()[0] > 0.0


def test_sidebar_min_width_during_sash_drag(app):
    """左右侧栏最小宽度: sash 拖过最小值时被钳回。

    回归: ttk 类绑定(移动 sash)先于实例绑定(钳制)执行时钳制被覆盖,
    须让类绑定先跑、实例钳制后跑。
    """
    app.state("normal")
    app.geometry("1600x900")
    app.update()
    up = app.upper_pane
    # 左: 文件栏 sash 向左拖到 10px (远小于 _min_fs)
    up.event_generate("<ButtonPress-1>", x=up.sashpos(0), y=200)
    up.event_generate("<B1-Motion>", x=10, y=200)
    app.update()
    assert up.sashpos(0) >= app._min_fs
    # 右: 统计侧栏 sash 向右拖到距右缘 10px (远小于 _min_side)
    uw = up.winfo_width()
    up.event_generate("<ButtonPress-1>", x=up.sashpos(1), y=200)
    up.event_generate("<B1-Motion>", x=uw - 10, y=200)
    app.update()
    assert uw - up.sashpos(1) >= app._min_side


def test_sidebar_content_fits_at_min_width(app, tmp_path):
    """右侧栏放到最窄时内容自适应: 各节请求宽度不超过视口 (无横向溢出)。

    回归: 内嵌剖面画布用 Canvas 默认宽 (10cm≈567px@144DPI) 会把刀具栏
    请求宽撑到 587, 最小宽度下内容溢出; 画布应只请求小宽度并靠 weight 拉伸。
    """
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y20F1000\n", encoding="utf-8")
    (tmp_path / "t_I.aptsource").write_text(
        "CUTTER/ 20.000000,  3.000000,  7.000000,  3.000000,  0.000000,$\n"
        "         0.000000, 30.000000\n", encoding="utf-8")
    app.open_file(str(p))
    app.state("normal")
    app.geometry("1280x800")
    app.update()
    up = app.upper_pane
    up.sashpos(1, up.winfo_width() - app._min_side)
    app.update()
    viewport = app.side_canvas.winfo_width()
    assert app._side_inner.winfo_reqwidth() <= viewport


def test_file_panel_min_fits_listbox(app):
    """文件栏最小宽度容纳列表自然宽 (最窄时文件名不被过度截断)"""
    assert app._min_fs >= app.file_listbox.winfo_reqwidth()
    app.state("normal")
    app.geometry("1280x800")
    app.update()
    up = app.upper_pane
    up.sashpos(0, app._min_fs)
    app.update()
    # 列表在最小宽度下不被压缩
    assert app.file_listbox.winfo_width() >= app.file_listbox.winfo_reqwidth() - 4


def test_pane_defaults_fit_content(app, tmp_path):
    """默认(未拖 sash)时三栏宽度=内容自然宽: 内容完整显示无横向挤压/溢出"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y20F1000\n", encoding="utf-8")
    (tmp_path / "t_I.aptsource").write_text(
        "CUTTER/ 20.000000,  3.000000,  7.000000,  3.000000,  0.000000,$\n"
        "         0.000000, 30.000000\n", encoding="utf-8")
    app._upper_touched = False                # 忽略前序测试的模拟拖拽
    app._rc_touched = False
    app.open_file(str(p))
    app.state("normal")
    app.geometry("1280x800")
    app.update()
    app._fit_pane_widths()
    app.update()
    # 右侧栏: 内容无横向溢出
    assert app._side_inner.winfo_reqwidth() <= app.side_canvas.winfo_width()
    # 文件栏: 列表不被压缩
    assert app.file_listbox.winfo_width() >= app.file_listbox.winfo_reqwidth() - 4
    # 底部右栏: 两列内容均不被压缩
    for cv, inner in zip(app._rc_canvases, app._rc_inners):
        assert inner.winfo_reqwidth() <= cv.winfo_width()


def test_bottom_right_columns_wheel_scroll(app, monkeypatch):
    """底部右栏两列: 滚轮按指针位置滚动对应滚动列 (全局滚轮分派)"""
    # 分派: 指针在左列 -> 左列画布; 右列 -> 右列画布; 侧栏 -> 侧栏画布
    assert app._wheel_scroll_target(app.loc_entry) is app._rc_canvases[0]
    assert app._wheel_scroll_target(app.lift_entry) is app._rc_canvases[1]
    assert app._wheel_scroll_target(app.stats_labels["x"]) is app.side_canvas
    # 端到端: 命中左列内容 -> 左列滚动 (winfo_containing 打桩, 与窗口堆叠无关)
    from types import SimpleNamespace
    monkeypatch.setattr(app, "winfo_containing", lambda x, y: app.loc_entry)
    lcv = app._rc_canvases[0]
    lcv.yview_moveto(0)
    app._on_side_wheel_global(SimpleNamespace(x_root=0, y_root=0, delta=-120))
    assert lcv.yview()[0] > 0.0, "滚动列未跟随滚轮滚动"


def test_seg_listbox_wheel_skips_column(app, monkeypatch):
    """滚轮在段列表上: 全局分派跳过 (列表自身滚轮由类绑定处理), 不滚所在列"""
    assert app._wheel_scroll_target(app.seg_listbox) is None
    from types import SimpleNamespace
    monkeypatch.setattr(app, "winfo_containing", lambda x, y: app.seg_listbox)
    rcv = app._rc_canvases[1]
    rcv.yview_moveto(0)
    app._on_side_wheel_global(SimpleNamespace(x_root=0, y_root=0, delta=-120))
    assert rcv.yview()[0] == 0.0, "所在列不应跟着滚动"


def test_segment_fields_and_navigation(app, tmp_path):
    """按段浏览: 段字段、抬刀平面修改重算、段导航"""
    p = tmp_path / "t.nc"
    p.write_text("G01X0Y0Z100F1000\n"
                 "G01X10Z50F1000\nG01X20Z-2F1000\nG01X30Z100F1000\n"
                 "G01X40Z50F1000\nG01X50Z-5F1000\nG01X60Z100F1000\n",
                 encoding="utf-8")
    app.open_file(str(p))
    assert len(app._segments) == 2
    app.set_current_line(2)
    assert app.pos_fields["段"].get() == "1"
    app.set_current_line(5)
    assert app.pos_fields["段"].get() == "2"
    # 修改抬刀平面 -> 重算 (抬刀 50 时 -2 与 -5 两次下降 -> 2 段)
    app.lift_entry.delete(0, "end")
    app.lift_entry.insert(0, "50")
    app._apply_lift()
    assert not app._lift_auto
    assert len(app._segments) == 2
    # 恢复自动
    app._auto_lift()
    assert app._lift_auto
    assert len(app._segments) == 2


def test_segment_mode_renders_only_current_segment(app, tmp_path):
    """仅显示当前段: 渲染过滤 + 播放钳制在段内"""
    p = tmp_path / "t.nc"
    p.write_text("G01X0Y0Z100F1000\n"
                 "G01X10Z50F1000\nG01X20Z-2F1000\nG01X30Z100F1000\n"
                 "G01X40Z50F1000\nG01X50Z-5F1000\nG01X60Z100F1000\n",
                 encoding="utf-8")
    app._lift_auto = True                     # 共享 fixture: 重置抬刀状态
    app.open_file(str(p))
    app._seg_only.set(True)
    app._toggle_seg_only()
    assert app._seg_filter is not None
    app._draw_all()
    assert app.current_line == 4              # 段1 末行
    # 播放钳制在段内
    app._play_mode = "play"
    app.current_line = 3
    app._play_tick()
    assert app.current_line == 4
    app._play_tick()
    assert app._play_mode is None             # 到段尾停止
    # 复位到段首 (段含下落前的抬刀平面定位行 -> 第 1 行)
    app._reset_line()
    assert app.current_line == 1
    # 关闭段模式恢复全局
    app._seg_only.set(False)
    app._toggle_seg_only()
    assert app._seg_filter is None
    app._draw_all()
    assert app.current_line == 7


def test_segment_multi_select_union_and_stats(app, tmp_path):
    """多段勾选: 过滤为勾选段并集; S/F 显示具体值; 统计随段变化"""
    p = tmp_path / "t.nc"
    p.write_text("G01X0Y0Z100F1000S5000\n"
                 "G01X10Z50F1000S5000\nG01X20Z-2F2000S8000\nG01X30Z100F2000S8000\n"
                 "G01X40Z50F3000S5000\nG01X50Z-5F3000S8000\nG01X60Z100F3000S8000\n"
                 "G01X70Z50F4000S5000\nG01X80Z-6F4000S8000\nG01X90Z100F4000S8000\n",
                 encoding="utf-8")
    app._lift_auto = True
    app.open_file(str(p))
    assert len(app._segments) == 3
    # S/F 具体值显示 (全程序)
    assert "5000" in app.stats_labels["s"]["text"]
    assert "8000" in app.stats_labels["s"]["text"]
    assert "1000" in app.stats_labels["f"]["text"]
    assert "4000" in app.stats_labels["f"]["text"]
    # 段模式: 勾选段1 -> 单范围过滤, 统计为段内 (F 无 3000/4000)
    # (段边界新语义: 段从抬刀平面定位移动(下落前)开始, 含 move0)
    app._seg_only.set(True)
    app.seg_listbox.selection_set(0)
    app._toggle_seg_only()
    assert app._seg_filter == [(0, 3)]
    assert "2000" in app.stats_labels["f"]["text"]
    assert "3000" not in app.stats_labels["f"]["text"]
    # 追加勾选段2 -> 并集过滤
    app.seg_listbox.selection_set(1)
    app._on_seg_list_select(None)
    assert app._seg_filter == [(0, 3), (4, 6)]
    assert "3000" in app.stats_labels["f"]["text"]
    assert "4000" not in app.stats_labels["f"]["text"]
    # 全选 3 段 -> 等同于全程序 (不过滤)
    app.seg_listbox.selection_set(2)
    app._on_seg_list_select(None)
    assert app._seg_filter is None
    # 全不勾选 -> 空过滤 (不显示任何段)
    app.seg_listbox.selection_clear(0, "end")
    app._on_seg_list_select(None)
    assert app._seg_filter == []
    # 关闭段模式 -> 全局
    app._seg_only.set(False)
    app._toggle_seg_only()
    assert app._seg_filter is None


def _write_segments(path, n_segs):
    """写 n_segs 个抬刀循环的测试程序 (每段 3 行移动, 抬刀平面 Z100)"""
    lines = []
    for i in range(n_segs):
        lines.append("G01X%dZ50F1000" % (3 * i))
        lines.append("G01X%dZ-2F1000" % (3 * i + 1))
        lines.append("G01X%dZ100F1000" % (3 * i + 2))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_seg_list_click_preserves_scroll(app, tmp_path):
    """段列表点击勾选: 重建标记后保持滚动位置 (不跳回顶部导致点击错位)"""
    p = tmp_path / "t.nc"
    _write_segments(p, 30)
    app.open_file(str(p))
    app.update_idletasks()
    lb = app.seg_listbox
    lb.yview_moveto(1.0)
    app.update_idletasks()
    top_before = lb.yview()[0]
    assert top_before > 0.1            # 确实滚到了底部
    lb.selection_clear(0, "end")
    lb.selection_set(29)               # 模拟点击列表底部的段
    app._on_seg_list_select(None)
    assert 29 in app._seg_checked
    assert abs(lb.yview()[0] - top_before) < 0.01, "点击后列表滚动位置应保持"


def test_seg_filter_change_during_trace_full_render(app, tmp_path):
    """轨迹播放中改变勾选段: 过滤变化使已画轨迹失效, 必须全量渲染勾选段

    回归: 此前走 _trace_redraw (按旧已画移动数重绘), 新勾选段不显示。
    """
    p = tmp_path / "t.nc"
    _write_segments(p, 5)
    app.open_file(str(p))
    app._seg_only.set(True)
    app._set_checked(0, True)
    app._apply_seg_filter()
    app._step_line_ctl(3)              # 进入轨迹模式并画出第 1 段
    assert app._trace_active
    assert len(app.canvas.find_withtag("path")) == 1     # 段1 一条折线
    # 模拟列表点击追加勾选第 5 段
    app.seg_listbox.selection_set(4)
    app._on_seg_list_select(None)
    assert not app._trace_active, "过滤变化后应退出轨迹模式做全量渲染"
    assert len(app.canvas.find_withtag("path")) == 2     # 段1 + 段5 都显示
    app._seg_only.set(False)
    app._toggle_seg_only()


def test_current_highlight_skips_filtered_move(app, tmp_path):
    """当前行落在被过滤的段上时: 不画当前段高亮与方向箭头 (防残留)"""
    p = tmp_path / "t.nc"
    _write_segments(p, 5)
    app.open_file(str(p))
    app._seg_only.set(True)
    app._set_checked(0, True)
    app._apply_seg_filter()
    app.set_current_line(14)           # 第 5 段内 (行 13~15), 不在勾选段
    assert not app.canvas.find_withtag("curseg")
    app._seg_only.set(False)
    app._toggle_seg_only()


def test_code_gutter_shows_segment_numbers(app, tmp_path):
    """NC 代码区行号槽显示段号: 段内行标注 S<n>, 段外行留空; 改抬刀平面后重建"""
    p = tmp_path / "t.nc"
    _write_segments(p, 3)
    with p.open("a", encoding="utf-8") as fh:
        fh.write("(done)\n")
    app.open_file(str(p))

    def gutter(ln):
        return app.code.get("%d.0" % ln, "%d.end" % ln).split("|", 1)[1][:4].strip()

    assert gutter(1) == "S1"           # 第 1 段 (行 1~3)
    assert gutter(5) == "S2"           # 第 2 段 (行 4~6)
    assert gutter(10) == ""            # 段外行无标注
    # 抬刀平面改到所有 Z 之上 -> 合并为 1 段, 标注随之重建
    app.lift_entry.delete(0, "end")
    app.lift_entry.insert(0, "1000")
    app._apply_lift()
    assert gutter(5) == "S1"
    app._auto_lift()


def test_draw_all_batches_coords_calls(app, tmp_path, monkeypatch):
    """绘制到结尾(大跳): canvas.coords 统一批量回写, 调用次数与移动数无关。

    回归: 逐移动回写合并折线的全量坐标是 O(n^2), 大程序"复位+绘制到结尾"卡死。
    """
    p = tmp_path / "t.nc"
    p.write_text("\n".join("G01X%dF1000" % i for i in range(1, 301)) + "\n",
                 encoding="utf-8")
    app.open_file(str(p))
    calls = []
    orig = app.canvas.coords

    def spy(*a, **kw):
        calls.append(1)
        return orig(*a, **kw)

    monkeypatch.setattr(app.canvas, "coords", spy)
    app._draw_all()
    assert app._trace_active
    # 300 个同色连续移动合并为 1 条折线, 末尾统一回写 (而非逐移动 299 次)
    assert len(calls) <= 3
    app._reset_line()


def test_crosshair_respans_and_dimmed(app, tmp_path):
    """十字虚线: 平移后重铺到可视区两端; 颜色暗化不刺眼 (当前点仍亮黄)"""
    from nc_viewer.geometry import CUR_COLOR, CUR_LINE_COLOR
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y20F1000\nG01X30Y40\n", encoding="utf-8")
    app.open_file(str(p))
    app.state("normal")
    app.geometry("1280x800+100+100")
    app.update()
    app.set_current_line(2)
    from types import SimpleNamespace
    app._pan_start(SimpleNamespace(x=400, y=300))
    app._pan_move(SimpleNamespace(x=620, y=390))
    app._pan_end(SimpleNamespace(x=620, y=390))
    lines = [i for i in app.canvas.find_withtag("curx")
             if app.canvas.type(i) == "line"]
    assert len(lines) == 2, "应有横竖两条十字虚线"
    cw, ch = app.canvas.winfo_width(), app.canvas.winfo_height()
    spans = [app.canvas.coords(i) for i in lines]
    assert any(x0 == 0 and x1 == cw for x0, y0, x1, y1 in spans), \
        "平移后横线应铺满可视区宽度"
    assert any(y0 == 0 and y1 == ch for x0, y0, x1, y1 in spans), \
        "平移后竖线应铺满可视区高度"
    assert all(app.canvas.itemcget(i, "fill") == CUR_LINE_COLOR for i in lines)
    ovals = [i for i in app.canvas.find_withtag("cur")
             if app.canvas.type(i) == "oval"]
    assert ovals and all(app.canvas.itemcget(i, "fill") == CUR_COLOR
                         for i in ovals)


def test_canvas_click_pick_jumps_to_line(app, tmp_path):
    """画布单击刀路: 拾取最近可见移动并跳转对应 NC 行; 拖动/点空白不触发"""
    from types import SimpleNamespace
    from nc_viewer.geometry import project
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y10F1000\nG01X110Y10\nG01X110Y110\n", encoding="utf-8")
    app.open_file(str(p))
    app.state("normal")
    app.geometry("1280x800+100+100")
    app.update()
    app.fit_view()                # 打开时画布未定型, 重新适配使比例确定

    def click(wx, wy, drag=False):
        a, b = project((wx, wy, 0.0), app.quat)
        mx, my = app.world_to_canvas(a, b)
        app._pan_start(SimpleNamespace(x=mx, y=my))
        if drag:
            app._pan_move(SimpleNamespace(x=mx + 120, y=my + 80))
            app._pan_end(SimpleNamespace(x=mx + 120, y=my + 80))
        else:
            app._pan_end(SimpleNamespace(x=mx, y=my))

    click(60.0, 10.0)             # 第 2 行移动 (X10..X110) 的中点
    assert app.current_line == 2
    click(110.0, 70.0)            # 第 3 行移动上的一点
    assert app.current_line == 3
    click(-400.0, -400.0)         # 空白处: 清除选择
    assert app.current_line is None
    app.set_current_line(2)
    click(60.0, 10.0, drag=True)  # 拖动平移: 不触发拾取也不清除
    assert app.current_line == 2


def test_canvas_click_empty_clears_selection(app, tmp_path):
    """点击无刀路区域: 清除当前位置选择 (画布标记/位置字段/代码高亮)"""
    from types import SimpleNamespace
    from nc_viewer.geometry import project
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y10F1000\nG01X110Y10\n", encoding="utf-8")
    app.open_file(str(p))
    app.state("normal")
    app.geometry("1280x800+100+100")
    app.update()
    app.fit_view()

    def click(wx, wy):
        a, b = project((wx, wy, 0.0), app.quat)
        mx, my = app.world_to_canvas(a, b)
        app._pan_start(SimpleNamespace(x=mx, y=my))
        app._pan_end(SimpleNamespace(x=mx, y=my))

    click(60.0, 10.0)             # 先点中刀路建立选择
    assert app.current_line == 2
    assert app.canvas.find_withtag("cur")
    assert app.pos_fields["X"].get() == "110.000"
    click(-500.0, -500.0)         # 点无刀路区域 -> 清除
    assert app.current_line is None
    assert not app.canvas.find_withtag("cur")
    assert not app.canvas.find_withtag("curseg")
    assert app.pos_fields["X"].get() == "-"
    assert app.pos_fields["行"].get() == "-"
    assert app.pos_fields["本行"].get() == "-"
    assert app.code.tag_ranges("cur") == ()
    assert app.loc_entry.get() == ""


def test_canvas_click_pick_respects_visibility(app, tmp_path):
    """拾取只看可见刀路: G0 隐藏时快移点不中, 显示后可点"""
    from types import SimpleNamespace
    from nc_viewer.geometry import project
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y0F1000\nG0X110Y0\nG01X110Y100F1000\n", encoding="utf-8")
    app.open_file(str(p))
    app.state("normal")
    app.geometry("1280x800+100+100")
    app.update()
    app.fit_view()

    def click(wx, wy):
        a, b = project((wx, wy, 0.0), app.quat)
        mx, my = app.world_to_canvas(a, b)
        app._pan_start(SimpleNamespace(x=mx, y=my))
        app._pan_end(SimpleNamespace(x=mx, y=my))

    app.show_g0.set(False)
    app._view_refresh()
    click(60.0, 0.0)              # G0 段中点 (已隐藏, 距可见段 >12px)
    assert app.current_line is None
    app.show_g0.set(True)
    app._view_refresh()
    click(60.0, 0.0)              # G0 段中点 (显示后可点中)
    assert app.current_line == 2


def test_canvas_click_pick_respects_seg_filter(app, tmp_path):
    """段过滤后, 被过滤段的刀路点不中"""
    from types import SimpleNamespace
    from nc_viewer.geometry import project
    p = tmp_path / "t.nc"
    # 两段抬刀循环, X 相距 100 (段2 刀路距段1 任意可见移动 >12px)
    p.write_text("G01X0Z50F1000\nG01X5Z-2F1000\nG01X10Z100F1000\n"
                 "G01X100Z50F1000\nG01X105Z-2F1000\nG01X110Z100F1000\n",
                 encoding="utf-8")
    app.open_file(str(p))
    app.state("normal")
    app.geometry("1280x800+100+100")
    app.update()
    app.fit_view()
    app._seg_only.set(True)
    app._set_checked(0, True)
    app._apply_seg_filter()
    # 段2 的移动 (行5: X100Z50 -> X105Z-2) 的中点, 段2 未勾选
    a, b = project((102.5, 0.0, 24.0), app.quat)
    mx, my = app.world_to_canvas(a, b)
    app._pan_start(SimpleNamespace(x=mx, y=my))
    app._pan_end(SimpleNamespace(x=mx, y=my))
    assert app.current_line is None
    app._seg_only.set(False)
    app._toggle_seg_only()


def test_direction_arrows_fixed_size_after_zoom(app, tmp_path):
    """方向箭头固定像素大小: 滚轮缩放后重绘为固定尺寸 (不被 canvas.scale 放大)"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y0F1000\nG01X200Y0\n", encoding="utf-8")
    app.open_file(str(p))
    app.state("normal")
    app.geometry("1280x800+100+100")
    app.update()
    app.fit_view()
    app.set_current_line(2)

    def arrow_span():
        spans = []
        for i in app.canvas.find_withtag("cur"):
            if app.canvas.type(i) == "polygon":
                co = app.canvas.coords(i)
                xs, ys = co[0::2], co[1::2]
                spans.append(max(max(xs) - min(xs), max(ys) - min(ys)))
        return spans

    assert arrow_span() and max(arrow_span()) <= 20      # 固定 14px 级
    app.zoom_at(2.0, None)                               # 放大 2 倍
    assert arrow_span() and max(arrow_span()) <= 20      # 缩放后仍固定尺寸


def test_roll_mode_when_middle_press_off_path(app, tmp_path):
    """中键未吸附刀路点(空白处)按下: 拖动绕画布中心做平面旋转 (滚转), 中心不动"""
    from types import SimpleNamespace
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y0F1000\nG01X200Y0\n", encoding="utf-8")
    app.open_file(str(p))
    app.quat = (1.0, 0.0, 0.0, 0.0)      # 共享 fixture: 复位视图
    app.state("normal")
    app.geometry("1280x800+100+100")
    app.update()
    app.fit_view()

    def screen_of(wx, wy, wz=0.0):
        a, b = project((wx, wy, wz), app.quat)
        return app.world_to_canvas(a, b)

    # 滚转锚点 = 画布中心; 沿以中心为圆心的圆弧拖动 (视觉 CCW 45°)
    cx = app.canvas.winfo_width() / 2
    cy = app.canvas.winfo_height() / 2
    r = 120.0
    start = SimpleNamespace(x=cx + r, y=cy)
    end = SimpleNamespace(x=cx + r * math.cos(math.radians(45)),
                          y=cy - r * math.sin(math.radians(45)))
    pt = (200.0, 0.0, 0.0)                     # 观察点: 刀路右端
    x0, y0 = screen_of(*pt)
    ang0 = math.atan2(-(y0 - cy), x0 - cx)     # 绕画布中心的视觉角度
    app._rot_start(start)                      # 按下处非顶点 -> 未吸附
    assert app._rot_mode == "roll"
    app._rot_move(start)
    app._rot_move(end)
    app._rot_end(None)
    x1, y1 = screen_of(*pt)
    ang1 = math.atan2(-(y1 - cy), x1 - cx)
    assert math.degrees(ang1 - ang0) == pytest.approx(45, abs=2)
    # 画布中心的世界点旋转后仍在画布中心 (固定不动)
    ax1, ay1 = screen_of(*app._rot_center)
    assert ax1 == pytest.approx(cx, abs=0.5)
    assert ay1 == pytest.approx(cy, abs=0.5)
    # 吸附到刀路点按下: 仍为轨道旋转模式 (旋转吸附按顶点, 取端点附近)
    px, py = screen_of(198.0, 0.0)
    app._rot_start(SimpleNamespace(x=px, y=py))
    assert app._rot_mode == "orbit"
    app._rot_end(None)


def test_click_pick_during_trace_views_without_moving_execution(app, tmp_path):
    """轨迹/暂停中点击已绘制刀路: 仅查看对应行 (代码高亮+位置字段),
    执行位置不动, 续播从停下的地方继续"""
    from types import SimpleNamespace
    from nc_viewer.geometry import project
    p = tmp_path / "t.nc"
    p.write_text("\n".join("G01X%dF1000" % (10 * i) for i in range(1, 21)) + "\n",
                 encoding="utf-8")
    app.open_file(str(p))
    app.quat = (1.0, 0.0, 0.0, 0.0)      # 共享 fixture: 复位视图
    app.state("normal")
    app.geometry("1280x800+100+100")
    app.update()
    app.fit_view()
    app._step_line_ctl(15)            # 轨迹模式: 执行到第 15 行
    assert app.current_line == 15
    # 点击第 5 行刀路 (已绘制部分): 查看该行
    a, b = project((45.0, 0.0, 0.0), app.quat)
    mx, my = app.world_to_canvas(a, b)
    app._pan_start(SimpleNamespace(x=mx, y=my))
    app._pan_end(SimpleNamespace(x=mx, y=my))
    assert app.current_line == 15, "执行位置不应被点击查看改变"
    assert app.pos_fields["行"].get() == "5"      # 字段显示查看的行
    assert str(app.code.tag_ranges("viewline")[0]) == "5.0"   # 代码区查看高亮
    # 续播从停下的地方 (15) 继续
    app.batch_cb.set("1")             # 共享 fixture: 合并行数显式置 1
    app._play_toggle()
    assert app.current_line == 16
    app._stop_playback()


def test_click_pick_limited_to_drawn_in_trace(app, tmp_path):
    """轨迹模式只能点中已绘制刀路: 未绘制部分不参与拾取, 点击视为空白
    (取消查看, 执行位置保留)"""
    from types import SimpleNamespace
    from nc_viewer.geometry import project
    p = tmp_path / "t.nc"
    p.write_text("\n".join("G01X%dF1000" % (10 * i) for i in range(1, 21)) + "\n",
                 encoding="utf-8")
    app.open_file(str(p))
    app.quat = (1.0, 0.0, 0.0, 0.0)      # 共享 fixture: 复位视图
    app.state("normal")
    app.geometry("1280x800+100+100")
    app.update()
    app.fit_view()
    app._step_line_ctl(5)             # 只画到第 5 行
    # 点击第 15 行刀路位置 (未绘制, 距已绘制部分 >12px)
    a, b = project((145.0, 0.0, 0.0), app.quat)
    mx, my = app.world_to_canvas(a, b)
    app._pan_start(SimpleNamespace(x=mx, y=my))
    app._pan_end(SimpleNamespace(x=mx, y=my))
    assert app.current_line == 5, "执行位置应保留"
    assert app.pos_fields["行"].get() == "5"


def test_canvas_resize_keeps_path_items(app, tmp_path):
    """窗口/画布尺寸变化不重绘刀路 (场景投影未变): path 图元 id 保持不变"""
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    app.state("normal")
    app.geometry("1280x800+100+100")
    app.update()
    before = app.canvas.find_withtag("path")
    assert before
    app.geometry("1400x860+100+100")
    app.update()
    assert app.canvas.find_withtag("path") == before, "尺寸变化不应重建刀路图元"


def test_rotation_reuses_path_items(app, tmp_path):
    """旋转/滚转复用画布图元 (仅 coords 更新, 不 delete/create): id 不变"""
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    app.state("normal")
    app.geometry("1280x800+100+100")
    app.update()
    before = app.canvas.find_withtag("path")
    assert before
    app.quat = orbit_rotate(app.quat, 30, 20)
    app.render()
    assert app.canvas.find_withtag("path") == before, "旋转应原位更新坐标而非重建"


def test_rotation_coalesces_refresh(app, tmp_path, monkeypatch):
    """旋转拖动合并刷新: 连续 motion 不逐事件渲染, 释放时一次性最终渲染"""
    from types import SimpleNamespace
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    app.state("normal")
    app.geometry("1280x800+100+100")
    app.update()
    calls = []
    orig = app._view_refresh
    monkeypatch.setattr(app, "_view_refresh",
                        lambda: (calls.append(1), orig()))
    app._rot_start(SimpleNamespace(x=100, y=100))
    app._rot_move(SimpleNamespace(x=120, y=110))
    app._rot_move(SimpleNamespace(x=140, y=120))
    app._rot_move(SimpleNamespace(x=160, y=130))
    assert len(calls) <= 1, "连续 motion 不应逐事件渲染 (最多 1 次挂起)"
    app._rot_end(None)            # 释放: 立即最终全量渲染
    assert len(calls) >= 1
def test_progress_dialog_during_load(app, tmp_path, monkeypatch):
    """加载文件进度显示: 超延时任务显示, 进度单调推进, 结束后隐藏"""
    app._progress_delay = 0                      # 测试: 立即显示
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    events = []
    orig = app._progress_update

    def spy(text, frac):
        events.append((text, frac))
        orig(text, frac)

    monkeypatch.setattr(app, "_progress_update", spy)
    app.add_files([str(p)])
    assert events, "应有进度更新"
    fracs = [f for _, f in events]
    assert fracs == sorted(fracs), "进度应单调推进"
    assert fracs[-1] <= 1.0
    assert app._prog is None, "加载结束后进度应隐藏"


def test_progress_dialog_mechanics(app):
    """进度内嵌显示机制: 延迟内不显示/超延时才显示/标签与进度条更新/结束隐藏"""
    app._progress_delay = 999                    # 快任务不显示
    app._progress_begin()
    app._progress_update("x", 0.1)
    assert app._prog is None
    assert app._prog_bar.winfo_manager() != "pack"
    app._progress_delay = 0                      # 超延时 -> 显示
    app.update()
    app._progress_update("读取 a", 0.3)
    assert app._prog is not None
    assert app._prog["lbl"]["text"] == "读取 a"
    assert float(app._prog["bar"]["value"]) == 30.0
    # 内嵌在顶部栏右上角 (不弹独立窗口)
    assert app._prog_lbl.winfo_manager() == "pack"
    assert app._prog_bar.winfo_manager() == "pack"
    assert not any(isinstance(c, tk.Toplevel) for c in app.winfo_children())
    app._progress_end()
    assert app._prog is None
    assert app._prog_lbl.winfo_manager() != "pack"
    assert app._prog_bar.winfo_manager() != "pack"


def test_progressbar_style_is_dark(app):
    """进度条深色样式: clam 默认进度条是浅色(白), 与深色弹窗不协调"""
    s = ttk.Style(app)
    assert s.lookup("TProgressbar", "troughcolor") == theme.INPUT_BG
    assert s.lookup("TProgressbar", "background") == theme.ACCENT


def test_playback_centers_current_line(app, tmp_path):
    """播放(动画路径)时当前行在代码区居中; 仅程序开头/结尾允许不在中间;
    非播放手动跳转仍是最小滚动 see"""
    p = tmp_path / "t.nc"
    p.write_text("\n".join("G01X%dF1000" % i for i in range(1, 401)) + "\n",
                 encoding="utf-8")
    app.open_file(str(p))
    app.state("normal")
    app.geometry("1280x1200+100+100")     # 加高窗口使居中/贴边差异可区分
    app.update()
    total = 400

    def top_line():
        return int(app.code.yview()[0] * total) + 1

    def middle_line():
        f0, f1 = app.code.yview()
        return int((f0 + f1) / 2 * total) + 1

    app._step_line_ctl(200)                  # 大跳转 (see 远距离也会居中)
    for _ in range(10):
        app._step_line_ctl(1)                # 逐行播放推进到 210
    assert abs(middle_line() - 210) <= 2, "逐行播放时当前行应保持居中"
    # 非播放手动跳转: see 最小滚动, 视口内不重排
    before = app.code.yview()
    app.set_current_line(211)
    assert app.code.yview() == before
    app.set_current_line(3, animate=True)    # 程序开头: 顶行即第 1 行
    assert top_line() == 1
    app.set_current_line(400, animate=True)  # 程序结尾: 底部贴尾行
    assert app.code.yview()[1] == 1.0
    app._stop_playback()


def test_position_fields_boxed_values(app, tmp_path):
    """当前位置: 只读框展示 X/Y/Z/S/F/G/行/本行"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y20F1000\n", encoding="utf-8")
    app.open_file(str(p))
    app.set_current_line(1)
    assert app.pos_fields["X"].get() == "10.000"
    assert app.pos_fields["Y"].get() == "20.000"
    assert app.pos_fields["Z"].get() == "0.000"
    assert app.pos_fields["X"]["state"] == "readonly"
    assert app.pos_fields["S"].get() == "-"        # 无 S
    assert app.pos_fields["F"].get() == "1000"
    assert app.pos_fields["G"].get() == "G1"
    assert app.pos_fields["行"].get() == "1"
    assert app.pos_fields["本行"].get() == "G01X10Y20F1000"


def test_position_benhang_label_and_value_two_rows(app):
    """当前位置「本行」: 标签与内容拆成两行 (标签行在上, 值框整行跨满在下)"""
    ent = app.pos_fields["本行"]
    parent = ent.master
    info = ent.grid_info()
    assert info["row"] == 1                       # 值框在第二行
    assert "e" in info["sticky"] and "w" in info["sticky"]   # 整行跨满
    labels = [w for w in parent.winfo_children()
              if isinstance(w, ttk.Label) and str(w.cget("text")) == "本行"]
    assert labels, "本行标签不存在"
    assert labels[0].grid_info()["row"] == 0      # 标签在第一行


def test_tool_profile_inline_drawn(app, tmp_path):
    """刀具剖面图直接内嵌在刀具栏 (无需点击二级窗口)"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10Y20F1000\n", encoding="utf-8")
    (tmp_path / "t_I.aptsource").write_text(
        "CUTTER/ 20.000000,  3.000000,  7.000000,  3.000000,  0.000000,$\n"
        "         0.000000, 30.000000\n", encoding="utf-8")
    app.open_file(str(p))
    app.update_idletasks()
    assert len(app.tool_cv.find_all()) > 0
    app.tool = None
    app._refresh_tool_ui()
    assert len(app.tool_cv.find_all()) == 0


def test_tool_profile_draw_any_size(app):
    """剖面图绘制函数可按任意尺寸运行"""
    cv = tk.Canvas(app, width=480, height=520)
    app._draw_tool_profile(cv, Tool("flat", {"d": 20, "r": 3, "l": 30}), 480, 520)
    assert len(cv.find_all()) > 0
    cv.destroy()


def test_toolbar_toggle_keeps_trace(app, tmp_path):
    """播放中切换显示开关: 不退出轨迹模式 (G0/刀具开关走 _view_refresh)"""
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    app._trace_begin()
    app.set_current_line(2, animate=True)
    assert app._trace_active
    app._view_refresh()
    assert app._trace_active
    app.render()          # 全量渲染才退出轨迹
    assert not app._trace_active


def test_tool_setup_fields_follow_type(app):
    """自定义窗口: 切换类型后规格输入行跟随变化"""
    app.show_tool_setup()
    assert len(app._setup_entries) == 3          # 默认平底: D/R/L
    app._setup_kind_var.set("中心钻")
    app._setup_kind_cb.event_generate("<<ComboboxSelected>>")
    app.update_idletasks()
    assert "point" in app._setup_entries and "r" not in app._setup_entries
    app._setup_kind_var.set("反锥立铣刀")
    app._setup_kind_cb.event_generate("<<ComboboxSelected>>")
    app.update_idletasks()
    assert "taper" in app._setup_entries
    app._setup_win.destroy()


def test_lead_skip_skips_origin_approach(app, tmp_path):
    """从原点出发的起始进给段被跳过 (程序从第一个可解析点开始)"""
    p = tmp_path / "t.nc"
    p.write_text("G0X0Y0\nG01X10Y0F100\nX20Y0\n", encoding="utf-8")
    app.open_file(str(p))
    assert app._lead_skip == 2
    app.update()
    # 全量渲染不画被跳过的段
    assert len(app.canvas.find_withtag("path")) == 1
    # 轨迹模式同样跳过
    app._trace_begin()
    app.set_current_line(3, animate=True)
    assert len(app.canvas.find_withtag("path")) == 1


def test_lead_skip_zero_for_non_origin_start(app, tmp_path):
    """首段不从原点出发时不跳过"""
    p = tmp_path / "t.nc"
    p.write_text("X10Y20\nG01X20Y30F100\n", encoding="utf-8")
    app.open_file(str(p))
    assert app._lead_skip == 0


def test_horizontal_scrollbar_spans_code_width(app, tmp_path):
    """NC 代码横向滚动条铺满代码区宽度 (grid 布局)"""
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    app.update_idletasks()
    # 找到横向滚动条: 父容器与代码相同
    for w in app.code.master.winfo_children():
        if w.winfo_class() == "TScrollbar" and str(w.cget("orient")) == "horizontal":
            xsb = w
            break
    else:
        pytest.fail("未找到横向滚动条")
    cvsb = app.code.master
    assert abs(xsb.winfo_width() - (cvsb.winfo_width() - ysb_width(cvsb))) < 8


def ysb_width(cvsb):
    for w in cvsb.winfo_children():
        if w.winfo_class() == "TScrollbar" and str(w.cget("orient")) == "vertical":
            return w.winfo_width()
    return 0


def test_playback_controls_present(app):
    """播放控制条按钮存在"""
    assert _find(app, "复位"), "缺少复位按钮"
    assert _find(app, "演示到行"), "缺少演示到行按钮"
    assert _find(app, "直达行"), "缺少直达行按钮"


def test_playback_controls_under_canvas(app):
    """播放控制条位于画布下方 (与画布同属 cv_frame)"""
    btn = _find(app, "复位")[0]
    assert btn.master.master == app.canvas.master


def test_set_target_does_not_move_position(app, tmp_path):
    """点击选中目标行: 目标记录且高亮, 执行位置不动"""
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    app.set_current_line(1)
    app._set_target(2)
    assert app._target_line == 2
    assert app.current_line == 1
    assert "target" in app.code.tag_names("2.0")


def test_target_line_toggle_and_clear_button(app, tmp_path):
    """目标行: 点击选中, 再点同一行清空; 控制条「清空」按钮可清空且状态跟随"""
    p = tmp_path / "t.nc"
    p.write_text("G01X10F1000\nG01X20\nG01X30\n", encoding="utf-8")
    app.open_file(str(p))
    # 初始: 无目标, 按钮禁用
    assert app._target_line is None
    assert str(app.target_clear_btn["state"]) == "disabled"
    # 点击第 2 行 -> 选中
    app._toggle_target(2)
    assert app._target_line == 2
    assert "target" in app.code.tag_names("2.0")
    assert app.target_lbl["text"] == "目标行: 2"
    assert str(app.target_clear_btn["state"]) == "normal"
    # 再点同一行 -> 清空
    app._toggle_target(2)
    assert app._target_line is None
    assert app.code.tag_ranges("target") == ()
    assert app.target_lbl["text"] == "目标行: -"
    assert str(app.target_clear_btn["state"]) == "disabled"
    # 选中后经「清空」按钮清空
    app._toggle_target(3)
    assert app._target_line == 3
    app.target_clear_btn.invoke()
    assert app._target_line is None
    assert app.code.tag_ranges("target") == ()
    assert str(app.target_clear_btn["state"]) == "disabled"


def test_run_to_target_instant(app, tmp_path):
    """直达行: 无动画直接运算到目标行"""
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    app._set_target(2)
    app._run_to_target(False)
    assert app.current_line == 2


def test_demo_step_size_adaptive():
    """演示步长: 距离 1/80 向上取整, 已过/到达停止"""
    assert NCViewer._demo_step_size(100, 0) == 2
    assert NCViewer._demo_step_size(5000, 0) == 63
    assert NCViewer._demo_step_size(50, 100) == 0
    assert NCViewer._demo_step_size(50, 50) == 0


def test_set_current_line_animate_light_path(app, tmp_path):
    """动画轻量路径: 只更新标记不崩溃"""
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    app.set_current_line(2, animate=True)
    assert app.current_line == 2


def test_trace_survives_zoom(app, tmp_path):
    """轨迹模式下缩放: 不退出轨迹, 已画轨迹保持"""
    from types import SimpleNamespace
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    app._trace_begin()
    app.set_current_line(3, animate=True)
    n_before = len(app.canvas.find_withtag("path"))
    app.zoom_at(1.25, None)
    assert app._trace_active
    assert len(app.canvas.find_withtag("path")) == n_before
    app._rot_start(SimpleNamespace(x=100, y=100))
    app._rot_move(SimpleNamespace(x=110, y=100))
    assert app._trace_active


def test_trace_pan_syncs_stored_coords(app, tmp_path):
    """轨迹模式下平移: 存储坐标与画面位移同步, 后续追加不混坐标系"""
    from types import SimpleNamespace
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    app._trace_begin()
    app.set_current_line(2, animate=True)     # 首段(从原点)被跳过, 从第2段取坐标
    flat0 = app._trace_items[0][2]
    x0, y0 = flat0[0], flat0[1]
    app._pan_start(SimpleNamespace(x=0, y=0))
    app._pan_move(SimpleNamespace(x=7, y=3))
    assert app._trace_items[0][2][0] == x0 + 7
    assert app._trace_items[0][2][1] == y0 + 3
    # 平移后继续追加不崩溃
    app.set_current_line(3, animate=True)
    assert len(app.canvas.find_withtag("path")) >= 1


def test_trace_draws_progressively(app, tmp_path):
    """轨迹渐进: 画布从空白起按行画刀路, 后退整段重绘

    首段(从原点出发)被前导跳过, 轨迹从第 2 段开始画。
    """
    p = tmp_path / "t.nc"
    p.write_text(NC_SMALL, encoding="utf-8")
    app.open_file(str(p))
    app.quat = (1.0, 0.0, 0.0, 0.0)      # 共享 fixture: 复位视图 (前序可能改过)
    app._trace_begin()
    assert len(app.canvas.find_withtag("path")) == 0
    app.set_current_line(1, animate=True)     # 第1段(原点出发)被跳过 -> 0 段
    assert len(app.canvas.find_withtag("path")) == 0
    app.set_current_line(2, animate=True)     # G2 F2000 -> 1 段
    assert len(app.canvas.find_withtag("path")) == 1
    app.set_current_line(3, animate=True)     # G0 灰色 -> 2 段
    assert len(app.canvas.find_withtag("path")) == 2
    app.set_current_line(1, animate=True)     # 后退 -> 重绘回 0 段
    assert len(app.canvas.find_withtag("path")) == 0