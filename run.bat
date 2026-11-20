@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo The application is not installed yet. Please run setup.bat first.
  pause & exit /b 1
)
call ".venv\Scripts\activate.bat"

rem ---- dependency check
python -c "import faster_whisper" >nul 2>nul || echo [WARNING] faster-whisper is missing - run setup.bat to enable local transcription.
set "FF=0"
where ffmpeg >nul 2>nul && where ffprobe >nul 2>nul && set "FF=1"
if exist "ffmpeg\bin\ffmpeg.exe" set "FF=1"
if "%FF%"=="0" echo [WARNING] FFmpeg not found - videos cannot be rendered. See README.md ^(step 3^).

echo Starting Local Bulk Video Production Studio...
echo The browser opens automatically. Keep this window open while you work; close it to stop the app.
python main.py
echo.
echo The application has stopped.
pause
