@echo off
echo ========================================================
echo Starting Local Travel Mapping Engine Services...
echo ========================================================

REM Start the FastAPI backend in a new command window
echo [SERVER] Starting FastAPI API on port 8000...
start cmd /k "title FastAPI Backend && cd backend && python -m uvicorn main:app --reload --port 8000"

REM Wait 3 seconds to let the API and DB initialize
timeout /t 3 /nobreak > NUL

REM Start the Streamlit frontend in a new command window
echo [FRONTEND] Starting Streamlit Dashboard on port 8501...
start cmd /k "title Streamlit Frontend && cd frontend && python -m streamlit run app.py"

echo.
echo All Local Services Spawned Successfully! 
echo 🗺️  Streamlit UI       : http://127.0.0.1:8501
echo ⚙️  API Swagger Docs   : http://127.0.0.1:8000/docs
echo ========================================================
