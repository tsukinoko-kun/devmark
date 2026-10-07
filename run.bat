@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 goto python
if not exist ".venv\Scripts\python.exe" goto uv_create
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3,11,8))"
if not errorlevel 1 goto uv_install
:uv_create
uv venv --clear --python ">=3.11.8" .venv
if errorlevel 1 exit /b 1
:uv_install
uv pip install --python ".venv\Scripts\python.exe" --quiet -r requirements.txt
if errorlevel 1 exit /b 1
goto benchmark

:python
where py >nul 2>nul
if not errorlevel 1 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python 3.11.8 or newer is required. >&2
        exit /b 1
    )
    set "PYTHON=python"
)
%PYTHON% -c "import sys; sys.exit(0 if sys.version_info >= (3,11,8) else 'Python 3.11.8 or newer is required.')"
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" goto python_create
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3,11,8))"
if not errorlevel 1 goto pip_install
:python_create
%PYTHON% -m venv --clear .venv
if errorlevel 1 exit /b 1
:pip_install
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
