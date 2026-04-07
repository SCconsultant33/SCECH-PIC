@echo off
cd /d "%~dp0"

REM Prefer Windows Python launcher if available.
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m streamlit run app.py
  goto :end
)

REM Fallback to python on PATH.
where python >nul 2>nul
if %errorlevel%==0 (
  python -m streamlit run app.py
  goto :end
)

echo.
echo Python was not found on your system PATH.
echo Install Python 3 from https://www.python.org/downloads/windows/
echo and make sure "Add python.exe to PATH" is enabled.
echo.
echo Then run these setup commands once:
echo   py -3 -m pip install -r requirements.txt
echo   py -3 -m playwright install chromium
echo.

:end
pause
