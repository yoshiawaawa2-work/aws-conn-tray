@echo off
setlocal
chcp 65001 >nul

REM ============================================================
REM このバッチは「aws-conn-tray の GUI アンインストーラ」を起動します。
REM 文字化けを避けるため、先頭で UTF-8 を有効化しています。
REM ============================================================

REM バッチ自身のある場所を取得（末尾は "\" 付き）
set "SCRIPT_DIR=%~dp0"

REM Python 実行コマンドを後で決めるための変数
set "PY_CMD="

REM Python の標準出力/ヘルプ出力を UTF-8 に固定（日本語ヘルプでの例外回避）
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "PY_CANDIDATE1=%SCRIPT_DIR%python\python.exe"
set "PY_CANDIDATE2=%SCRIPT_DIR%python3\python.exe"
set "PY_CANDIDATE3=%SCRIPT_DIR%.venv\Scripts\python.exe"

REM CI（GitHub Actions 等）では PATH 上の python を即使用
if "%GITHUB_ACTIONS%"=="true" set "PY_CMD=python"

REM 同梱の Python（配布物内）を優先して利用
if not defined PY_CMD if exist "%PY_CANDIDATE1%" set "PY_CMD=%PY_CANDIDATE1%"
if not defined PY_CMD if exist "%PY_CANDIDATE2%" set "PY_CMD=%PY_CANDIDATE2%"
if not defined PY_CMD if exist "%PY_CANDIDATE3%" set "PY_CMD=%PY_CANDIDATE3%"

REM PATH 上の python / python3 を次に試す
if not defined PY_CMD (
  where /Q python
  if not errorlevel 1 set "PY_CMD=python"
)
if not defined PY_CMD (
  where /Q python3
  if not errorlevel 1 set "PY_CMD=python3"
)

if not defined PY_CMD (
  REM ここまでで Python が見つからない場合は GUI で確認して導入を促す
  echo Python 3.10+ was not found. Please install Python and re-run.
  REM CI では GUI を出さずにそのまま失敗（ハングを防ぐ）
  if "%GITHUB_ACTIONS%"=="true" exit /b 1
  REM GUI ダイアログで winget インストールを確認
  powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; $r=[System.Windows.MessageBox]::Show('Python was not found. Install via winget now?','Python install','YesNo','Question'); if($r -eq 'Yes'){exit 0}else{exit 1}"
  if errorlevel 1 (
    echo Please install Python 3.10+ manually and rerun.
    exit /b 1
  )
  REM winget で Python をインストール
  winget install -e --id Python.Python
  if errorlevel 1 (
    echo winget install failed. Please install Python manually.
    exit /b 1
  )
  set "PY_CMD=python"
)

REM ここから GUI アンインストーラを起動
pushd "%SCRIPT_DIR%" || (echo SCRIPT_DIR not found.& exit /b 1)
REM どの Python が選ばれたかを表示（トラブル時の手掛かり）
echo Using python: %PY_CMD%
"%PY_CMD%" "%SCRIPT_DIR%uninstaller_ui.py" %*
if errorlevel 1 (
  echo Uninstaller failed with errorlevel %errorlevel%.
  REM GUI が出ない場合は直接起動も試してください
  echo If no GUI appeared, please run manually:
  echo   %PY_CMD% "%SCRIPT_DIR%uninstaller_ui.py"
  pause
)
popd
endlocal
