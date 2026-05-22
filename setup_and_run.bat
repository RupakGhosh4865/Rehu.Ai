@echo off
title SuperHuman Platform
color 0A

echo.
echo ============================================
echo   SuperHuman AI Persona Platform
echo ============================================
echo.

cd /d "%~dp0backend"

echo Checking Python...
python --version
if errorlevel 1 (
    echo.
    echo ERROR: Python not found. Install from https://python.org
    echo Make sure "Add Python to PATH" is checked during install.
    echo.
    pause
    exit /b 1
)

echo Clearing Python cache...
if exist app\__pycache__ rd /s /q app\__pycache__
if exist __pycache__ rd /s /q __pycache__

if not exist venv\Scripts\activate.bat goto install_venv
echo Virtual environment found.
call venv\Scripts\activate.bat
goto run_server

:install_venv
echo Creating virtual environment...
python -m venv venv
if errorlevel 1 (
    echo ERROR: Could not create virtual environment.
    pause
    exit /b 1
)
call venv\Scripts\activate.bat

echo.
echo Installing packages (first time: 2-3 min)...
echo.
python -m pip install --upgrade pip --quiet

python -m pip install fastapi "uvicorn[standard]" python-multipart aiofiles "pydantic>=2.0" pydantic-settings python-dotenv httpx websockets "openai>=1.74.0" "deepgram-sdk>=3.0.0" elevenlabs "rank-bm25>=0.2.2" beautifulsoup4 pypdf

if errorlevel 1 (
    echo.
    echo ERROR: Package install failed. See above.
    pause
    exit /b 1
)
echo.
echo Packages installed OK.

:run_server
if exist "..\.env" copy /Y "..\.env" ".env" >nul 2>&1

echo.
echo ============================================
echo   Open: http://localhost:8000
echo   Stop: Ctrl+C
echo ============================================
echo.

set PYTHONDONTWRITEBYTECODE=1
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

echo.
echo Server stopped. Press any key to close.
pause >nul
