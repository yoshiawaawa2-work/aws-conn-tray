import configparser
import os
import shutil
import subprocess
from typing import List, Tuple

from .logger import notify, log_message


def check_profile_config(profile: str) -> List[str]:
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
    session_name = section_data.get("sso_session")
    if session_name:
        sess_section = f"sso-session {session_name}"
        if sess_section in parser:
            sess_data = parser[sess_section]
            if "sso_start_url" not in sess_data or "sso_region" not in sess_data:
                issues.append(f"[{sess_section}] に sso_start_url / sso_region が設定されていません。`aws configure sso` を再設定してください。")
        else:
            issues.append(f"~/.aws/config に [{sess_section}] が存在しません。`aws configure sso` を確認してください。")
    else:
        if not has_direct_sso:
            issues.append(f"[{section}] に sso_start_url / sso_region が設定されていません。`aws configure sso` を再設定してください。")

    return issues


def check_prerequisites(
    profile: str,
    region: str,
    instance_id: str,
    remote_host: str,
    require_ssm: bool = True,
) -> Tuple[bool, List[str]]:
    issues: List[str] = []

    aws_path = shutil.which("aws")
    if not aws_path:
        issues.append("aws CLI が見つかりません。AWS CLI v2 をインストールしてください。")
    else:
        code, out, err = _run_command([aws_path, "--version"])
        version_line = out or err
        if "aws-cli/2" not in version_line:
            issues.append(f"AWS CLI v2 ではない可能性があります: {version_line}")

    smp_path = shutil.which("session-manager-plugin")
    if not smp_path:
        issues.append("session-manager-plugin が見つかりません。AWS SSM Session Manager Plugin をインストールしてください。")

    issues.extend(check_profile_config(profile))

    if aws_path and require_ssm:
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

    if not remote_host:
        issues.append("config.json の remote_host が設定されていません。RDS のエンドポイントを指定してください。")

    return len(issues) == 0, issues


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


def ensure_supported_os():
    import sys

    if sys.platform not in ("linux", "linux2", "win32", "darwin"):
        notify("非対応OS", "このアプリは Linux / Windows / macOS に対応しています。")
        raise SystemExit(1)
