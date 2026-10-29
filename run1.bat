@echo off
cd /d "%~dp0"
echo Checking file names...
if exist "app (5).py" if not exist app.py ren "app (5).py" app.py
if exist "app-1.py" if not exist app.py ren "app-1.py" app.py
if exist "index (2).html" if not exist index.html ren "index (2).html" index.html
if exist "index-1.html" if not exist index.html ren "index-1.html" index.html
if exist "index-2.html" if not exist index.html ren "index-2.html" index.html
for %%F in (app.py index.html admin.html) do (
  if not exist %%F (
    echo ERROR: %%F not found in this folder.
    pause
    exit /b
  )
)
where python >nul 2>nul
if %errorlevel%==0 (
  python app.py
) else (
  py app.py
)
pause
