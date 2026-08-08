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
from nc_viewer.tool import Tool
from nc_viewer.viewer import NCViewer, _sample_dir
from nc_viewer.geometry import project

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
    """F 曲线绘制函数可按任意尺寸运行"""
    cv = tk.Canvas(app, width=600, height=400)
    app._draw_f_curve(cv, [(1, 1000.0), (2, 2000.0), (3, 1000.0)], 600, 400)
    assert len(cv.find_all()) > 0
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


def test_sidebar_bottom_three_pinned(app):
    """程序统计/当前位置/刀具三区在侧栏内完整堆叠 (无滚动容器)"""
    side = app.stats_labels["x"].master.master
    assert side is app.pos_fields["X"].master.master
    assert side is app.tool_cv.master.master
    assert not hasattr(app, "side_canvas")     # 滚动容器已移除


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
    # 复位到段首
    app._reset_line()
    assert app.current_line == 2
    # 关闭段模式恢复全局
    app._seg_only.set(False)
    app._toggle_seg_only()
    assert app._seg_filter is None
    app._draw_all()
    assert app.current_line == 7


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