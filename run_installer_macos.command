#!/bin/bash
# macOS でダブルクリック起動できるインストーララッパー
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY_CANDIDATES=(
  "${SCRIPT_DIR}/python/bin/python3"
  "${SCRIPT_DIR}/python3/bin/python3"
  "${SCRIPT_DIR}/.venv/bin/python3"
  "python3"
)

PY_CMD=""
for c in "${PY_CANDIDATES[@]}"; do
  if command -v "${c}" >/dev/null 2>&1; then
    PY_CMD="${c}"
    break
  fi
done

if [ -z "${PY_CMD}" ]; then
  choice=$(osascript -e 'display dialog "Python が見つかりません。Homebrew で python3 をインストールしますか？" buttons {"キャンセル", "OK"} default button "OK"')
  if echo "${choice}" | grep -q "OK"; then
    if command -v brew >/dev/null 2>&1; then
      brew install python || true
    else
      osascript -e 'display dialog "Homebrew が見つかりません。手動で Python をインストールしてください。" buttons {"OK"}'
      exit 1
    fi
  else
    osascript -e 'display dialog "Python 3.10+ をインストールしてから再実行してください。" buttons {"OK"}'
    exit 1
  fi
  for c in "${PY_CANDIDATES[@]}"; do
    if command -v "${c}" >/dev/null 2>&1; then
      PY_CMD="${c}"
      break
    fi
  done
fi

cd "${SCRIPT_DIR}"
exec "${PY_CMD}" "${SCRIPT_DIR}/installer_ui.py" "$@"
