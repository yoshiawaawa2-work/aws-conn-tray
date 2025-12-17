#!/bin/bash
# 配布フォルダ内でダブルクリック/実行できるインストーラ起動ラッパー

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_CANDIDATES=(
  "${SCRIPT_DIR}/python/bin/python3"
  "${SCRIPT_DIR}/python3/bin/python3"
  "${SCRIPT_DIR}/.venv/bin/python3"
  "python3"
  "python"
)

PY_CMD=""
for c in "${PY_CANDIDATES[@]}"; do
  if command -v "${c}" >/dev/null 2>&1; then
    PY_CMD="${c}"
    break
  fi
done

confirm_install() {
  local msg="$1"
  if command -v zenity >/dev/null 2>&1; then
    zenity --question --text "$msg" --width=360
    return $?
  elif command -v kdialog >/dev/null 2>&1; then
    kdialog --yesno "$msg"
    return $?
  else
    read -p "$msg [y/N] " ans
    case "$ans" in
      y|Y) return 0 ;;
      *) return 1 ;;
    esac
  fi
}

# Python が無い場合は、ユーザー確認の上で標準パッケージマネージャから導入を試みる
if [ -z "${PY_CMD}" ]; then
  if confirm_install "Python が見つかりません。OS のパッケージマネージャで python3 をインストールしますか？"; then
    if command -v apt-get >/dev/null 2>&1; then
      sudo apt-get update && sudo apt-get install -y python3
    elif command -v dnf >/dev/null 2>&1; then
      sudo dnf install -y python3
    elif command -v zypper >/dev/null 2>&1; then
      sudo zypper install -y python3
    else
      echo "対応するパッケージマネージャが見つかりません。Python を手動でインストールしてください。" >&2
    fi
  else
    echo "Python をインストールしてから再実行してください。" >&2
    read -p "Enter を押して終了します..." _
    exit 1
  fi
  for c in "${PY_CANDIDATES[@]}"; do
    if command -v "${c}" >/dev/null 2>&1; then
      PY_CMD="${c}"
      break
    fi
  done
fi

if [ -z "${PY_CMD}" ]; then
  echo "Python が見つかりません。事前に Python 3.10+ をインストールしてください。" >&2
  read -p "Enter を押して終了します..." _
  exit 1
fi

cd "${SCRIPT_DIR}"
exec "${PY_CMD}" "${SCRIPT_DIR}/installer_ui.py" "$@"
