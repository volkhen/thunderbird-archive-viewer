@echo off
setlocal
if not exist .venv\Scripts\python.exe (
 echo Python environment not found. Run setup.bat first.
 pause
 exit /b 1
)
call .venv\Scripts\activate.bat
python -c "import PySide6" 2>nul
if errorlevel 1 (
 echo Dependencies are missing. Run setup.bat first.
 pause
 exit /b 1
)
python app.py
