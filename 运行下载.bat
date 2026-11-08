@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem --- Speed preset: global concurrent images / concurrent chapters ---
rem     64 / 8  = default (fast, safe)
rem     96 / 12 = turbo (needs good bandwidth)
rem     32 / 4  = conservative (if the site starts rejecting requests)
set WORKERS=64
set CHWORKERS=8

rem --- Choose runner: working venv -> bundled exe -> system python ---
set RUN=
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import aiohttp, Crypto" >nul 2>&1
    if !errorlevel! equ 0 set RUN="%~dp0.venv\Scripts\python.exe" "%~dp0download.py"
)
if not defined RUN if exist "%~dp0dist\ManwaDownloader.exe" set RUN="%~dp0dist\ManwaDownloader.exe"
if not defined RUN set RUN=python "%~dp0download.py"
set RUN=%RUN% --workers %WORKERS% --ch-workers %CHWORKERS%

:loop
echo.
echo ============================================
echo    Manwa Manga Downloader v5 - fast mode (manwaqi.cc)
echo ============================================
echo.
echo   Examples:
echo     1728  = YiRenZhiXia
echo     Or paste URL ^> https://www.manwaqi.cc/comic/1728
echo.
set /p COMIC_ID=Enter Comic ID or URL (q=quit): 

if "%COMIC_ID%"=="" goto loop
if /i "%COMIC_ID%"=="q" goto end
if /i "%COMIC_ID%"=="quit" goto end

echo.
echo   Fetching info...
%RUN% %COMIC_ID% --info
if errorlevel 1 (
    echo.
    echo   [X] 取不到漫画信息（多半是网络连不上站点，看上面的提示）
    echo       有代理的话在 config.json 里填 "proxy"
    goto loop
)

echo.
echo   Download range:
echo     N           = Single chapter N (e.g. 1 = ch.1)
echo     N-M         = Chapters N to M   (e.g. 1-10)
echo     all         = ALL chapters
echo     [Enter]     = Test: first 5 chapters
echo.
set /p RANGE=Chapter number or range: 

echo.
if "%RANGE%"=="" (
    echo [TEST] First 5 chapters...
    %RUN% %COMIC_ID% --end 5
    goto loop
)

if /i "%RANGE%"=="all" (
    echo [FULL] All chapters...
    %RUN% %COMIC_ID%
    goto loop
)

rem --- Check for N-M range format ---
echo %RANGE% | find "-" >nul
if !errorlevel! equ 0 (
    for /f "tokens=1,2 delims=-" %%a in ("%RANGE%") do (
        set _A=%%a
        set _B=%%b
        call :isnum _A
        if !errorlevel! equ 0 (
            call :isnum _B
            if !errorlevel! equ 0 (
                echo [RANGE] Chapters %%a-%%b...
                %RUN% %COMIC_ID% --start %%a --end %%b
                goto loop
            )
        )
    )
)

rem --- Check for pure number ---
call :isnum RANGE
if !errorlevel! equ 0 (
    echo [SINGLE] Chapter %RANGE%...
    %RUN% %COMIC_ID% --start %RANGE% --end %RANGE%
    goto loop
)

echo Unknown format. Try again.
goto loop

:isnum
set _v=!%~1!
if "!_v!"=="" exit /b 1
for /f "delims=0123456789" %%x in ("!_v!") do exit /b 1
exit /b 0

:end
echo Bye!
pause
