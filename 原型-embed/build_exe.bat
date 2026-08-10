@echo off
chcp 65001 >nul
REM ============================================================
REM  打包 NC 刀路查看器为单文件 EXE (PyInstaller)
REM  用法: 双击运行 或 在命令行执行  build_exe.bat
REM  产物: dist\NCViewer.exe
REM
REM  构建解释器自动探测顺序:
REM    1. 环境变量 NCVIEWER_PY 显式指定 (python.exe 完整路径)
REM    2. conda python38 环境 (Win7 目标构建优先)
REM    3. py -3.11 / py -3.14 (Win10/11 构建)
REM
REM  注意: Win7 目标必须用 Python 3.8 构建 (3.9+ 不支持 Win7);
REM        PyInstaller 需为 5.x (6.10+ 的 bootloader 不支持 Win7);
REM        conda 环境构建时自动把 UCRT DLL 打包进 EXE,
REM        使裸装 Win7 (无 KB2999226 补丁) 也能运行;
REM        样例文件仅本地测试用, 不打包进 EXE。
REM
REM  脚本规范: 凡以引号开头的命令行不得以重定向结尾
REM  (cmd 会剥首尾引号), 重定向一律前置; 命令输出用临时文件传递。
REM ============================================================
setlocal
cd /d "%~dp0"

REM ---- 1) 环境变量显式覆盖 ----
if defined NCVIEWER_PY (
    set "PYCMD="%NCVIEWER_PY%""
    goto :py_check
)

REM ---- 2) 探测 conda python38 环境 (Win7 目标构建优先) ----
REM     优先通过 where conda 动态定位 conda 根目录 (不硬编码本机路径)
set CONDA_EXE=
for /f "delims=" %%c in ('where conda 2^>nul') do if not defined CONDA_EXE set "CONDA_EXE=%%c"
if defined CONDA_EXE (
    for %%r in ("%CONDA_EXE%\..\..") do (
        if exist "%%~fr\envs\python38\python.exe" set "PYCMD="%%~fr\envs\python38\python.exe""
    )
)
if defined PYCMD goto :py_check
REM     常见安装位置兜底 (基于环境变量, 无本机硬编码路径)
for %%E in ("%USERPROFILE%\anaconda3\envs\python38"
            "%USERPROFILE%\miniconda3\envs\python38"
            "%ProgramData%\anaconda3\envs\python38"
            "%ProgramData%\miniconda3\envs\python38") do (
    if exist "%%~E\python.exe" set "PYCMD="%%~E\python.exe""
)
if defined PYCMD goto :py_check

REM ---- 3) 回退 py 启动器 (Win10/11 构建) ----
where py >nul 2>nul
if errorlevel 1 goto :nopy
set "PYCMD=py -3.11"
>nul 2>nul py -3.11 -c "import tkinter"
if errorlevel 1 (
    set "PYCMD=py -3.14"
    >nul 2>nul py -3.14 -c "import tkinter"
    if errorlevel 1 goto :notk
)

:py_check
REM 校验 Tkinter 可用
>nul 2>nul %PYCMD% -c "import tkinter"
if errorlevel 1 goto :notk

REM 查询解释器前缀; 前缀下存在 ucrtbase.dll 即 conda 环境
set HAS_UCRT=
> "%TEMP%\ncv_prefix.txt" %PYCMD% -c "import sys; print(sys.prefix)"
for /f "usebackq delims=" %%p in ("%TEMP%\ncv_prefix.txt") do set "PY_PREFIX=%%p"
del /q "%TEMP%\ncv_prefix.txt" >nul 2>nul
if exist "%PY_PREFIX%\ucrtbase.dll" set "HAS_UCRT=1"

echo [1/4] 构建解释器: %PY_PREFIX%
%PYCMD% --version

echo [2/4] 检查 PyInstaller ...
>nul 2>nul %PYCMD% -m pip show pyinstaller
if errorlevel 1 (
    echo        未找到，正在安装 PyInstaller 5.13.2 ...
    %PYCMD% -m pip install "pyinstaller==5.13.2"
    if errorlevel 1 goto :fail
    goto :pi_ok
)
if not defined HAS_UCRT goto :pi_ok
> "%TEMP%\ncv_piver.txt" %PYCMD% -m PyInstaller --version
for /f "usebackq delims=" %%v in ("%TEMP%\ncv_piver.txt") do set "PI_VER=%%v"
del /q "%TEMP%\ncv_piver.txt" >nul 2>nul
if not "%PI_VER:~0,1%"=="5" (
    echo        当前 PyInstaller %PI_VER% 的 bootloader 不支持 Win7，固定为 5.13.2 ...
    %PYCMD% -m pip install "pyinstaller==5.13.2"
    if errorlevel 1 goto :fail
)
:pi_ok

REM --key 字节码加密依赖 tinyaes (AES 实现), 缺失时自动安装
>nul 2>nul %PYCMD% -m pip show tinyaes
if errorlevel 1 (
    echo        未找到 tinyaes，正在安装 --key 字节码加密依赖 ...
    %PYCMD% -m pip install tinyaes
    if errorlevel 1 goto :fail
)

REM 加密密钥: NCVIEWER_KEY 环境变量覆盖, 否则每次打包重新生成
REM (EXE 自包含解密, 密钥无需跨版本一致)。密钥随打包产物保存到
REM dist\.ncviewer_key 与 EXE 放一起, 不入库 (dist 已被 git 忽略),
REM 每次打包更新。
REM 注: 用 goto 链而非 if 括号块, 括号块内 %KEY% 在解析时展开
REM (尚未赋值) 会导致写入空值。
if defined NCVIEWER_KEY (
    set "KEY=%NCVIEWER_KEY%"
    goto :key_ok
)
> "%TEMP%\ncv_key.txt" %PYCMD% -c "import secrets; print(secrets.token_hex(16))"
for /f "usebackq delims=" %%k in ("%TEMP%\ncv_key.txt") do set "KEY=%%k"
del /q "%TEMP%\ncv_key.txt" >nul 2>nul
:key_ok

echo [3/4] 清理旧构建产物 ...
if exist build rmdir /s /q build
if exist dist\NCViewer.exe del /q dist\NCViewer.exe

echo [4/4] 正在打包 EXE (单文件, 不含样例文件, 图标 assets\NCodeViewer_icon.ico)...
REM Qt 离屏渲染器经 versionFunctions() 运行时动态导入
REM PyQt5.QtOpenGL / PyQt5._QOpenGLFunctions_2_0 (独立 .pyd),
REM PyInstaller 静态分析发现不了, 必须显式 hidden-import,
REM 否则打包版 Qt 渲染初始化失败回退 Tk 渲染
set HIDDEN_IMPORTS=--hidden-import=PyQt5.QtOpenGL --hidden-import=PyQt5._QOpenGLFunctions_2_0
REM 窗口图标数据内置 (--add-data): 运行时 _set_icon 从 _MEIPASS 读
REM NCodeViewer_icon.ico 设置标题栏/二级窗口图标 (仅 --icon 只改 EXE
REM 文件图标, 运行时窗口无图标)
set ICON_DATA=--add-data "assets\NCodeViewer_icon.ico;assets"
if defined HAS_UCRT (
    echo        检测到 conda 环境，内置 UCRT 运行时，支持裸装 Win7...
    %PYCMD% -m PyInstaller --onefile --windowed --name NCViewer --paths src --icon "assets\NCodeViewer_icon.ico" %HIDDEN_IMPORTS% %ICON_DATA% --key "%KEY%" --add-binary "%PY_PREFIX%\ucrtbase.dll;." --add-binary "%PY_PREFIX%\api-ms-win-crt-*.dll;." --add-binary "%PY_PREFIX%\vcruntime140.dll;." --add-binary "%PY_PREFIX%\vcruntime140_1.dll;." --clean launcher.py
) else (
    %PYCMD% -m PyInstaller --onefile --windowed --name NCViewer --paths src --icon "assets\NCodeViewer_icon.ico" %HIDDEN_IMPORTS% %ICON_DATA% --key "%KEY%" --clean launcher.py
)
if errorlevel 1 goto :fail

REM 密钥随打包产物保存 (与 EXE 同目录, 不入库; 每次打包更新)
echo %KEY%>"dist\.ncviewer_key" 2>nul

echo.
echo 打包完成！产物: %~dp0dist\NCViewer.exe
pause
exit /b 0

:nopy
echo [错误] 未找到 py 启动器，请先安装 Python 3.11 / 3.14，或设置 NCVIEWER_PY 指向 conda python38 的 python.exe。
goto :fail

:notk
echo [错误] 当前解释器未捆绑 Tkinter。Win7 构建请用 conda 的 python38 环境(自带 Tk)。
goto :fail

:fail
echo [错误] 打包失败，请检查上方日志。
pause
exit /b 1
