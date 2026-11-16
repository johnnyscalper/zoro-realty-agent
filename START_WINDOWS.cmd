@echo off
setlocal
pushd "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 scripts\bootstrap.py --guided
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Install Python 3.11 or newer from https://www.python.org/downloads/
        echo Then run this file again.
    ) else (
        python scripts\bootstrap.py --guided
    )
)
popd
pause
