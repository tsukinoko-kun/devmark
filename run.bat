@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 goto python
if not exist ".venv\Scripts\python.exe" uv venv --python ">=3.11" --no-python-downloads .venv
if errorlevel 1 exit /b 1
uv pip install --python ".venv\Scripts\python.exe" --quiet -r requirements.txt
if errorlevel 1 exit /b 1
goto benchmark

:python
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 'Python 3.11 or newer is required.')"
    if errorlevel 1 exit /b 1
    if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python 3.11 or newer is required. >&2
        exit /b 1
    )
    python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 'Python 3.11 or newer is required.')"
    if errorlevel 1 exit /b 1
    if not exist ".venv\Scripts\python.exe" python -m venv .venv
)
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 (
    ".venv\Scripts\python.exe" -m ensurepip --upgrade
    if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --quiet -r requirements.txt
if errorlevel 1 exit /b 1

:benchmark
".venv\Scripts\python.exe" -m devmark %*
exit /b %errorlevel%
