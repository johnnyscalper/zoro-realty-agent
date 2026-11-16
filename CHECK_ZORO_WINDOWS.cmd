@echo off
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo First run CONFIGURE_ZORO_WINDOWS.cmd to prepare the environment.
    goto finish
)
echo Checking the configured model server. Keep GPU and SSH tunnel running.
".venv\Scripts\python.exe" scripts\check_zoro.py
if errorlevel 1 echo Some checks failed. Read the messages above; do not call this a verified demo yet.
:finish
popd
pause
