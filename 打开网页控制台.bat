@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Manwa Web Console Launcher

rem ============================================================
rem  双击本文件：启动本地网页控制台并自动打开浏览器
rem  运行方式与 运行下载.bat 一致：venv -> dist\ManwaDownloader.exe -> 系统 python
rem ============================================================

set PORT=8787
set RUN=

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import aiohttp, Crypto" >nul 2>&1
    if !errorlevel! equ 0 set RUN="%~dp0.venv\Scripts\python.exe" "%~dp0download.py"
)
if not defined RUN if exist "%~dp0dist\ManwaDownloader.exe" set RUN="%~dp0dist\ManwaDownloader.exe"
if not defined RUN set RUN=python "%~dp0download.py"

echo.
echo ============================================
echo    Manwa Web Console  -  本地网页控制台
echo ============================================
echo.

rem --- 已在运行则直接打开浏览器 ---
netstat -ano | findstr "127.0.0.1:%PORT%" | findstr LISTENING >nul
if not errorlevel 1 (
    echo   检测到服务已在运行，直接打开浏览器...
    start "" http://127.0.0.1:%PORT%/
    goto :end
)

echo   正在启动服务（可写模式，仅监听本机）...
start "Manwa Web Console" %RUN% --serve --allow-write --port %PORT%

set /a TRIES=0
:wait
netstat -ano | findstr "127.0.0.1:%PORT%" | findstr LISTENING >nul
if not errorlevel 1 goto :ready
set /a TRIES+=1
if !TRIES! geq 20 goto :timeout
ping -n 2 127.0.0.1 >nul
goto :wait

:ready
echo   已就绪：http://127.0.0.1:%PORT%/
start "" http://127.0.0.1:%PORT%/
goto :end

:timeout
echo.
echo   [X] 启动超时：端口 %PORT% 在 20 秒内没有响应
echo       1. 看新弹出的那个窗口里打印了什么错误
echo       2. 端口被占用时，把上面 set PORT=8787 改成 8788 再试
echo       3. 也可以在命令行手动运行：python download.py --serve --allow-write
echo.
pause

:end
endlocal
