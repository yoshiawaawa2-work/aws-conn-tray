import configparser
import datetime
import os
import shutil
import subprocess
import sys
from typing import List, Tuple

from aws_conn_tray.config_loader import ForwardTarget
from aws_conn_tray import prerequisites as prereq_mod


def get_app_root() -> str:
    """
    インストール先のルートディレクトリを返す。
    PyInstaller で固めた場合は実行ファイルのディレクトリ、
    通常の実行時はこのファイルのあるディレクトリを基準にする。
    """
    if getattr(sys, "frozen", False):
        # PyInstaller 実行バイナリ
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_ROOT = get_app_root()
CONFIG_DIR = os.path.join(APP_ROOT, "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
LOG_DIR = os.path.join(os.path.expanduser("~"), ".cache", "aws_conn_tray")
LOG_PATH = os.path.join(LOG_DIR, "tray.log")


def log_message(message: str) -> None:
    """簡易ログを ~/.cache/aws_conn_tray/tray.log に出力する。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        timestamp = datetime.datetime.now().isoformat()
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} {message}\n")
    except Exception:
        pass


def _run_command(cmd: list, timeout: int = 5) -> Tuple[int, str, str]:
    try:
        result = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        return result.returncode, (result.stdout or "").strip(), (result.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except FileNotFoundError:
        return 127, "", "not found"
    except Exception as e:
        return 1, "", str(e)


def _check_profile_config(profile: str) -> List[str]:
    issues: List[str] = []
    aws_config_path = os.path.expanduser("~/.aws/config")
    if not os.path.exists(aws_config_path):
        issues.append("~/.aws/config が見つかりません。`aws configure sso` を実行してください。")
        return issues

    parser = configparser.ConfigParser()
    parser.read(aws_config_path)
    section = f"profile {profile}" if profile and not profile.startswith("profile ") else profile
    if section not in parser:
        issues.append(f"~/.aws/config にプロファイル [{section}] が存在しません。`aws configure sso` を確認してください。")
        return issues

    section_data = parser[section]
    has_direct_sso = "sso_start_url" in section_data and "sso_region" in section_data

    # sso_session が指定されている場合は [sso-session X] を参照する
    session_name = section_data.get("sso_session")
    if session_name:
        sess_section = f"sso-session {session_name}"
        if sess_section in parser:
            sess_data = parser[sess_section]
            has_session_sso = "sso_start_url" in sess_data and "sso_region" in sess_data
            if not has_session_sso:
                issues.append(f"[{sess_section}] に sso_start_url / sso_region が設定されていません。`aws configure sso` を再設定してください。")
        else:
            issues.append(f"~/.aws/config に [{sess_section}] が存在しません。`aws configure sso` を確認してください。")
    else:
        # sso_session 指定なしの場合はプロファイル内に sso_start_url / sso_region が必要
        if not has_direct_sso:
            issues.append(f"[{section}] に sso_start_url / sso_region が設定されていません。`aws configure sso` を再設定してください。")

    return issues


def check_prerequisites(forwards: List[ForwardTarget]) -> Tuple[bool, List[str]]:
    """
    旧インストーラ用のラッパー関数。
    - 引数を新しい複数フォワード形式に変更し、内部では aws_conn_tray.prerequisites を利用する。
    """
    return prereq_mod.check_prerequisites(forwards)
