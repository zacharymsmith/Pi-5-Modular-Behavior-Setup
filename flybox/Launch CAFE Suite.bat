@echo off
REM Double-click to open the CAFE Analysis Suite on Windows.
REM First run installs the small dependencies if they're missing.
setlocal
cd /d "%~dp0"
echo Starting CAFE Analysis Suite...

REM prefer the 'py' launcher, fall back to 'python'
where py >nul 2>nul && (set "PY=py") || (set "PY=python")

%PY% -c "import cv2, numpy, PIL, matplotlib" 2>nul || (
  echo Installing dependencies ^(one time^)...
  %PY% -m pip install opencv-python numpy pillow matplotlib
)
%PY% -c "import tkinter" 2>nul || echo NOTE: tkinter missing - reinstall Python from python.org and keep the "tcl/tk and IDLE" option checked.

%PY% cafe_suite.py
pause
