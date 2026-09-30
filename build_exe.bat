@echo off
setlocal
if not exist .venv\Scripts\python.exe (
 echo Python environment not found. Run setup.bat first.
 pause
 exit /b 1
)
call .venv\Scripts\activate.bat
python -c "import PySide6, PyInstaller" 2>nul
if errorlevel 1 (
 echo Dependencies are missing. Run setup.bat first.
 pause
 exit /b 1
)
python -m PyInstaller --noconfirm --clean --windowed --name ThunderbirdArchiveViewer app.py
if errorlevel 1 pause & exit /b 1
echo.
echo Build complete: dist\ThunderbirdArchiveViewer\ThunderbirdArchiveViewer.exe
pause
