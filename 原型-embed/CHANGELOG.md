# 更改记录 (CHANGELOG)

本文件记录 NC 刀路查看器 Qt 离屏渲染实验版 (原型-embed) 的所有更改。
> **维护约定**：每次代码/文档/打包有改动，都应在「未发布」或当次版本条目下追加记录。
> - 记录格式：`- <类型> <一句话说明>`，类型取 `新增 / 修改 / 修复 / 移除 / 文档 / 打包 / 测试`。
> - 改动较大或对外发布时，新建版本号条目 `[x.y.z] - YYYY-MM-DD`。
> - 遵循 TDD：先写/跑测试（green），再打包。

### 修改
- 本实验版由主程序 `NCodeViewer` 1.1.0 分叉而来：**界面与交互完全保持主程序 Tk 实现**（三栏布局、播放控制、段过滤、搜索定位、统计侧栏、刀具面板等），仅把**画布刀路渲染**替换为 Qt 离屏 OpenGL 渲染（`qt_offscreen.py`）。
- **渲染架构**：`QOffscreenSurface + QOpenGLContext + QOpenGLFramebufferObject` 离屏渲染 → `QImage` 读回 → `PIL/ImageTk.PhotoImage` → Tk canvas 图片项显示；`_qt_ready` 就绪前（after 150ms）或 OpenGL 不可用时自动回退主程序 Tk 渲染。
- **全量刀路 VBO**：加载/段过滤/G0/刀具变化才重建（data 版本 key），旋转/缩放/平移只更新 MVP 重渲染；合并折线（同色连续、共享端点），**不抽稀、不断线**。
- **overlay 移至 Qt 立即模式**：XYZ 轴、当前行高亮/方向箭头、十字线、3D 刀具模型（刀尖对刀、直径投影放大判定）全部由 `qt_offscreen` 绘制，Tk canvas 不再有刀路/标记图元。
- **移动级轨迹渐进**：播放/单步按**移动**逐步增长刀路（"走到哪显示到哪"，`_move_seg_map` 逐移动精确映射段数，渲染按 `trace_drawn` 裁剪顶点）；合并跳行仍为行级推进语义，渲染回推到该行最后移动。
- **PhotoImage 复用**：播放每 tick 复用同一 PhotoImage（`paste` 更新像素），Tk 图片对象不累积（修复越用越卡）；画布尺寸变化时按新尺寸重建。
- 测试适配 Qt 离屏：主程序 163 例中涉及 Tk 画布图元/回退渲染机制的用例改为断言离屏渲染路径（`_trace_drawn` 状态、`_qt_render` 触发、`_renderer` 状态传递）；新增 `test_qt_offscreen.py` 8 例（全量 VBO 不抽稀、seg_map 逐移动精确、G0 过滤、段过滤、空程序、渲染帧、FBO 重建、trace 裁剪）。**py38 / py311 双环境全绿（170 例）**。
### 修复
- **seg_map 计数 bug（"一段颜色执行完才显示"）**：合并进同色折线的移动，段数只在颜色段切换（折线 flush）时计入 → 播放到颜色段内部时整段不显示，颜色段结束才一起出现；改为每移动完成后 = 已落库段数 + 当前折线段数，逐移动精确。
- **播放段尾不停止**：段模式播放钳制在段内最后移动后停止（`_play_tick` 按 `_seg_line_bounds` 计算终点）。
- **`_fill_code` 全删文件崩溃**：`self.result` 为 None 时访问 `.moves` 崩溃，补空结果保护。
- **`show_tool` 切换不生效**：render 数据版本 key 漏 `show_tool`，开关切换后 overlay 不更新；已加入 key。
- **Tk 回退渲染残留图元**：`_qt_ready` 就绪前早期渲染产生的 Tk 刀路/标记图元残留画布（与离屏图片混叠）；Qt 接管渲染时统一清理。
- **`_play_tick` 合并跳行语义**：初版把合并数当移动数推进（行内多移动时跨行）；改回行级推进 + 渲染按移动裁剪。

### 文档
- 按主程序规格建立完整项目结构并撰写本文档目录下全套文档（README、技术手册、开发流程、本文件）。

---

## [0.1.0] - 2026-08-09

本实验版定位：验证「Qt OpenGL 渲染嵌入 Tk 界面」方案，目标在完整刀路下实现 60fps 交互，且界面完全保持主程序 Tk 实现。
### 新增
- 从主程序 `src/nc_viewer` 复制 `parser / geometry / theme / tool / viewer`（viewer 改造接入离屏渲染），新增 `qt_offscreen.py`。
- 离屏渲染链路：`QOffscreenSurface + QOpenGLContext(2.0 compatibility, 4x MSAA) + QOpenGLFramebufferObject` → 每帧渲染 VBO 全量刀路（GL_LINES）+ 立即模式 overlay → `FBO.toImage()` → `convertToFormat(RGB888)` → `PIL Image.frombuffer`（按 `bytesPerLine` stride 逐行，防 45° 斜线）→ `ImageTk.PhotoImage` → canvas `create_image`。
- 渲染性能：data 版本 key（result/段过滤/G0/刀具）不变则 VBO 复用，旋转/缩放/平移仅重渲染图片；`_view_refresh_soon` 合并拖动刷新；**不抽稀、不断线**（延续主程序约束）。
- 轨迹渐进：移动级裁剪渲染（`_move_seg_map`：移动索引 → 完成后的累计段数），播放推进维护 `_trace_drawn`，渲染 `glDrawArrays` 裁剪到该移动边界。
- 播放/单步/复位/绘制到结尾/直达/演示、平移、缩放、适配、段过滤、G0 开关、刀具开关在离屏渲染下全部接通。
### 移除
- 主程序 Tk 画布刀路/overlay 渲染路径（回退保留于 `_qt_ready=False` 时）。
- 原 `qtgl.py`（原型-qt 的 QOpenGLWidget 嵌入方案，已被离屏方案取代）。
### 文档
- 初版：README、技术手册、开发流程、本文件（对齐主程序 1.1.0 规格）。
