@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo  Local Bulk Video Production Studio - setup
echo ============================================================
echo.

rem ---- 1. Python
set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY ( py -3 --version >nul 2>nul && set "PY=py -3" )
if not defined PY (
  echo [ERROR] Python was not found.
  echo   Install Python 3.10 or newer from https://www.python.org/downloads/
  echo   IMPORTANT: tick "Add python.exe to PATH" in the installer, then run setup.bat again.
  pause & exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" || (
  echo [ERROR] Python 3.10 or newer is required. Your version:
  %PY% --version
  pause & exit /b 1
)
echo [OK] Python found:
%PY% --version

rem ---- 2. Virtual environment
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv || ( echo [ERROR] Could not create the virtual environment. & pause & exit /b 1 )
)
call ".venv\Scripts\activate.bat"

rem ---- 3. Dependencies
echo Installing dependencies (needs internet this one time)...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Dependency installation failed. Check your internet connection and run setup.bat again.
  pause & exit /b 1
)
echo [OK] Python packages installed.

rem ---- 4. FFmpeg
set "FF=0"
where ffmpeg >nul 2>nul && where ffprobe >nul 2>nul && set "FF=1"
if exist "ffmpeg\bin\ffmpeg.exe" if exist "ffmpeg\bin\ffprobe.exe" set "FF=1"
if "%FF%"=="1" (
  echo [OK] FFmpeg found.
) else (
  echo.
  echo [ACTION NEEDED] FFmpeg was not found. Install it with ONE of these options:
  echo   A^) Open a terminal and run:   winget install Gyan.FFmpeg      ^(then open a NEW terminal^)
  echo   B^) Download "ffmpeg-release-full.7z" from https://www.gyan.dev/ffmpeg/builds/
  echo      and unpack it so that this file exists:  %~dp0ffmpeg\bin\ffmpeg.exe
  echo   Then run setup.bat again to confirm.
)

rem ---- 5. Optional pieces
python -c "import tkinter" >nul 2>nul || echo [NOTE] Python's tkinter is missing: the Browse... folder button will not work, but you can type folder paths.

rem ---- 6. Folders + transcription model
if not exist "data" mkdir data
for %%D in (logs tmp thumbs cache previews transcripts) do if not exist "data\%%D" mkdir "data\%%D"
echo.
set /p DL="Download the transcription model now so it works offline later? (about 150 MB) [Y/n] "
if /i not "%DL%"=="n" (
  python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8'); print('[OK] Model downloaded.')" || echo [WARNING] Model download failed. It will be retried on first use (internet needed once).
)
echo.
echo Setup finished. Start the application with run.bat
pause
