@echo off
echo ========================================================
echo Running Unit Tests for Travel Mapping Engine...
echo ========================================================
echo.

REM Ensure the testing module uses the current Python environment
python -m pytest backend/tests/ -v

echo.
echo ========================================================
echo Test execution complete.
echo ========================================================
pause
