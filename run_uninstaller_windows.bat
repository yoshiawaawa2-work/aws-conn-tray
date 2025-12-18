@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
set "PY_CMD="

set "PY_CANDIDATE1=%SCRIPT_DIR%python\python.exe"
set "PY_CANDIDATE2=%SCRIPT_DIR%python3\python.exe"
set "PY_CANDIDATE3=%SCRIPT_DIR%.venv\Scripts\python.exe"
rem CI (GitHub Actions 等) の場合は PATH 上の python を優先して即決する
if "%GITHUB_ACTIONS%"=="true" (
  set "PY_CMD=python"
)

rem PATH 上の python / python3 が見つかればそれを優先
if not defined PY_CMD (
  where /Q python
  if "%errorlevel%"=="0" set "PY_CMD=python"
)
if not defined PY_CMD (
  where /Q python3
  if "%errorlevel%"=="0" set "PY_CMD=python3"
)

if not defined PY_CMD (
  if exist "%PY_CANDIDATE1%" (
    set "PY_CMD=%PY_CANDIDATE1%"
  ) else if exist "%PY_CANDIDATE2%" (
    set "PY_CMD=%PY_CANDIDATE2%"
  ) else if exist "%PY_CANDIDATE3%" (
    set "PY_CMD=%PY_CANDIDATE3%"
  ) else (
    powershell -NoProfile -Command ^
      "Add-Type -AssemblyName PresentationFramework; " ^
      "$r = [System.Windows.MessageBox]::Show('Python が見つかりません。winget で Python 3 をインストールしますか？', 'Python インストール', 'YesNo', 'Question'); " ^
      "if ($r -eq 'Yes') { exit 0 } else { exit 1 }"
    if %errorlevel%==0 (
      winget install -e --id Python.Python
      if %errorlevel%==0 (
        set "PY_CMD=python"
      ) else (
        echo インストールに失敗しました。手動で Python 3.10+ を導入してください。
        pause
        exit /b 1
      )
    ) else (
      echo Python をインストールしてから再実行してください。
      pause
      exit /b 1
    )
  )
)

if not defined PY_CMD (
  set "PY_CMD=python"
)

pushd "%SCRIPT_DIR%" || (echo SCRIPT_DIR が見つかりません。& exit /b 1)
"%PY_CMD%" "%SCRIPT_DIR%uninstaller_ui.py" %*
popd
endlocal
