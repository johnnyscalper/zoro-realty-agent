@echo off
setlocal
pushd "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 scripts\bootstrap.py --no-run
) else (
    python scripts\bootstrap.py --no-run
)
if errorlevel 1 goto finish
".venv\Scripts\python.exe" scripts\configure_zoro.py
:finish
popd
pause
