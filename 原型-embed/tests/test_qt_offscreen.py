# -*- coding: utf-8 -*-
"""qt_offscreen 离屏渲染器测试

覆盖: 全量 VBO 构建 (不抽稀)、移动级段数映射 (seg_map 逐移动精确,
回归"一段颜色执行完才显示"bug)、G0 过滤、渲染帧尺寸与格式、
FBO 随画布尺寸重建、空程序容错。
"""
import pytest

from nc_viewer.parser import parse_nc
from nc_viewer.geometry import build_palette
from nc_viewer.qt_offscreen import OffscreenRenderer

# 同色连续移动 (合并成一条折线) + 颜色切换 + G0:
# N1  G0 灰 → 1 段 (起点原点)
# N2-N4 F1000 同色合并 → 逐移动各 +1 段
# N5-N6 F2000 另一色 → 逐移动各 +1 段
NC_MERGE = """N1 G0 X0 Y0 Z50
N2 G1 X10 Y0 F1000
N3 G1 X20 Y0 F1000
N4 G1 X30 Y0 F1000
N5 G1 X40 Y0 F2000
N6 G1 X50 Y0 F2000
"""


@pytest.fixture(scope="module")
def renderer():
    try:
        from PyQt5.QtWidgets import QApplication
        qa = QApplication.instance() or QApplication([])
        # Qt 5.15 平台初始化时序: QApplication 创建后立即 create()
        # 离屏 surface 会访问违规崩溃, 需先经过事件循环一小段时间
        # (主程序 viewer 由 after(150) 天然规避)
        import time
        for _ in range(10):
            qa.processEvents()
            time.sleep(0.05)
        r = OffscreenRenderer()
    except Exception as e:          # 无 OpenGL 环境则跳过
        pytest.skip(f"无法创建离屏渲染器: {e}")
    yield r
    r.surface.destroy()


def _res():
    return parse_nc(NC_MERGE)


def test_vbo_full_no_decimation(renderer):
    """VBO 顶点数 = 全量折线段数 ×2, 不抽稀"""
    renderer.set_result(_res(), build_palette(_res().feeds))
    assert renderer._nverts == 6 * 2     # 6 移动 × 1 段 × 2 顶点


def test_seg_map_move_level_exact(renderer):
    """seg_map 逐移动精确: 合并进同色折线的移动段数实时累计

    回归: 旧实现只在颜色段切换 (折线 flush) 时计段, 播放到颜色段
    内部时 seg_map 停留在旧值 → 刀路整段不显示, 颜色段结束才一起
    出现 ("一段颜色执行完才显示")。
    """
    renderer.set_result(_res(), build_palette(_res().feeds))
    sm = renderer._move_seg_map
    # 每个移动完成后的累计段数应等于其序号+1 (每移动 1 段, 单调精确)
    assert [sm[i] for i in range(6)] == [1, 2, 3, 4, 5, 6]


def test_seg_map_monotonic_and_end(renderer):
    """seg_map 单调不减, 末尾 = VBO 总段数"""
    renderer.set_result(_res(), build_palette(_res().feeds))
    sm = renderer._move_seg_map
    prev = 0
    for i in sorted(sm):
        assert sm[i] >= prev
        prev = sm[i]
    assert prev == renderer._nverts // 2


def test_show_g0_false_excludes_g0(renderer):
    """show_g0=False 时 G0 移动不进 seg_map, 段数减少"""
    res = _res()
    renderer.set_result(res, build_palette(res.feeds), show_g0=False)
    assert renderer._nverts == 5 * 2            # 仅 N2-N6 5 段
    sm = renderer._move_seg_map
    assert 0 not in sm                          # N1 (G0) 被过滤
    assert [sm[i] for i in (1, 2, 3, 4, 5)] == [1, 2, 3, 4, 5]


def test_seg_filter_respects_index(renderer):
    """段过滤: seg_map 只含过滤范围内的移动, 索引为原始移动索引"""
    res = _res()
    renderer.set_result(res, build_palette(res.feeds),
                        seg_filter=[(2, 3)])    # 仅 N3-N4
    assert renderer._nverts == 2 * 2
    assert sorted(renderer._move_seg_map) == [2, 3]


def test_empty_program_ok(renderer):
    """空程序 (无移动) 不崩溃"""
    renderer.set_result(parse_nc("N1 G90"), build_palette([]))
    assert renderer._nverts == 0
    assert renderer._move_seg_map == {}


def test_render_frame_size_and_format(renderer):
    """渲染一帧: 返回 QImage, 尺寸正确"""
    res = _res()
    renderer.set_result(res, build_palette(res.feeds))
    img = renderer.render((1.0, 0.0, 0.0, 0.0), 1.0, (0.0, 0.0), 320, 200)
    assert img.width() == 320
    assert img.height() == 200
    assert not img.isNull()


def test_render_fbo_rebuild_on_resize(renderer):
    """FBO 随画布尺寸变化重建, 不崩溃"""
    res = _res()
    renderer.set_result(res, build_palette(res.feeds))
    img1 = renderer.render((1.0, 0.0, 0.0, 0.0), 1.0, (0.0, 0.0), 100, 80)
    img2 = renderer.render((1.0, 0.0, 0.0, 0.0), 1.0, (0.0, 0.0), 240, 160)
    assert img1.width() == 100
    assert img2.width() == 240
    # 尺寸不变时复用 FBO (对象同一), 变后重建 (对象不同)
    img3 = renderer.render((1.0, 0.0, 0.0, 0.0), 1.0, (0.0, 0.0), 240, 160)
    assert renderer._fbo.width() == 240
    assert img3.width() == 240


def test_render_trace_cutoff_not_crash(renderer):
    """trace_drawn 裁剪渲染不崩溃 (移动级渐进播放路径)"""
    res = _res()
    renderer.set_result(res, build_palette(res.feeds))
    for n in (0, 1, 3, 6):
        img = renderer.render((1.0, 0.0, 0.0, 0.0), 1.0, (0.0, 0.0), 160, 120,
                              trace_drawn=n)
        assert not img.isNull()
