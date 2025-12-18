@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PY_CMD="

set "PY_CANDIDATE1=%SCRIPT_DIR%python\python.exe"
set "PY_CANDIDATE2=%SCRIPT_DIR%python3\python.exe"
set "PY_CANDIDATE3=%SCRIPT_DIR%.venv\Scripts\python.exe"

rem CI (GitHub Actions 等) では PATH 上の python を即使用
if "%GITHUB_ACTIONS%"=="true" set "PY_CMD=python"

rem 同梱の Python 優先
if not defined PY_CMD if exist "%PY_CANDIDATE1%" set "PY_CMD=%PY_CANDIDATE1%"
if not defined PY_CMD if exist "%PY_CANDIDATE2%" set "PY_CMD=%PY_CANDIDATE2%"
if not defined PY_CMD if exist "%PY_CANDIDATE3%" set "PY_CMD=%PY_CANDIDATE3%"

rem PATH 上の python / python3 を次に試す
if not defined PY_CMD (
  where /Q python
  if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
  where /Q python3
  if not errorlevel 1 set "PY_CMD=python3"
)

if not defined PY_CMD (
  echo Python 3.10+ was not found. Please install Python and re-run.
  exit /b 1
)

pushd "%SCRIPT_DIR%" || (echo SCRIPT_DIR not found.& exit /b 1)
"%PY_CMD%" "%SCRIPT_DIR%installer_ui.py" %*
popd
endlocal
