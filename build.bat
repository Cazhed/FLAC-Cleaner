@echo off
rem Builds dist\FLACCleaner.exe. Needs 64-bit Python 3.11+ from python.org (tick "Add to PATH").
setlocal
cd /d "%~dp0"

where py >nul 2>nul && (set PY=py -3) || (set PY=python)

if not exist .venv (
    echo Creating virtual environment...
    %PY% -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat || goto :error

python -m pip install --upgrade pip >nul
pip install -r requirements-dev.txt || goto :error

echo Running tests...
python -m pytest -q || goto :error

echo Building exe...
pyinstaller --noconfirm FLACCleaner.spec || goto :error

echo.
echo Done: %cd%\dist\FLACCleaner.exe
pause
exit /b 0

:error
echo.
echo Build failed.
pause
exit /b 1
