# -*- coding: utf-8 -*-
"""nc_viewer.geometry 旋转数学单元测试 —— 验证轨道旋转方向与旋转中心不动"""
import math

import pytest

from nc_viewer.geometry import (arc_points, compensate_center, orbit_rotate,
                                project, quat_from_axis_angle, quat_mul,
                                quat_normalize)


def test_orbit_rotate_drag_right_moves_pivot_right():
    """鼠标右拖 => 前中心点(0,0,1)应向右移动(屏幕 x 增大)"""
    q = (1.0, 0.0, 0.0, 0.0)
    new_q = orbit_rotate(q, 10, 0, sens=0.01)
    x, y = project((0.0, 0.0, 1.0), new_q)
    assert x > 0.0
    assert y == pytest.approx(0.0, abs=1e-9)


def test_orbit_rotate_drag_down_moves_pivot_down():
    """鼠标下拖 => 前中心点(0,0,1)应向下移动(世界 y 减小 = 屏幕向下)"""
    q = (1.0, 0.0, 0.0, 0.0)
    new_q = orbit_rotate(q, 0, 10, sens=0.01)
    x, y = project((0.0, 0.0, 1.0), new_q)
    assert y < 0.0
    assert x == pytest.approx(0.0, abs=1e-9)


def test_orbit_rotate_identity_when_no_drag():
    q = (1.0, 0.0, 0.0, 0.0)
    assert orbit_rotate(q, 0, 0, sens=0.01) == q


def test_orbit_rotate_preserves_unit_quaternion():
    q = quat_normalize((0.6, 0.2, -0.3, 0.7))
    new_q = orbit_rotate(q, -7, 13, sens=0.01)
    assert math.hypot(*new_q) == pytest.approx(1.0, abs=1e-9)


def test_compensate_center_keeps_pivot_screen_fixed():
    """旋转前后, 补偿偏移应使旋转中心在屏幕上的投影保持不变"""
    pivot = (12.0, -4.0, 8.0)
    q = quat_normalize((0.6, 0.2, -0.3, 0.7))
    new_q = orbit_rotate(q, 20, -15, sens=0.01)
    scale = 2.5
    ox, oy = 100.0, -50.0
    # 旋转前 pivot 的屏幕位置
    a0, b0 = project(pivot, q)
    sx0, sy0 = a0 * scale + ox, -b0 * scale + oy
    # 旋转后, 用补偿公式得到新偏移
    n_ox, n_oy = compensate_center(pivot, q, new_q, scale, (ox, oy))
    a1, b1 = project(pivot, new_q)
    sx1, sy1 = a1 * scale + n_ox, -b1 * scale + n_oy
    assert sx1 == pytest.approx(sx0, abs=1e-9)
    assert sy1 == pytest.approx(sy0, abs=1e-9)


def test_small_arc_discretized_into_curve():
    """小半径圆弧(R=1, 90°)不应退化成直线, 需离散出中间点表现曲率。
    旧逻辑按 max_seg=4 控制段数, 弧长<4mm 时 n=1 退化为两点直线。
    """
    pts = arc_points((0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (1.0, 0.0, 0.0),
                     False, "XY", max_seg=4)
    assert len(pts) >= 3   # 至少 start + 中间若干 + 终点前


def test_small_arc_points_lie_on_circle():
    """小圆弧的中间采样点都应落在以圆心为心、半径为 1 的圆上"""
    pts = arc_points((0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (1.0, 0.0, 0.0),
                     False, "XY", max_seg=4)
    for p in pts:
        d = math.hypot(p[0] - 1.0, p[1] - 0.0)
        assert d == pytest.approx(1.0, abs=1e-6)