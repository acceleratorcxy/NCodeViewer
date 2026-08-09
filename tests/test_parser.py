# -*- coding: utf-8 -*-
"""nc_viewer.parser 单元测试 —— TDD RED 阶段先写测试"""
import math

import pytest

from nc_viewer.parser import (Move, compute_lift_plane, compute_segments,
                              compute_stats, parse_nc)


# ---------- 基础线性移动 ----------
def test_single_linear_move_records_start_end_feed():
    text = "G01X10Y20F1000"
    r = parse_nc(text)
    assert len(r.moves) == 1
    m = r.moves[0]
    assert m.motion == "G1"
    assert m.start == (0.0, 0.0, 0.0)
    assert m.end == (10.0, 20.0, 0.0)
    assert m.feed == 1000.0


def test_g0_rapid_has_no_feed():
    text = "G0X10Y20"
    r = parse_nc(text)
    assert len(r.moves) == 1
    assert r.moves[0].motion == "G0"
    assert r.moves[0].feed is None


def test_g_code_is_modal():
    text = "G01X10Y20F1000\nX30Y40\nX50"
    r = parse_nc(text)
    assert [m.motion for m in r.moves] == ["G1", "G1", "G1"]
    assert r.moves[1].end == (30.0, 40.0, 0.0)
    assert r.moves[2].end == (50.0, 40.0, 0.0)  # Y 未给则保留


def test_feed_is_modal():
    text = "G01X10F500\nX20\nX30F800\nX40"
    r = parse_nc(text)
    assert r.moves[0].feed == 500.0
    assert r.moves[1].feed == 500.0   # 模态保留
    assert r.moves[2].feed == 800.0
    assert r.moves[3].feed == 800.0


def test_z_is_also_modal_and_tracked():
    text = "G01X10Z-5F300\nX20"
    r = parse_nc(text)
    assert r.moves[0].end == (10.0, 0.0, -5.0)
    assert r.moves[1].end == (20.0, 0.0, -5.0)


# ---------- 圆弧 ----------
def test_g2_cw_arc_records_center_and_direction():
    # 从 (0,0) 顺时针到 (10,0), 圆心 (5,0)
    text = "G02X10Y0I5J0F200"
    r = parse_nc(text)
    assert len(r.moves) == 1
    m = r.moves[0]
    assert m.motion == "G2"
    assert m.cw is True
    assert m.center == (5.0, 0.0, 0.0)   # 绝对圆心 = start + (I,J)
    assert m.end == (10.0, 0.0, 0.0)
    assert m.feed == 200.0


def test_g3_ccw_arc_direction():
    text = "G03X10Y0I5J0F200"
    r = parse_nc(text)
    assert r.moves[0].motion == "G3"
    assert r.moves[0].cw is False


def test_arc_default_plane_is_xy():
    text = "G03X10Y0I5J0"
    r = parse_nc(text)
    assert r.moves[0].plane == "XY"


# ---------- 行号 / N 字 ----------
def test_n_number_is_captured_when_present():
    text = "N6G01X10Y20F3000"
    r = parse_nc(text)
    assert r.moves[0].n_number == 6.0


def test_n_number_none_when_absent():
    text = "G01X10F300"
    r = parse_nc(text)
    assert r.moves[0].n_number is None


def test_move_line_number_is_file_line_1_based():
    text = "%;\nN2M08;\nN4G01X10F300"
    r = parse_nc(text)
    # 第3行才是移动
    assert r.moves[0].line_number == 3


# ---------- 非移动行 / 位置跟踪 ----------
def test_line_positions_returns_position_after_each_line():
    # 第1行无移动 -> 保持原点
    # 第2行移动到 (10,0,0)
    # 第3行 M代码无移动 -> 仍为 (10,0,0)
    text = "M08\nG01X10F300\nM03"
    r = parse_nc(text)
    assert r.line_positions[1] == (0.0, 0.0, 0.0)
    assert r.line_positions[2] == (10.0, 0.0, 0.0)
    assert r.line_positions[3] == (10.0, 0.0, 0.0)


def test_position_at_line_returns_end_of_that_line():
    text = "G01X10F300\nX20\nX30"
    r = parse_nc(text)
    assert r.position_at_line(1) == (10.0, 0.0, 0.0)
    assert r.position_at_line(2) == (20.0, 0.0, 0.0)
    assert r.position_at_line(3) == (30.0, 0.0, 0.0)


def test_position_at_line_clamps_to_range():
    text = "G01X10F300"
    r = parse_nc(text)
    # 超出行号范围: 取首/末位置
    assert r.position_at_line(0) == (0.0, 0.0, 0.0)
    assert r.position_at_line(99) == (10.0, 0.0, 0.0)


def test_find_line_by_n_number():
    text = "%;\nN2M08;\nN6G01X10F300"
    r = parse_nc(text)
    # N6 在第3行
    assert r.line_for_n(6) == 3
    assert r.line_for_n(999) is None


def test_duplicate_n_number_keeps_first_occurrence():
    # 长程序中 N 编号会重启, 同一 N 值多次出现时取首次
    text = "N6G01X10F300\nN6G01X20F300"
    r = parse_nc(text)
    assert r.line_for_n(6) == 1


# ---------- F 值收集(用于配色) ----------
def test_feeds_collected_in_first_appearance_order_unique():
    text = "G01X10F3000\nX20F1800\nX30F3000\nX40F600\nX50"
    r = parse_nc(text)
    assert r.feeds == [3000.0, 1800.0, 600.0]


def test_feeds_excludes_g0_rapid():
    text = "G0X10\nG01X20F1000\nG0X30"
    r = parse_nc(text)
    assert r.feeds == [1000.0]


# ---------- 包围盒 ----------
def test_bbox_covers_all_endpoints():
    text = "G01X10Y-5Z-2F300\nX-3Y20Z1"
    r = parse_nc(text)
    minx, miny, minz, maxx, maxy, maxz = r.bbox
    assert (minx, maxx) == (-3.0, 10.0)
    assert (miny, maxy) == (-5.0, 20.0)
    assert (minz, maxz) == (-2.0, 1.0)


def test_bbox_includes_origin_when_start_at_zero():
    text = "G01X10F300"
    r = parse_nc(text)
    minx, miny, minz, maxx, maxy, maxz = r.bbox
    assert minx == 0.0 and miny == 0.0 and minz == 0.0


# ---------- 注释 / 容错 ----------
def test_semicolon_comment_is_ignored():
    text = "G01X10F300 ; 这是注释\nX20"
    r = parse_nc(text)
    assert r.moves[0].end == (10.0, 0.0, 0.0)
    assert r.moves[1].end == (20.0, 0.0, 0.0)


def test_paren_comment_is_ignored():
    text = "(头部注释)\nG01X10F300 (行内注释)"
    r = parse_nc(text)
    assert len(r.moves) == 1
    assert r.moves[0].end == (10.0, 0.0, 0.0)


def test_blank_lines_and_pure_m_codes_do_not_move():
    text = "\nM08\n\nM03\n"
    r = parse_nc(text)
    assert r.moves == []
    # 所有行位置都应为原点
    for i in range(1, 5):
        assert r.position_at_line(i) == (0.0, 0.0, 0.0)


# ---------- 增量编程 ----------
def test_g91_incremental_mode():
    text = "G91G01X10Y5F300\nX10"
    r = parse_nc(text)
    assert r.moves[0].end == (10.0, 5.0, 0.0)
    assert r.moves[1].end == (20.0, 5.0, 0.0)


def test_g90_restores_absolute():
    text = "G91G01X10F300\nG90X50"
    r = parse_nc(text)
    assert r.moves[0].end == (10.0, 0.0, 0.0)
    assert r.moves[1].end == (50.0, 0.0, 0.0)


# ---------- 平面选择 ----------
def test_g18_selects_xz_plane():
    text = "G18G02X10Z0I5K0F200"
    r = parse_nc(text)
    assert r.moves[0].plane == "XZ"


def test_g19_selects_yz_plane():
    text = "G19G03Y10Z0J5K0F200"
    r = parse_nc(text)
    assert r.moves[0].plane == "YZ"


# ---------- 真实样例片段 ----------
def test_real_sample_first_moves_parse_correctly():
    # 取自样例 D0354F31311-201_AG6D311A0101.MPF 前几行
    text = (
        "%;\n"
        "N2M08;\n"
        "N4T1M06;\n"
        "N6G01X-334.446Y167.432Z100.F3000.S5000M03;\n"
        "N8Z10.1F6000.;\n"
        "N10Z0.1F300.;\n"
    )
    r = parse_nc(text)
    assert len(r.moves) == 3
    assert r.moves[0].motion == "G1"
    assert r.moves[0].n_number == 6.0
    assert r.moves[0].end == (-334.446, 167.432, 100.0)
    assert r.moves[0].feed == 3000.0
    assert r.moves[1].feed == 6000.0
    assert r.moves[1].end == (-334.446, 167.432, 10.1)
    assert r.moves[2].feed == 300.0
    assert r.feeds == [3000.0, 6000.0, 300.0]
    # 第4行(N6)位置
    assert r.position_at_line(4) == (-334.446, 167.432, 100.0)


# ---------- S 主轴转速 ----------
def test_s_spindle_is_modal():
    text = "G01X10F300S5000\nX20\nX30S8000\nX40"
    r = parse_nc(text)
    assert r.moves[0].s == 5000.0
    assert r.moves[1].s == 5000.0   # 模态保留
    assert r.moves[2].s == 8000.0
    assert r.moves[3].s == 8000.0


def test_s_none_when_absent_and_for_g0():
    text = "G01X10F300\nG0X20"
    r = parse_nc(text)
    assert r.moves[0].s is None
    assert r.moves[1].s is None    # G0 快移不记录转速


# ---------- 程序统计 ----------
def test_compute_stats_ranges_and_counts():
    # X 范围 -3..10, Y 范围 -5..20, Z 范围 -2..1 (端点, 不含原点)
    # S: 5000..8000; F: 1000/2000 两档; G0:1 G1:1 G2:1
    text = ("G01X10Y-5Z-2F1000S5000\n"
            "G02X-3Y20Z1I0J5F2000S8000\n"
            "G0X0Y0Z0\n")
    r = parse_nc(text)
    st = compute_stats(r)
    assert (st.x_min, st.x_max) == (-3.0, 10.0)
    assert (st.y_min, st.y_max) == (-5.0, 20.0)
    assert (st.z_min, st.z_max) == (-2.0, 1.0)
    assert (st.s_min, st.s_max) == (5000.0, 8000.0)
    assert (st.f_min, st.f_max) == (1000.0, 2000.0)
    assert st.f_count == 2
    assert st.g_counts == {"G1": 1, "G2": 1, "G0": 1}
    assert st.moves_total == 3
    assert st.cut_total == 2
    assert st.f_seg_counts == {1000.0: 1, 2000.0: 1}


def test_compute_stats_empty_program():
    r = parse_nc("\nM08\n")
    st = compute_stats(r)
    assert (st.x_min, st.x_max) == (0.0, 0.0)
    assert st.s_min is None and st.s_max is None
    assert st.f_min is None and st.f_max is None
    assert st.f_count == 0
    assert st.g_counts == {}
    assert st.moves_total == 0 and st.cut_total == 0


def test_compute_stats_f_excludes_g0_rapid():
    text = "G0X10\nG01X20F1000\nG0X30"
    r = parse_nc(text)
    st = compute_stats(r)
    assert (st.f_min, st.f_max) == (1000.0, 1000.0)
    assert st.cut_total == 1
    assert st.g_counts == {"G0": 2, "G1": 1}

# ---------- 分段 (抬刀平面) ----------
def test_lift_plane_is_most_repeated_high_z():
    """抬刀平面 = 出现次数>=2 的最高 Z 档位"""
    text = ("G01X0Y0Z100F1000\n"      # 抬刀平面 Z100 (多次)
            "G01X10Z50F1000\n"
            "G01X20Z-2F1000\n"
            "G01X30Z50F1000\n"
            "G01X40Z100F1000\n"
            "G01X50Z-2F1000\n"
            "G01X60Z100F1000\n")
    r = parse_nc(text)
    assert compute_lift_plane(r.moves) == 100.0


def test_lift_plane_excludes_one_off_high_z():
    """偶发高 Z (仅一次) 不作为抬刀平面"""
    text = ("G01X0Y0Z100F1000\n"
            "G01X10Z100F1000\n"
            "G01X20Z200F1000\n"       # 偶发 Z200
            "G01X30Z100F1000\n")
    r = parse_nc(text)
    assert compute_lift_plane(r.moves) == 100.0


def test_lift_plane_falls_back_to_max_when_all_unique():
    """全部 Z 仅出现一次时回退最大值"""
    r = parse_nc("G01X0Z10F1000\nG01X10Z20F1000\nG01X20Z30F1000\n")
    assert compute_lift_plane(r.moves) == 30.0


def test_segments_plunge_and_retract_cycles():
    """段 = 抬刀平面上的定位移动(下落前) -> 下降 -> 加工 -> 回升到抬刀平面"""
    text = ("G01X0Y0Z100F1000\n"      # 抬刀平面定位 (下落前一行)
            "G01X10Z50F1000\n"        # 下降
            "G01X20Z-2F1000\n"
            "G01X30Z-2F1000\n"
            "G01X40Z100F1000\n"       # 回升 -> 段 1 结束
            "G01X50Z50F1000\n"        # 段 2 开始 (上段结束后的移动归入下段)
            "G01X60Z-5F1000\n"
            "G01X70Z100F1000\n")      # 段 2 结束
    r = parse_nc(text)
    segs = compute_segments(r.moves)
    assert len(segs) == 2
    s1, s2 = segs
    assert (s1.start_idx, s1.end_idx) == (0, 4)
    assert s1.z_min == -2.0
    assert (s2.start_idx, s2.end_idx) == (5, 7)
    assert s2.z_min == -5.0
    assert s1.start_line == 1 and s1.end_line == 5


def test_segments_lift_override():
    """用户覆盖抬刀平面后分段重算"""
    text = ("G01X0Y0Z100F1000\n"
            "G01X10Z50F1000\n"
            "G01X20Z-2F1000\n"
            "G01X30Z50F1000\n"
            "G01X40Z100F1000\n")
    r = parse_nc(text)
    segs = compute_segments(r.moves, lift=50.0)
    # 抬刀 50: 前两行(100/50)在平面上(下落前定位), 第3行(-2)下降, 第4行回 50 -> 1 段
    assert len(segs) == 1
    assert (segs[0].start_idx, segs[0].end_idx) == (0, 3)


def test_segments_all_below_lift_is_one_segment():
    # 首行 Z10 恰在抬刀平面(唯一 Z, 回退最大值)上, 段从该行(下落前)开始
    r = parse_nc("G01X0Y0Z10F1000\nG01X10Z-2F1000\n")
    segs = compute_segments(r.moves)
    assert len(segs) == 1
    assert (segs[0].start_idx, segs[0].end_idx) == (0, 1)


def test_segments_empty_program():
    r = parse_nc("\nM08\n")
    assert compute_segments(r.moves) == []
