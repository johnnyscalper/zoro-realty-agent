@echo off
setlocal
pushd "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 scripts\bootstrap.py --no-run
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Install Python 3.11 or newer from https://www.python.org/downloads/
        echo Then run this file again. Never paste API keys into this window as a command.
        goto finish
    ) else (
        python scripts\bootstrap.py --no-run
    )
)
if errorlevel 1 (
    echo Setup failed. Fix the environment and run this file again.
    goto finish
)
echo A separate key-entry window will open. Paste the key into that window.
".venv\Scripts\python.exe" scripts\configure_keys_gui.py
if errorlevel 1 (
    echo Key setup was cancelled or could not finish. No API check was started.
    goto finish
)
echo Keys saved. Checking the trade and rent APIs. No typing is needed now.
".venv\Scripts\python.exe" scripts\check_api.py --kind both --lawd-cd 11680 --month 202610
if errorlevel 1 echo API verification did not complete. Read the status above.
:finish
popd
pause
