@echo off
setlocal
set "PYTHONUTF8=1"
set "MAGVIEW_PYTHON=%~dp0..\..\.venv-windows\Scripts\python.exe"
if not exist "%MAGVIEW_PYTHON%" (
    call "%~dp0setup.cmd"
    if errorlevel 1 exit /b 1
)
"%MAGVIEW_PYTHON%" -m magview %*
exit /b %errorlevel%
