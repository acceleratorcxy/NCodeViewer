# NC 刀路查看器 Qt 离屏渲染实验版 (NCodeViewer Embed)

> 主程序 [NCodeViewer](https://github.com/acceleratorcxy/NCodeViewer) 的渲染实验分支：**界面与交互完全保持主程序 Tk 实现，仅把画布刀路渲染替换为 Qt 离屏 OpenGL**，目标是在完整刀路（2.5 万+ 段，不抽稀）下实现 60fps 交互。

可视化查看 NC/G 代码刀路的桌面工具。不同 F 进给以不同颜色绘制，支持按行号（或 N 号）定位刀具实际位置、多文件加载切换、代码搜索跳转，以及 CAD 风格的轨道球旋转视图。

## 特性

- 解析 NC/G 代码（`.MPF/.NC/.CNC/.TXT`），按模态还原刀路
- 不同 F 值用不同颜色绘制，G0 快移灰色，颜色图例同步显示
- 圆弧（G2/G3）按角度与弧长双因素离散，小圆弧也能显示曲率
- 按行号 / N 号定位刀具实际位置（十字 + 当前段高亮 + 加工方向箭头）
- 画布内左键单击刀路即跳转到对应 NC 行（按折线段最近距离拾取）
- 左侧文件栏一次加载多个文件，随时切换；顶部栏内嵌加载进度
- 代码搜索定位与跳转
- 轨道球（四元数 ArcBall）旋转：中键旋转（空白处为平面滚转）/ 左键平移 / 滚轮缩放
- 深色现代化界面（ttk clam 定制主题，纯标准库）
- 程序统计：X/Y/Z 行程、S/F 范围、G 次数、加工时间 + 详情二级窗口
- F 进给趋势曲线：按 F 档位着色的折线图二级窗口
- 程序逐行运行：连续播放（合并跳行 1-100）/单步/直达/演示到行（**移动级渐进**：刀路逐移动增长，走到哪显示到哪）
- 刀具：aptsource 自动解析、直径剖面图、六种类型自定义、画布 3D 模型（刀尖对刀）
- 按段浏览：抬刀平面可编辑、段导航、仅显示当前段（段内播放）
- 文件列表双区：MPF 与 APT 按文件名/程序名关联互高亮
- **Qt 离屏 OpenGL 渲染**：全量刀路 VBO（合并折线、不抽稀），旋转/缩放/平移只更新 MVP 重渲染；overlay（XYZ 轴/当前行/十字线/方向箭头/3D 刀具）由 GL 立即模式绘制；`QOffscreenSurface + FBO` 离屏渲染 → 读回 → Tk canvas 显示，**不产生独立 Qt 窗口**，界面外观与主程序一致

## 文档

- **[技术手册](docs/技术手册.md)**：总结性文档，含概述、架构（Qt 离屏渲染链路）、算法、操作、测试、FAQ 及一切注意事项。
- **[开发流程](docs/开发流程.md)**：开发 / 测试 / 打包流程规范，供严格遵循。
- **[更改记录](CHANGELOG.md)**：历次改动留痕。

## 环境要求

- Python ≥ 3.8，且必须自带 Tkinter（验证 3.8 / 3.11）
- **运行时依赖（与主程序不同）**：`PyQt5>=5.15,<5.16`（Qt 离屏渲染）、`numpy`、`Pillow`
- **Windows 7 目标**：PyQt5 5.15.x 为最后支持 Win7 的 Qt5 版本；numpy 请用 `<2`（1.24.x 最后支持 py38/Win7）、Pillow 请用 `<11`；打包须用 conda 的 Python 3.8 + PyInstaller 5.x（详见开发流程）

## 安装

```bash
# 推荐：可编辑安装，使 `nc-viewer` 命令与 `import nc_viewer` 可用
python -m pip install -e .

# 若仅开发测试，安装 pytest 即可
python -m pip install pytest
```

## 运行

```bash
# 方式一：模块入口
python -m nc_viewer

# 方式二：带文件启动
python -m nc_viewer "样例文件\数控程序\...\xxx.MPF"

# 方式三：安装后使用命令行脚本
nc-viewer
```

> 命令行仅支持加载**单个**文件；多文件请用界面「打开文件…」多选。

## 运行测试

```bash
python -m pytest -q
```

共 170 例：解析（46）+ 几何（8）+ 刀具（14）+ 离屏渲染（9）+ 界面结构（93）。遵循 TDD，改动后需全绿（green）再打包。py38 / py311 双环境全绿。

## 打包 EXE

```bash
build_exe.bat
```

产物为单文件 `dist\NCViewer.exe`。打包前须保证测试全绿，打包后做冒烟测试。
构建解释器自动探测顺序：`NCVIEWER_PY` 环境变量 → conda `python38` 环境（**Win7 目标构建优先**）→ `py -3.11`（Win10/11 构建）。conda 构建时自动内置 UCRT 运行时并固定 PyInstaller 5.13.2。

## 项目结构

```
原型-embed/
├── src/nc_viewer/        # 源码包
│   ├── parser.py         # NC/G 代码解析（纯计算，不依赖 Tkinter）
│   ├── geometry.py       # 配色 / 圆弧离散 / 四元数轨道旋转 / 投影（纯计算）
│   ├── theme.py          # 深色现代化主题（配色/字体常量 + apply_theme）
│   ├── tool.py           # 刀具模型（aptsource 解析/剖面轮廓/摘要）
│   ├── qt_offscreen.py   # Qt 离屏渲染器（QOffscreenSurface + FBO + VBO + overlay）
│   ├── viewer.py         # Tkinter 主窗口（画布渲染接入 qt_offscreen）
│   └── __main__.py       # python -m nc_viewer 入口
├── tests/                # pytest 单元测试（170 例）
│   ├── test_parser.py
│   ├── test_geometry.py
│   ├── test_qt_offscreen.py
│   └── test_viewer_ui.py
├── docs/                 # 发布版文档（不含本机隐私信息）
├── CHANGELOG.md          # 更改记录
├── launcher.py           # PyInstaller 打包入口
├── build_exe.bat         # 打包脚本
└── pyproject.toml        # 构建与测试配置
```
