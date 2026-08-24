@echo off
REM Windows Build Script for ByteProof
echo ==========================================
echo Building ByteProof for Windows
echo ==========================================

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python is not installed or not in PATH. Please install Python.
    pause
    exit /b 1
)

REM Install dependencies
echo.
echo Installing dependencies...
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

REM Check for logo.ico
if not exist "logo\logo.ico" (
    echo.
    echo WARNING: logo\logo.ico not found. The executable will have the default icon.
    echo Please convert logo\logo.png to logo\logo.ico for a branded executable.
)

REM Embed prompt assets into the binary so no loose prompt files ship in the install
echo.
echo Embedding prompt assets...
if exist "scripts\embed_prompts.py" (
    python "scripts\embed_prompts.py"
    if %errorlevel% neq 0 (
        echo Failed to embed prompt assets.
        pause
        exit /b 1
    )
) else (
    echo scripts\embed_prompts.py not found; using committed src\prompt_data.py.
)

REM Build with PyInstaller
echo.
echo Building executable...
python -m PyInstaller --noconfirm "ByteProof_win.spec"
if %errorlevel% neq 0 (
    echo Build failed.
    pause
    exit /b 1
)

REM Create the distribution zip
echo.
echo Creating distribution zip...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\ByteProof\*' -DestinationPath 'ByteProof_Windows.zip' -Force"
if %errorlevel% neq 0 (
    echo Warning: could not create zip. The build is still in dist\ByteProof\ByteProof.exe
) else (
    echo Zip created: ByteProof_Windows.zip
)

echo.
echo ==========================================
echo Build complete!
echo The executable is located in: dist\ByteProof\ByteProof.exe
echo ==========================================
pause
