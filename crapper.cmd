@echo off
rem Run the CRAP tool on native Windows, creating a local virtualenv on first use.
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" (
  python -m venv "%~dp0.venv" || exit /b 1
  "%PY%" -m pip install -e "%~dp0." || exit /b 1
)
"%PY%" -m crapper %*
exit /b %ERRORLEVEL%
