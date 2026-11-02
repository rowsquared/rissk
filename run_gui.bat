@echo off
REM RISSK GUI launcher for Windows (uv-aware)
cd /d "%~dp0"

echo.
echo Starting RISSK GUI...
echo Open your browser at: http://localhost:8080
echo (Press Ctrl+C to stop)
echo.
uv run --extra gui python app\main.py
