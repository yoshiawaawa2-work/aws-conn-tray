import json
import os
import shutil
import sys
from typing import Dict, Tuple

import PySimpleGUI as sg

from app_utils import check_prerequisites, log_message


SUPPORTED_PLATFORMS = ("linux", "linux2", "win32")


def ensure_supported_os():
    if sys.platform not in SUPPORTED_PLATFORMS:
        sg.popup_error("OSが対応していません。Linux または Windows で実行してください。", keep_on_top=True)
        sys.exit(1)


def default_install_dir() -> str:
    if sys.platform.startswith("win"):
        drive = os.environ.get("SystemDrive", "C:")
        return os.path.join(drive, "aws-rds-tray")
    return os.path.expanduser("~/aws-rds-tray")


def build_layout(default_dir: str) -> list:
    return [
        [sg.Text("インストール先"), sg.Input(default_dir, key="install_dir", size=(50, 1)), sg.FolderBrowse("参照")],
        [sg.Text("踏み台EC2インスタンスID"), sg.Input(key="instance_id", size=(50, 1))],
        [sg.Text("AWSプロファイル"), sg.Input(key="profile", size=(30, 1))],
        [sg.Text("リージョン"), sg.Input("ap-northeast-1", key="region", size=(20, 1))],
        [sg.Text("ローカルポート"), sg.Input("15432", key="local_port", size=(10, 1))],
        [sg.Text("RDSエンドポイント"), sg.Input(key="remote_host", size=(50, 1))],
        [sg.Text("RDSポート"), sg.Input("5432", key="remote_port", size=(10, 1))],
        [sg.Text("SSOスタートURL"), sg.Input(key="sso_start_url", size=(50, 1))],
        [sg.Text("SSOログインタイムアウト(秒)"), sg.Input("900", key="sso_login_timeout_seconds", size=(10, 1))],
        [sg.Button("インストール", bind_return_key=True), sg.Button("キャンセル")],
    ]


def gather_values(values: Dict[str, str]) -> Tuple[bool, Dict[str, str]]:
    required_fields = ["install_dir", "instance_id", "profile", "region", "remote_host", "sso_start_url"]
    missing = [f for f in required_fields if not values.get(f)]
    if missing:
        sg.popup_error(f"未入力の項目があります: {', '.join(missing)}", keep_on_top=True)
        return False, {}
    return True, {
        "install_dir": values["install_dir"],
        "instance_id": values["instance_id"],
        "profile": values["profile"],
        "region": values["region"],
        "local_port": int(values.get("local_port") or 15432),
        "remote_host": values["remote_host"],
        "remote_port": int(values.get("remote_port") or 5432),
        "sso_start_url": values["sso_start_url"],
        "sso_login_timeout_seconds": int(values.get("sso_login_timeout_seconds") or 900),
    }


def write_config(install_dir: str, config_data: Dict[str, str]) -> None:
    config_dir = os.path.join(install_dir, "config")
    os.makedirs(config_dir, exist_ok=True)
    config_path = os.path.join(config_dir, "config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)
    log_message(f"config.json created at {config_path}")


def copy_app_files(src_root: str, dest_root: str) -> None:
    def _ignore(dirpath, contents):
        ignore_list = {".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".gitignore"}
        return ignore_list.intersection(contents)

    os.makedirs(dest_root, exist_ok=True)
    for item in os.listdir(src_root):
        if item in {".git", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache"}:
            continue
        src_path = os.path.join(src_root, item)
        dest_path = os.path.join(dest_root, item)
        if os.path.isdir(src_path):
            shutil.copytree(src_path, dest_path, dirs_exist_ok=True, ignore=_ignore)
        else:
            shutil.copy2(src_path, dest_path)


def main():
    ensure_supported_os()

    src_root = os.path.dirname(os.path.abspath(__file__))
    layout = build_layout(default_install_dir())
    window = sg.Window("aws-rds-tray インストーラ", layout)

    while True:
        event, values = window.read()
        if event in (sg.WINDOW_CLOSED, "キャンセル"):
            window.close()
            sys.exit(0)
        if event == "インストール":
            ok, data = gather_values(values)
            if not ok:
                continue

            install_dir = data.pop("install_dir")

            # 前提条件チェック
            ok_pre, issues = check_prerequisites(
                profile=data["profile"],
                region=data["region"],
                instance_id=data["instance_id"],
                remote_host=data["remote_host"],
            )
            if not ok_pre:
                sg.popup_error("前提条件エラー:\n" + "\n".join(issues), keep_on_top=True)
                log_message("Installer prerequisites failed: " + "; ".join(issues))
                window.close()
                sys.exit(1)

            try:
                copy_app_files(src_root, install_dir)
                write_config(install_dir, data)
            except Exception as e:
                sg.popup_error(f"インストールに失敗しました: {e}", keep_on_top=True)
                log_message(f"Install failed: {e}")
                window.close()
                sys.exit(1)

            sg.popup_ok(f"インストールが完了しました。\nインストール先: {install_dir}", keep_on_top=True)
            window.close()
            sys.exit(0)


if __name__ == "__main__":
    main()
