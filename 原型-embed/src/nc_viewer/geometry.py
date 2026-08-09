# -*- coding: utf-8 -*-
"""视图几何与渲染数学

包含: 颜色映射、圆弧离散、四元数 ArcBall 轨道旋转、旋转中心补偿、正交投影。
这些函数均为纯计算, 不依赖 Tkinter, 便于独立单元测试。
"""
from __future__ import annotations

import colorsys
import math

# ---------- 颜色 ----------
G0_COLOR = "#9a9a9a"      # 快移灰
BG_COLOR = "#2b2b2b"      # 画布背景
CUR_COLOR = "#ffe600"     # 当前位置标记
CUR_LINE_COLOR = "#786e12"  # 当前位置十字虚线 (暗化黄, 不刺眼)
SEG_COLOR = "#ff3b3b"     # 当前段高亮


def build_palette(feeds):
    """为每个 F 值生成一个区分度较好的颜色, 返回 {feed: '#rrggbb'}"""
    palette = {}
    n = len(feeds)
    for i, f in enumerate(feeds):
        h = (i / n) if n > 1 else 0.55
        # 蛇形铺开色相, 提升相邻 F 的区分度
        h = (h * 0.85 + 0.05) % 1.0
        r, g, b = colorsys.hsv_to_rgb(h, 0.85, 1.0)
        palette[f] = "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))
    return palette


def color_of_move(move, palette):
    if move.motion == "G0":
        return G0_COLOR
    return palette.get(move.feed, "#ffffff")


# ---------- 圆弧离散 ----------
def arc_points(start, end, center, cw, plane, max_seg=2.0, max_steps=240):
    """返回圆弧离散 3D 点列表(含起点, 不含终点)"""
    if plane == "XY":
        a0, b0, c0 = start[0], start[1], start[2]
        a1, b1, c1 = end[0], end[1], end[2]
        ca, cb = center[0], center[1]
    elif plane == "XZ":
        a0, b0, c0 = start[0], start[2], start[1]
        a1, b1, c1 = end[0], end[2], end[1]
        ca, cb = center[0], center[2]
    else:  # YZ
        a0, b0, c0 = start[1], start[2], start[0]
        a1, b1, c1 = end[1], end[2], end[0]
        ca, cb = center[1], center[2]

    r = math.hypot(a0 - ca, b0 - cb)
    if r < 1e-9:
        return []
    start_ang = math.atan2(b0 - cb, a0 - ca)
    end_ang = math.atan2(b1 - cb, a1 - ca)
    sweep = end_ang - start_ang
    if cw:        # G2 顺时针, sweep 取负
        while sweep >= 0:
            sweep -= 2 * math.pi
    else:         # G3 逆时针, sweep 取正
        while sweep <= 0:
            sweep += 2 * math.pi

    arc_len = abs(sweep) * r
    max_angle = 0.1                    # 每段最大圆心角(弧度≈5.7°), 保证小圆弧也显曲率
    n_angle = int(abs(sweep) / max_angle) + 1
    n_len = int(arc_len / max_seg) + 1
    n = max(1, min(max_steps, max(n_angle, n_len)))
    pts = []
    for i in range(n):  # 0..n-1, 不含终点
        t = i / n
        ang = start_ang + sweep * t
        a = ca + r * math.cos(ang)
        b = cb + r * math.sin(ang)
        c = c0 + (c1 - c0) * t
        if plane == "XY":
            pts.append((a, b, c))
        elif plane == "XZ":
            pts.append((a, c, b))
        else:
            pts.append((c, a, b))
    return pts


def move_points_3d(move, max_seg=2.0):
    """单条移动的 3D 离散点序列(含起点与终点)"""
    if move.motion in ("G2", "G3") and move.center is not None:
        pts = arc_points(move.start, move.end, move.center, move.cw, move.plane, max_seg)
        if not pts or pts[0] != move.start:
            pts.insert(0, move.start)
        pts.append(move.end)
        return pts
    return [move.start, move.end]


def point_seg_dist_sq(px, py, x1, y1, x2, y2):
    """点 (px,py) 到线段 (x1,y1)-(x2,y2) 距离的平方 (垂足钳制在线段内)"""
    dx = x2 - x1
    dy = y2 - y1
    l2 = dx * dx + dy * dy
    if l2 < 1e-12:
        return (px - x1) ** 2 + (py - y1) ** 2
    t = ((px - x1) * dx + (py - y1) * dy) / l2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    cx = x1 + t * dx
    cy = y1 + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2


# ---------- 视图投影 (四元数 ArcBall) ----------
def quat_rotate(p, q):
    """用单位四元数 q=(w,x,y,z) 旋转 3D 点 p"""
    w, x, y, z = q
    vx, vy, vz = p
    tx = 2 * (y * vz - z * vy)
    ty = 2 * (z * vx - x * vz)
    tz = 2 * (x * vy - y * vx)
    wx = vx + w * tx + (y * tz - z * ty)
    wy = vy + w * ty + (z * tx - x * tz)
    wz = vz + w * tz + (x * ty - y * tx)
    return wx, wy, wz


def quat_mul(a, b):
    """四元数乘法 a * b"""
    w1, x1, y1, z1 = a
    w2, x2, y2, z2 = b
    return (w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2)


def quat_from_axis_angle(ax, ay, az, theta):
    s = math.sin(theta / 2)
    return math.cos(theta / 2), ax * s, ay * s, az * s


def quat_normalize(q):
    w, x, y, z = q
    n = math.hypot(w, x, y, z)
    if n < 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    return w / n, x / n, y / n, z / n


def orbit_rotate(quat, dx, dy, sens=0.01):
    """屏幕轴轨道旋转(CATIA 风格): 返回旋转后的四元数。

    增量旋转在**视空间(屏幕空间)**进行, 因此用**前乘**叠加到当前四元数上:
      - 鼠标水平拖动 dx 绕屏幕垂直(上下)轴旋转
      - 鼠标垂直拖动 dy 绕屏幕水平(左右)轴旋转
    方向满足"抓住模型, 往哪边拖就往哪边转"的自然直觉。
    """
    if dx == 0 and dy == 0:
        return quat
    rx = quat_from_axis_angle(1.0, 0.0, 0.0, dy * sens)   # 绕屏幕水平轴
    ry = quat_from_axis_angle(0.0, 1.0, 0.0, dx * sens)    # 绕屏幕垂直轴
    dq = quat_mul(ry, rx)                                  # 视空间增量
    return quat_normalize(quat_mul(dq, quat))              # 前乘: 视空间旋转


def compensate_center(pivot, q_old, q_new, scale, offset):
    """围绕旋转中心做旋转时, 计算补偿后的画布偏移, 使 pivot 屏幕投影绝对不动。

    返回 (new_ox, new_oy)。旋转中心投影的屏幕坐标:
      sx = a*scale + ox,  sy = -b*scale + oy  (a,b = pivot 在当前四元数下的投影)
    """
    ox, oy = offset
    a_old, b_old = project(pivot, q_old)
    a_new, b_new = project(pivot, q_new)
    return ox + (a_old - a_new) * scale, oy + (b_new - b_old) * scale


def project(p, q):
    """3D -> 2D 正交投影 (x, y)"""
    x1, y2, _ = quat_rotate(p, q)
    return x1, y2


# 三个主视图预设四元数
VIEW_QUAT = {
    "XY": (1.0, 0.0, 0.0, 0.0),
    "XZ": quat_normalize((math.cos(-math.pi / 4), -math.sin(math.pi / 4), 0.0, 0.0)),
    "YZ": quat_normalize((math.cos(math.pi / 4), 0.0, math.sin(math.pi / 4), 0.0)),
}