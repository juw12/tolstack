@echo off
rem Start the tolstack web app on Windows. The first run creates .venv and installs the app (a few minutes).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install -e ".[app]" || goto :error
)
".venv\Scripts\python.exe" -m streamlit run app.py
goto :eof

:error
echo.
echo Setup failed. Python 3.10 to 3.13 must be installed and on PATH.
pause
