# -*- coding: utf-8 -*-
"""nc_viewer.tool 单元测试: aptsource 解析 / 剖面轮廓 / 摘要"""
import math

import pytest

from nc_viewer.tool import Tool, parse_aptsource_tool, tool_profile_points, tool_summary

CUTTER_20_3 = (
    "CUTTER/ 20.000000,  3.000000,  7.000000,  3.000000,  0.000000,$\n"
    "         0.000000, 30.000000\n"
)


def test_parse_flat_with_corner_radius():
    """D20R3 (r<d/2) -> 平底立铣刀带圆角"""
    t = parse_aptsource_tool(CUTTER_20_3)
    assert t is not None and t.kind == "flat"
    assert t.p("d") == 20.0 and t.p("r") == 3.0


def test_parse_ball_nose():
    """r == d/2 -> 圆鼻立铣刀"""
    t = parse_aptsource_tool("CUTTER/ 10.000000,  5.000000,  0.000000,  5.000000,  0.000000,$\n"
                             "         0.000000, 35.000000\n")
    assert t.kind == "ball"
    assert t.p("r") == 5.0


def test_parse_drill_point_angle():
    """r=0 a=31 e=d/2 -> 普通钻头, 顶角 2a"""
    t = parse_aptsource_tool("CUTTER/  2.500000,  0.000000,  1.250000,  0.751076, 31.000000,$\n"
                             "         0.000000, 11.000000\n")
    assert t.kind == "drill"
    assert t.p("point") == pytest.approx(62.0)


def test_parse_inverse_taper():
    """a<0 -> 反锥立铣刀, 锥角 |a|"""
    t = parse_aptsource_tool("CUTTER/ 12.000000,  3.000000,  3.000000,  3.000000,  0.000000,$\n"
                             "        -2.000000, 25.000000\n")
    assert t.kind == "invtaper"
    assert t.p("taper") == pytest.approx(2.0)


def test_parse_no_cutter_returns_none():
    assert parse_aptsource_tool("GOTO/ 1, 2, 3\n") is None
    assert parse_aptsource_tool("") is None


def test_parse_first_cutter_wins():
    text = CUTTER_20_3 + ("CUTTER/ 6.000000,  3.000000,  0.000000,  3.000000,  0.000000,$\n"
                          "         0.000000, 10.000000\n")
    t = parse_aptsource_tool(text)
    assert t.p("d") == 20.0


def test_profile_ball_max_radius():
    t = Tool("ball", {"d": 10.0, "r": 5.0, "l": 30.0})
    pts = tool_profile_points(t)
    assert pts[0] == (0.0, 0.0)
    assert max(x for x, _ in pts) == pytest.approx(5.0)
    assert pts[-1] == (5.0, 30.0)


def test_profile_drill_point_length():
    t = Tool("drill", {"d": 10.0, "point": 118.0, "l": 50.0})
    pts = tool_profile_points(t)
    tip_len = 5.0 / math.tan(math.radians(59.0))
    assert pts[1][1] == pytest.approx(tip_len)
    assert pts[2] == (5.0, 50.0)


def test_profile_invtaper_top_narrower():
    t = Tool("invtaper", {"d": 12.0, "taper": 2.0, "l": 30.0})
    pts = tool_profile_points(t)
    assert pts[0] == (0.0, 0.0)
    assert pts[1] == (6.0, 0.0)          # 下底宽
    assert pts[2][0] < 6.0               # 上端窄 (反锥)


def test_profile_flat_with_corner():
    t = Tool("flat", {"d": 10.0, "r": 2.0, "l": 20.0})
    pts = tool_profile_points(t)
    assert len(pts) >= 4
    assert pts[-1] == (5.0, 20.0)


def test_summary_texts():
    assert tool_summary(Tool("ball", {"d": 10, "r": 5, "l": 30})) == "圆鼻立铣刀 D10 R5 L30"
    assert tool_summary(Tool("drill", {"d": 2.5, "point": 62, "l": 50})) == "普通钻头 D2.5 顶角62° L50"
    assert tool_summary(Tool("center", {"d": 3, "point": 60, "l": 15})) == "中心钻 D3 顶角60° L15"


def test_parse_toolno_lengths():
    """刃长取 TOOLNO 第 5 位 (70), 总长取 CUTTER 第 7 参数 (30)"""
    text = (
        "CUTTER/ 20.000000,  3.000000,  7.000000,  3.000000,  0.000000,$\n"
        "         0.000000, 30.000000\n"
        "TOOLNO/1,   20.000000,    3.000000,,  120.000000,$\n"
        "   70.000000,,   30.000000,4,    0.000000,NOTE\n"
    )
    t = parse_aptsource_tool(text)
    assert t is not None
    assert t.p("l") == pytest.approx(70.0)    # 刃长
    assert t.p("h") == pytest.approx(30.0)    # 总长 (CUTTER h, 非 TOOLNO 刀柄长 120)
    assert "L70" in tool_summary(t)


def test_parse_taper_toolno_with_angle_field():
    """反锥刀 TOOLNO 第 3 位为锥角、第 5 位为 0 时, 刃长回退 CUTTER e"""
    text = (
        "CUTTER/ 12.000000,  3.000000,  3.000000,  3.000000,  0.000000,$\n"
        "        -2.000000, 25.000000\n"
        "TOOLNO/4,   10.467000,    3.000000,   -4.000000,  120.000000,$\n"
        "    0.000000,,   25.000000,6,    0.000000,NOTE\n"
    )
    t = parse_aptsource_tool(text)
    assert t is not None and t.kind == "invtaper"
    assert t.p("l") == pytest.approx(3.0)     # TOOLNO 第5位为0 -> 回退 CUTTER e
    assert t.p("h") == pytest.approx(25.0)    # CUTTER 第7参数


def test_parse_fallback_lengths_without_toolno():
    """无 TOOLNO 时回退 CUTTER 的 e 作为刃长"""
    t = parse_aptsource_tool(CUTTER_20_3)
    assert t is not None
    assert t.p("l") == pytest.approx(7.0)
    assert t.p("h") == pytest.approx(30.0)
