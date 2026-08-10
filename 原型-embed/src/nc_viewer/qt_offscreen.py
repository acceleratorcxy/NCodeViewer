# -*- coding: utf-8 -*-
"""Qt 离屏渲染器: 无窗口渲染刀路到 FBO, 读回 QImage 供 Tk 显示

画布区域为 Tk canvas (纯 Tk 控件), 渲染由 Qt GPU 完成:
  1. 刀路 VBO (全量合并折线, 不抽稀) 静态上传
  2. 旋转/缩放/平移只更新 MVP
  3. 渲染到离屏 FBO -> toImage() 读回 (Qt C++ 层, 快)
  4. overlay (XYZ 轴/当前行/十字线/方向箭头/刀具) 用立即模式叠加
"""
from __future__ import annotations

import math

import numpy as np
from PyQt5.QtGui import (QSurfaceFormat, QOpenGLShaderProgram,
                         QOpenGLShader, QOpenGLBuffer, QMatrix4x4,
                         QOpenGLVersionProfile, QOpenGLFramebufferObject,
                         QOpenGLContext)
from PyQt5.QtGui import QOffscreenSurface

from .geometry import (move_points_3d, color_of_move, project, VIEW_QUAT,
                       BG_COLOR, CUR_COLOR, CUR_LINE_COLOR, SEG_COLOR,
                       G0_COLOR)
from .tool import tool_full_profile, tool_overall_height

VERT_SRC = """
attribute vec3 aPos;
attribute vec3 aColor;
uniform mat4 uMVP;
varying vec3 vColor;
void main() {
    gl_Position = uMVP * vec4(aPos, 1.0);
    vColor = aColor;
}
"""

FRAG_SRC = """
varying vec3 vColor;
void main() {
    gl_FragColor = vec4(vColor, 1.0);
}
"""


def _hex_rgb(s):
    return (int(s[1:3], 16) / 255.0, int(s[3:5], 16) / 255.0,
            int(s[5:7], 16) / 255.0)


class OffscreenRenderer:
    """离屏 OpenGL 渲染器 (QOffscreenSurface + FBO)"""

    def __init__(self):
        fmt = QSurfaceFormat()
        fmt.setSamples(4)
        fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
        fmt.setVersion(2, 0)
        self._fmt = fmt
        self.surface = QOffscreenSurface()
        self.surface.setFormat(fmt)
        self.surface.create()
        self.ctx = QOpenGLContext()
        self.ctx.setFormat(fmt)
        self.ctx.create()
        if not self.ctx.makeCurrent(self.surface):
            raise RuntimeError("离屏 OpenGL 上下文创建失败")
        prof = QOpenGLVersionProfile()
        prof.setVersion(2, 0)
        self.gl = self.ctx.versionFunctions(prof)
        self.gl.initializeOpenGLFunctions()
        self.gl.glClearColor(int(BG_COLOR[1:3], 16) / 255.0,
                             int(BG_COLOR[3:5], 16) / 255.0,
                             int(BG_COLOR[5:7], 16) / 255.0, 1.0)
        self._fbo = None
        self._program = None
        self._vbo = QOpenGLBuffer(QOpenGLBuffer.VertexBuffer)
        self._vbo_data = None
        self._nverts = 0
        self._move_seg_map = {}
        self._world = None
        self._tool = None
        self._show_tool = True

    # ---------- 数据 ----------
    def set_result(self, result, palette, seg_filter=None, show_g0=True,
                   lead_skip=0):
        """重建刀路 VBO (全量合并折线, 段过滤/G0 过滤)。

        lead_skip: 跳过前导起始进给移动 (从机器原点出发的对刀/接近段),
        与主程序显示口径一致 (否则画面从原点拉一条线到刀路起点)。
        """
        rows = []
        world = []
        check_seg = seg_filter is not None

        def flush_poly():
            """当前折线落库为行顶点对, 返回其段数"""
            if len(poly) < 2:
                return 0
            cr, cg, cb = _hex_rgb(poly_col)
            for a, b_ in zip(poly, poly[1:]):
                rows.append((a[0], a[1], a[2], b_[0], b_[1], b_[2],
                             cr, cg, cb))
                world.append(a)
            world.append(poly[-1])
            return len(poly) - 1

        flushed = 0          # 已 flush (颜色段切换时落库) 的折线段数
        seg_map = {}
        poly = []
        poly_col = None
        for i, m in enumerate(result.moves):
            if i < lead_skip:            # 前导起始进给不显示
                continue
            if m.motion == "G0" and not show_g0:
                continue
            if check_seg:
                if not any(lo <= i <= hi for lo, hi in seg_filter):
                    continue
            pts = move_points_3d(m)
            col = color_of_move(m, palette)
            if poly and poly_col == col and poly[-1] == pts[0]:
                poly.extend(pts[1:])
            else:
                flushed += flush_poly()
                poly = list(pts)
                poly_col = col
            # 移动 i 完成后的真实累计段数 = 已 flush + 当前折线段数。
            # 若按旧值 (只计 flush 边界) 记录, 合并进同色折线的移动在播放时
            # 整段不显示, 直到颜色段结束才整条一起出现 (一段颜色执行完才显示)
            seg_map[i] = flushed + len(poly) - 1
        flushed += flush_poly()
        self._move_seg_map = seg_map
        self._seg_map_max = max(seg_map) if seg_map else 0
        if not rows:
            self._vbo_data = None
            self._nverts = 0
            self._world = None
            return
        arr = np.asarray(rows, dtype=np.float32)
        inter = np.empty((flushed * 2, 6), dtype=np.float32)
        inter[0::2, :3] = arr[:, 0:3]
        inter[1::2, :3] = arr[:, 3:6]
        inter[0::2, 3:] = arr[:, 6:9]
        inter[1::2, 3:] = arr[:, 6:9]
        self._vbo_data = inter
        self._nverts = flushed * 2
        self._world = np.asarray(world, dtype=np.float32)
        self._upload_vbo()

    def set_tool(self, tool, show=True):
        self._tool = tool
        self._show_tool = show

    def _upload_vbo(self):
        if self._vbo_data is None:
            return
        if not self._vbo.isCreated():
            self._vbo.create()
        self._vbo.bind()
        self._vbo.allocate(self._vbo_data.tobytes(),
                           self._vbo_data.nbytes)

    # ---------- 渲染 ----------
    def render(self, quat, scale, offset, W, H, trace_drawn=None,
               current_line=None, result=None, highlight_move=None):
        """渲染一帧到 FBO 并读回 QImage

        highlight_move: 要高亮的当前移动 (viewer 已做前导/段过滤/G0
        检查, None 不高亮; 轨迹模式用 trace_drawn 最后移动定位刀尖)
        """
        if self._fbo is None or self._fbo.width() != W or self._fbo.height() != H:
            self._fbo = QOpenGLFramebufferObject(W, H)   # 旧 FBO 由 GC 回收
        self._fbo.bind()
        gl = self.gl
        gl.glViewport(0, 0, W, H)
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        if self._vbo_data is not None:
            if self._program is None:
                prog = QOpenGLShaderProgram()
                prog.addShaderFromSourceCode(QOpenGLShader.Vertex, VERT_SRC)
                prog.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAG_SRC)
                prog.link()
                self._program = prog
            prog = self._program
            prog.bind()
            self._vbo.bind()
            prog.enableAttributeArray("aPos")
            prog.setAttributeBuffer("aPos", gl.GL_FLOAT, 0, 3, 24)
            prog.enableAttributeArray("aColor")
            prog.setAttributeBuffer("aColor", gl.GL_FLOAT, 12, 3, 24)
            prog.setUniformValue("uMVP", self._mvp(quat, scale, offset, W, H))
            n_draw = self._nverts
            if trace_drawn is not None:
                segs = self._seg_of_move(trace_drawn)
                n_draw = min(segs * 2, self._nverts)
            gl.glDrawArrays(gl.GL_LINES, 0, n_draw)
            prog.release()
        # overlay: XYZ 轴/当前行/十字线/箭头/刀具 (立即模式)
        self._draw_overlay(gl, quat, scale, offset, W, H, trace_drawn,
                           current_line, result, highlight_move)
        img = self._fbo.toImage()
        # 轴文字标签: GL 立即模式无文字 API, 用 QPainter 叠加到 QImage
        # (纯 CPU 绘制, 不依赖 GL 上下文)
        labels = getattr(self, "_axis_labels", None)
        if labels:
            from PyQt5.QtGui import QPainter, QColor, QFont
            p = QPainter(img)
            f = QFont("Consolas", 11)
            f.setBold(True)
            p.setFont(f)
            for x, y, text, rgb, fs in labels:
                if fs != 11:
                    f = QFont("Consolas", fs)
                    f.setBold(True)
                    p.setFont(f)
                p.setPen(QColor(int(rgb[0] * 255), int(rgb[1] * 255),
                                int(rgb[2] * 255)))
                p.drawText(int(x), int(y), text)
            p.end()
        return img

    def _seg_of_move(self, trace_drawn):
        """移动 trace_drawn-1 完成后的累计段数; 该移动若被过滤
        (隐藏 G0/前导不在 seg_map), 回退到最近一个未过滤移动的段数 ——
        否则播放经过过滤移动时刀路瞬间清空, 与刀具/十字线不同步。"""
        if trace_drawn <= 0:
            return 0
        i = min(trace_drawn - 1, self._seg_map_max)
        while i >= 0 and i not in self._move_seg_map:
            i -= 1
        return self._move_seg_map.get(i, 0)

    def _mvp(self, quat, scale, offset, W, H):
        w, x, y, z = quat
        ox, oy = offset
        r00 = 1 - 2 * (y * y + z * z); r01 = 2 * (x * y - w * z); r02 = 2 * (x * z + w * y)
        r10 = 2 * (x * y + w * z); r11 = 1 - 2 * (x * x + z * z); r12 = 2 * (y * z - w * x)
        return QMatrix4x4(
            2 * scale / W * r00, 2 * scale / W * r01, 2 * scale / W * r02,
            2 * ox / W - 1,
            2 * scale / H * r10, 2 * scale / H * r11, 2 * scale / H * r12,
            1 - 2 * oy / H,
            0.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 1.0)

    def _ndc(self, mx, my, W, H):
        return mx / W * 2 - 1, 1 - my / H * 2

    def _draw_overlay(self, gl, quat, scale, offset, W, H, trace_drawn,
                      current_line, result, highlight_move):
        def ndc(mx, my):
            return mx / W * 2 - 1, 1 - my / H * 2
        # XYZ 方向指示器: 固定画布左下角 (随旋转变化, 始终可见)。
        # 轴画在世界原点投影会贴画布边缘/出画布, 用户看不到标识
        axis_labels = []       # [(像素x, 像素y, 文字, rgb, 字号)] 供 QPainter 叠加

        def _axis_arrow(ex, ey, cx, cy, s, rgb):
            """轴端实心箭头 (指向轴正方向) + 返回文字标签位置"""
            L = math.hypot(ex - cx, ey - cy)
            if L <= 10:
                return (ex, ey)
            ux, uy = (ex - cx) / L, (ey - cy) / L
            px, py = -uy, ux
            ax, ay = ndc(ex, ey)
            gl.glBegin(gl.GL_TRIANGLES)
            gl.glColor3f(*rgb)
            gl.glVertex2f(ax, ay)
            gl.glVertex2f(*ndc(ex - ux * s + px * s * 0.6,
                               ey - uy * s + py * s * 0.6))
            gl.glVertex2f(*ndc(ex - ux * s - px * s * 0.6,
                               ey - uy * s - py * s * 0.6))
            gl.glEnd()
            return (ex + ux * (s + 12), ey + uy * (s + 12))

        # 世界原点坐标系: 三轴随缩放/平移 (轴长 = 世界尺度), 比左下角
        # 指示器更大, 刀路定位点附近始终可见
        if result is not None:
            r_big = max(scale * 8, 60)
            for axis, rgb, vec in (("X", (1.0, 0.33, 0.33), (1, 0, 0)),
                                   ("Y", (0.33, 1.0, 0.33), (0, 1, 0)),
                                   ("Z", (0.33, 1.0, 1.0), (0, 0, 1))):
                a, b = project((vec[0] * r_big, vec[1] * r_big,
                                vec[2] * r_big), quat)
                ex, ey = a * scale + offset[0], -b * scale + offset[1]
                nx, ny = ndc(ex, ey)
                ox, oy = ndc(offset[0], offset[1])
                gl.glLineWidth(2)
                gl.glBegin(gl.GL_LINES)
                gl.glColor3f(*rgb)
                gl.glVertex2f(ox, oy)
                gl.glVertex2f(nx, ny)
                gl.glEnd()
                gl.glLineWidth(1)
                tx, ty = _axis_arrow(ex, ey, offset[0], offset[1], 10.0, rgb)
                axis_labels.append((tx, ty, axis, rgb, 12))
            # 世界原点白点
            ox, oy = ndc(offset[0], offset[1])
            gl.glPointSize(6)
            gl.glBegin(gl.GL_POINTS)
            gl.glColor3f(1.0, 1.0, 1.0)
            gl.glVertex2f(ox, oy)
            gl.glEnd()
            gl.glPointSize(1)

        if result is not None:
            cx, cy = 62, H - 56
            r = 34
            for axis, rgb, vec in (("X", (1.0, 0.33, 0.33), (1, 0, 0)),
                                   ("Y", (0.33, 1.0, 0.33), (0, 1, 0)),
                                   ("Z", (0.33, 1.0, 1.0), (0, 0, 1))):
                a, b = project((vec[0] * r, vec[1] * r, vec[2] * r), quat)
                ex, ey = cx + a, cy - b
                nx, ny = ndc(ex, ey)
                ox, oy = ndc(cx, cy)
                gl.glBegin(gl.GL_LINES)
                gl.glColor3f(*rgb)
                gl.glVertex2f(ox, oy)
                gl.glVertex2f(nx, ny)
                gl.glEnd()
                tx, ty = _axis_arrow(ex, ey, cx, cy, 8.0, rgb)
                axis_labels.append((tx, ty, axis, rgb, 11))
            # 指示器原点小白点
            ox, oy = ndc(cx, cy)
            gl.glPointSize(5)
            gl.glBegin(gl.GL_POINTS)
            gl.glColor3f(1.0, 1.0, 1.0)
            gl.glVertex2f(ox, oy)
            gl.glEnd()
            gl.glPointSize(1)
        self._axis_labels = axis_labels
        # 当前行高亮 + 方向箭头 (viewer 已检查前导跳过/段过滤/隐藏 G0,
        # 只传可高亮的移动 —— 否则会高亮一条不显示的刀路)
        if current_line is not None and result is not None:
            m = highlight_move
            if m is not None:
                pts = move_points_3d(m)
                screen = []
                for px, py, pz in pts:
                    a, b = project((px, py, pz), quat)
                    screen.append(ndc(a * scale + offset[0],
                                      -b * scale + offset[1]))
                gl.glLineWidth(3)
                gl.glBegin(gl.GL_LINE_STRIP)
                gl.glColor3f(*_hex_rgb(SEG_COLOR))
                for nx, ny in screen:
                    gl.glVertex2f(nx, ny)
                gl.glEnd()
                gl.glLineWidth(1)
                n = len(screen)
                if n >= 2:
                    t_vals = [0.6] if n == 2 else [0.25, 0.5, 0.75]
                    samples = []
                    for frac in t_vals:
                        seg = frac * (n - 1)
                        i = int(seg)
                        t = seg - i
                        j = min(i + 1, n - 1)
                        samples.append((screen[i][0] + (screen[j][0] - screen[i][0]) * t,
                                        screen[i][1] + (screen[j][1] - screen[i][1]) * t))
                    size_px = 28.0 / max(W, 1) * 2     # 镖形箭头 28px (旧三角 14px 偏小)
                    for k, (x, y) in enumerate(samples):
                        if k + 1 < len(samples):
                            nx, ny = samples[k + 1]
                        else:
                            nx, ny = screen[-1]
                        dx, dy = nx - x, ny - y
                        L = math.hypot(dx, dy)
                        if L < 1e-6:
                            continue
                        ux, uy = dx / L, dy / L
                        px, py = -uy, ux
                        size = min(size_px, L * 0.5)   # 避免过短段上箭头过大
                        # 镖形: 尖端 + 双翼 + 尾部内凹 (比纯三角饱满精致)
                        half = size * 0.62
                        tail = size * 1.0
                        notch = size * 0.32
                        gl.glBegin(gl.GL_TRIANGLE_FAN)
                        gl.glColor3f(*_hex_rgb(CUR_COLOR))
                        gl.glVertex2f(x, y)            # 尖端
                        gl.glVertex2f(x - ux * tail + px * half,
                                      y - uy * tail + py * half)
                        gl.glVertex2f(x - ux * (tail - notch),
                                      y - uy * (tail - notch))
                        gl.glVertex2f(x - ux * tail - px * half,
                                      y - uy * tail - py * half)
                        gl.glEnd()
            # 十字线 + 当前点: 轨迹模式跟随刀尖 (最后未过滤移动的终点,
            # 与刀具/已画刀路同步); 否则跟随当前行位置 (行号落在注释行
            # 时 position_at_line 返回旧位置, 十字线停在原点)
            if trace_drawn is not None and trace_drawn > 0 and result.moves:
                idx = min(trace_drawn - 1, self._seg_map_max)
                while idx >= 0 and idx not in self._move_seg_map:
                    idx -= 1
                if idx >= 0:
                    p3 = move_points_3d(result.moves[idx])[-1]
                else:
                    p3 = (0.0, 0.0, 0.0)
            else:
                p3 = result.position_at_line(current_line)
            w, x, y, z = quat
            px, py, pz = p3
            tx = 2 * (y * pz - z * py)
            ty = 2 * (z * px - x * pz)
            tz = 2 * (x * py - y * px)
            vx = px + w * tx + y * tz - z * ty
            vy = py + w * ty + z * tx - x * tz
            cx, cy = ndc(vx * scale + offset[0], -vy * scale + offset[1])
            gl.glBegin(gl.GL_LINES)
            gl.glColor3f(*_hex_rgb(CUR_LINE_COLOR))
            gl.glVertex2f(cx, -1.0)
            gl.glVertex2f(cx, 1.0)
            gl.glVertex2f(-1.0, cy)
            gl.glVertex2f(1.0, cy)
            gl.glEnd()
            gl.glPointSize(7)
            gl.glBegin(gl.GL_POINTS)
            gl.glColor3f(*_hex_rgb(CUR_COLOR))
            gl.glVertex2f(cx, cy)
            gl.glEnd()
            gl.glPointSize(1)
        # 3D 刀具模型 (轮廓线)
        if self._show_tool and self._tool is not None and result is not None:
            self._draw_tool_model(gl, quat, scale, offset, W, H,
                                  current_line, result, ndc,
                                  trace_drawn=trace_drawn)

    def _draw_tool_model(self, gl, quat, scale, offset, W, H, current_line,
                         result, ndc, trace_drawn=None):
        tool = self._tool
        # 轨迹模式 (播放/单步): 刀具钉在刀路最后未过滤移动的终点, 与
        # 已画刀路/十字线同步 (行号推进落在注释/空行时 position_at_line
        # 返回旧位置, 刀具会慢于刀路出现); 非轨迹模式跟随当前行
        if trace_drawn is not None and trace_drawn > 0 and result.moves:
            idx = min(trace_drawn - 1, self._seg_map_max)
            while idx >= 0 and idx not in self._move_seg_map:
                idx -= 1
            if idx >= 0:
                pos = move_points_3d(result.moves[idx])[-1]
            else:
                pos = (0.0, 0.0, 0.0)
        else:
            pos = (result.position_at_line(current_line)
                   if current_line else (0.0, 0.0, 0.0))
        full = tool_full_profile(tool)
        max_r = max(r for r, _ in full)
        px_w = 2 * max_r * scale
        factor = 1.0
        if 0 < px_w < 24:
            factor = min(24.0 / px_w, 6.0)
        h = tool_overall_height(tool)
        r_top = full[-1][0]
        top_circle = [(r_top * math.cos(t), r_top * math.sin(t), h)
                      for t in (i * math.tau / 24 for i in range(24))]
        # 剪影 (右缘 下->上 + 顶部直径 + 左缘 上->下, 闭合于刀尖)。
        # 与顶部圆环带分离: 环带斜视角投影自相交 (纯线框会透出背景刀路),
        # 剪影不自交, 三角扇填充完整
        sil = ([(r, 0.0, y) for r, y in full]
               + [(-r, 0.0, y) for r, y in reversed(full)])

        def verts(pts):
            out = []
            for x, y, z in pts:
                wx = pos[0] + x * factor
                wy = pos[1] + y * factor
                wz = pos[2] + z * factor
                a, b = project((wx, wy, wz), quat)
                out.append(ndc(a * scale + offset[0], -b * scale + offset[1]))
            return out

        sv = verts(sil)
        # 旋转体表面网格填充: 整个外表面 (圆柱面/球头/刀柄) 上色。
        # 旧实现填过轴心的剪影薄片 (y=0 平面), 斜视角投影变窄成
        # "中间一片", 外表面透明; 网格覆盖全部表面, 侧面看是完整实体。
        # 填充色要亮于背景 (#2b2b2b), 否则深灰实体在深灰背景上近乎不可见
        gl.glColor3f(0.56, 0.56, 0.60)     # #8f8f99 亮灰实体
        gl.glBegin(gl.GL_TRIANGLES)
        n_seg = 24
        for i in range(len(full) - 1):
            r0, y0 = full[i]
            r1, y1 = full[i + 1]
            for t in range(n_seg):
                a0 = t * math.tau / n_seg
                a1 = (t + 1) * math.tau / n_seg
                c0, s0 = math.cos(a0), math.sin(a0)
                c1, s1 = math.cos(a1), math.sin(a1)
                # 四边形 (r0,y0)-(r1,y1) 的 4 顶点 -> 2 三角形
                p00 = verts([(r0 * c0, r0 * s0, y0)])[0]
                p01 = verts([(r0 * c1, r0 * s1, y0)])[0]
                p11 = verts([(r1 * c1, r1 * s1, y1)])[0]
                p10 = verts([(r1 * c0, r1 * s0, y1)])[0]
                gl.glVertex2f(p00[0], p00[1])
                gl.glVertex2f(p01[0], p01[1])
                gl.glVertex2f(p11[0], p11[1])
                gl.glVertex2f(p00[0], p00[1])
                gl.glVertex2f(p11[0], p11[1])
                gl.glVertex2f(p10[0], p10[1])
        gl.glEnd()
        # 顶面圆盘填充 (扇心=顶部圆心): 比侧面更亮出立体顶面
        tv = verts([(0.0, 0.0, h)] + top_circle)
        gl.glBegin(gl.GL_TRIANGLE_FAN)
        gl.glColor3f(0.66, 0.66, 0.70)     # #a8a8b2 顶面
        for nx, ny in tv:
            gl.glVertex2f(nx, ny)
        gl.glEnd()
        # 剪影描边 (唯一的外轮廓线, 亮色勾勒)
        gl.glLineWidth(2)
        gl.glBegin(gl.GL_LINE_LOOP)
        gl.glColor3f(0.83, 0.83, 0.86)
        for nx, ny in sv:
            gl.glVertex2f(nx, ny)
        gl.glEnd()
        gl.glLineWidth(1)
        # 顶面圆线 (顶面轮廓)
        gl.glBegin(gl.GL_LINE_LOOP)
        gl.glColor3f(0.7, 0.7, 0.74)
        for nx, ny in tv[1:]:
            gl.glVertex2f(nx, ny)
        gl.glEnd()
        # 轴线 (深色细线) + 刀尖点 (保留, 供对刀定位)
        v = verts([(0.0, 0.0, 0.0), (0.0, 0.0, h)])
        gl.glBegin(gl.GL_LINES)
        gl.glColor3f(0.43, 0.43, 0.43)
        gl.glVertex2f(v[0][0], v[0][1])
        gl.glVertex2f(v[1][0], v[1][1])
        gl.glEnd()
        gl.glPointSize(6)
        gl.glBegin(gl.GL_POINTS)
        gl.glColor3f(*_hex_rgb(CUR_COLOR))
        gl.glVertex2f(v[0][0], v[0][1])
        gl.glEnd()
        gl.glPointSize(1)
