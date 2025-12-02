import configparser
import datetime
import os
import shutil
import subprocess
import sys
from typing import List, Tuple


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
LOG_DIR = os.path.join(os.path.expanduser("~"), ".cache", "aws-rds-tray")
LOG_PATH = os.path.join(LOG_DIR, "tray.log")


def log_message(message: str) -> None:
    """簡易ログを ~/.cache/aws-rds-tray/tray.log に出力する。"""
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
    if "sso_start_url" not in section_data or "sso_region" not in section_data:
        issues.append(f"[{section}] に sso_start_url / sso_region が設定されていません。`aws configure sso` を再設定してください。")

    return issues


def check_prerequisites(profile: str, region: str, instance_id: str, remote_host: str) -> Tuple[bool, List[str]]:
    """
    前提条件をチェックし、(OK/NG, 問題リスト) を返す。
    問題がある場合はアプリ/インストーラ側で通知・終了すること。
    """
    issues: List[str] = []

    # aws CLI
    aws_path = shutil.which("aws")
    if not aws_path:
        issues.append("aws CLI が見つかりません。AWS CLI v2 をインストールしてください。")
    else:
        code, out, err = _run_command([aws_path, "--version"])
        version_line = out or err
        if "aws-cli/2" not in version_line:
            issues.append(f"AWS CLI v2 ではない可能性があります: {version_line}")

    # session-manager-plugin
    smp_path = shutil.which("session-manager-plugin")
    if not smp_path:
        issues.append("session-manager-plugin が見つかりません。AWS SSM Session Manager Plugin をインストールしてください。")

    # プロファイル設定
    issues.extend(_check_profile_config(profile))

    # SSM でインスタンスを参照できるか軽く確認
    if aws_path:
        cmd = [
            aws_path,
            "ssm",
            "describe-instance-information",
            "--instance-information-filter-list",
            f"key=InstanceIds,valueSet=[{instance_id}]",
            "--profile",
            profile,
            "--region",
            region,
        ]
        code, out, err = _run_command(cmd, timeout=10)
        if code != 0:
            detail = err or out or "不明なエラー"
            issues.append(f"SSM でインスタンス {instance_id} を参照できません。権限やIDを確認してください: {detail}")

    # remote_host チェック
    if not remote_host:
        issues.append("config.json の remote_host が設定されていません。RDS のエンドポイントを指定してください。")

    return len(issues) == 0, issues
