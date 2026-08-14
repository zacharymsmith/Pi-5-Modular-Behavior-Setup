@echo off
REM CAFE Analysis Suite - Windows launcher (conda). Explicitly activates conda, then
REM creates/uses the "flybox-cafe" env from requirements.txt and launches the suite.
setlocal
cd /d "%~dp0"
set "ENVNAME=flybox-cafe"
echo Starting CAFE Analysis Suite...

REM --- find the conda base (the folder that contains Scripts\activate.bat) ---
set "CBASE="
for %%P in (
  "%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3"
  "%USERPROFILE%\Anaconda3" "%USERPROFILE%\Miniconda3"
  "%LOCALAPPDATA%\anaconda3" "%LOCALAPPDATA%\miniconda3"
  "%ProgramData%\anaconda3" "%ProgramData%\miniconda3"
) do if exist "%%~P\Scripts\activate.bat" set "CBASE=%%~P"
REM fallback: derive base from CONDA_EXE if conda is already known to this shell
if not defined CBASE if defined CONDA_EXE for %%I in ("%CONDA_EXE%\..\..") do (
  if exist "%%~fI\Scripts\activate.bat" set "CBASE=%%~fI"
)

if not defined CBASE (
  echo.
  echo Could not locate conda automatically.
  echo Open the "Anaconda Prompt", then run:
  echo     cd /d "%~dp0"
  echo     "Launch CAFE Suite.bat"
  echo.
  pause
  exit /b 1
)

REM --- activate conda (bootstraps it into this cmd session) ---
call "%CBASE%\Scripts\activate.bat" || ( echo Could not activate conda. & pause & exit /b 1 )

REM --- create the env once from requirements.txt ---
conda env list | findstr /C:"%ENVNAME%" >nul 2>nul
if errorlevel 1 goto CREATE
goto ACTIVATE

:CREATE
echo Creating conda env "%ENVNAME%" ^(one time, ~1-2 min^)...
call conda create -y -n %ENVNAME% python=3.11 || ( echo env create failed & pause & exit /b 1 )
call conda activate %ENVNAME% || ( echo could not activate %ENVNAME% & pause & exit /b 1 )
python -m pip install -r requirements.txt || ( echo dependency install failed & pause & exit /b 1 )
goto LAUNCH

:ACTIVATE
call conda activate %ENVNAME% || ( echo could not activate %ENVNAME% & pause & exit /b 1 )

:LAUNCH
echo Launching in conda env "%ENVNAME%"...
python cafe_suite.py
pause
