@echo off
echo ========================================================
echo Starting Containerized Travel Mapping Engine...
echo ========================================================
echo.
echo Building images and spinning up the Docker cluster...
echo.

docker-compose up --build

echo.
pause
