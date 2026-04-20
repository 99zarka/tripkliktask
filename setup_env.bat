@echo off
echo ========================================================
echo Checking for existing virtual environment...
echo ========================================================

if exist venv (
    echo Virtual environment already exists. Using existing venv.
) else (
    echo Creating virtual environment...
    python -m venv venv
)

if exist venv\Scripts\activate (
    echo Activating virtual environment...
    call venv\Scripts\activate
    echo Installing dependencies from unified requirements...
    pip install -r requirements.txt
    
    echo.
    echo ========================================================
    echo Virtual environment setup complete!
    echo To start the platform, run: start_local.bat
    echo To test the platform, run: test.bat
    echo ========================================================
) else (
    echo Error: Virtual environment activation script not found.
    echo Please check if Python is installed correctly and accessible in your PATH.
)
pause
