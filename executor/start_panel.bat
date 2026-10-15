@echo off
rem ============================================================
rem  MaaEnd executor panel launcher (shipped inside the
rem  maaend_bridge plugin).
rem
rem  MUST run as administrator: Seize (SendInput) cannot reach
rem  the game window from a non-elevated process. Without it the
rem  log shows successful clicks while the game does nothing.
rem
rem  Usage: start_panel.bat [port]
rem
rem  This is the manual fallback. The plugin panel can also
rem  launch the executor itself ("Start executor" button), which
rem  does the same thing through ShellExecuteW(runas).
rem ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"
chcp 65001 >nul 2>&1

set PORT=%1
if "%PORT%"=="" set PORT=12702

rem ---- Find the interpreter that has MaaFramework installed ----
rem  A plugin-shipped copy of the executor, so the interpreter is
rem  NOT necessarily the one N.E.K.O. runs on. The only reliable
rem  test is importing maa.
rem
rem  Order: NEKO_MAAEND_PYTHON -> python_path.txt (written by the
rem  plugin's settings panel) -> py -3.11 -> python.
set "PY="
for %%C in ("%NEKO_MAAEND_PYTHON%" "py -3.11" "python") do (
    if not defined PY (
        %%~C -c "import maa" >nul 2>&1
        if not errorlevel 1 set "PY=%%~C"
    )
)
if not defined PY (
    if exist "%~dp0python_path.txt" (
        set /p CANDIDATE=<"%~dp0python_path.txt"
        !CANDIDATE! -c "import maa" >nul 2>&1
        if not errorlevel 1 set "PY=!CANDIDATE!"
    )
)
if not defined PY (
    echo [ERROR] No Python 3.11 with MaaFramework found.
    echo         Check a candidate with:  ^<python^> -c "import maa"
    echo         Then point the plugin setting "executor_python" at it,
    echo         or set the NEKO_MAAEND_PYTHON environment variable.
    pause
    exit /b 1
)

rem S-1-5-19 = local Administrators SID, visible in HKU only when elevated.
reg query "HKU\S-1-5-19" >nul 2>&1
if errorlevel 1 (
    echo [panel] Requesting administrator rights...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%PORT%' -Verb RunAs"
    exit /b 0
)

echo [panel] Administrator confirmed.
echo [panel] Interpreter: %PY%
echo [panel] Starting panel backend on 127.0.0.1:%PORT%
echo.
rem No --open: the control UI is the plugin's own panel, not a browser tab.
%PY% panel_server.py --port %PORT%
echo.
echo [panel] Backend stopped.
pause
endlocal
