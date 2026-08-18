@echo off
REM CAFE Analysis Suite - Windows launcher (conda). Builds/uses the isolated env
REM "flybox-cafe" from requirements.txt. Best run from the "Anaconda Prompt".
setlocal
cd /d "%~dp0"
set "ENVNAME=flybox-cafe"
echo Starting CAFE Analysis Suite...

REM --- locate conda base (folder that has Scripts\activate.bat) ---
set "CBASE="
REM 1) conda already on PATH (e.g. Anaconda Prompt) -> ask conda itself
where conda >nul 2>nul && for /f "delims=" %%i in ('conda info --base 2^>nul') do set "CBASE=%%i"
REM 2) env vars set by an active conda
if not defined CBASE if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\Scripts\activate.bat" set "CBASE=%CONDA_PREFIX%"
if not defined CBASE if defined CONDA_EXE for %%I in ("%CONDA_EXE%\..\..") do if exist "%%~fI\Scripts\activate.bat" set "CBASE=%%~fI"
REM 3) common install locations
if not defined CBASE for %%P in (
  "%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "%USERPROFILE%\miniforge3" "%USERPROFILE%\mambaforge"
  "%LOCALAPPDATA%\anaconda3" "%LOCALAPPDATA%\miniconda3" "%LOCALAPPDATA%\Continuum\anaconda3"
  "%ProgramData%\anaconda3" "%ProgramData%\miniconda3" "%ProgramData%\miniforge3" "%ProgramData%\mambaforge"
) do if exist "%%~P\Scripts\activate.bat" set "CBASE=%%~P"

if not defined CBASE (
  echo.
  echo Could not locate conda automatically.
  echo Easiest fix: open  "Anaconda Prompt"  ^(Start menu^), then run:
  echo     cd /d "%~dp0"
  echo     "Launch CAFE Suite.bat"
  echo.
  pause
  exit /b 1
)

echo Using conda at: %CBASE%
call "%CBASE%\Scripts\activate.bat" || ( echo Could not activate conda. & pause & exit /b 1 )

conda env list | findstr /C:"%ENVNAME%" >nul 2>nul
if errorlevel 1 goto CREATE
goto ACTIVATE

:CREATE
echo Creating conda env "%ENVNAME%" ^(one time, ~1-2 min^)...
call conda create -y -n %ENVNAME% python=3.11 || ( echo env create failed & pause & exit /b 1 )
call conda activate %ENVNAME% || ( echo could not activate %ENVNAME% & pause & exit /b 1 )
python -m pip install -r requirements-analysis.txt || ( echo dependency install failed & pause & exit /b 1 )
goto LAUNCH

:ACTIVATE
call conda activate %ENVNAME% || ( echo could not activate %ENVNAME% & pause & exit /b 1 )

:LAUNCH
echo Launching in conda env "%ENVNAME%"...
python cafe_suite.py
pause
