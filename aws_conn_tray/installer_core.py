"""
クロスプラットフォームでインストール/アンインストール/自動起動登録を行うための共通ヘルパー。
実際の UI（PySimpleGUI など）から呼び出す想定で、ここではファイル生成とコマンド組み立てを担当する。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Any

from . import platforms
from .paths import APP_ROOT, CONFIG_EXAMPLE_PATH


@dataclass
class PreinstallResult:
    ok: bool
    issues: List[str] = field(default_factory=list)
    suggested_installs: List[List[str]] = field(default_factory=list)  # 各 OS 向けの推奨コマンド列


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _windows_user_start_menu_dir() -> Path:
    """
    Windows のユーザー単位スタートメニュー配下のフォルダを返す。
    管理者権限が不要な場所を優先する。
    """
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "aws-conn-tray"


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _create_windows_shortcut(
    path: Path,
    target: str,
    arguments: str,
    working_dir: Optional[str] = None,
    description: Optional[str] = None,
) -> None:
    """
    Windows の .lnk ショートカットを作成する。
    非 Windows 環境ではテスト用のプレースホルダを書き出す。
    """
    if os.name != "nt":
        content = "\n".join(
            [
                "Shortcut placeholder",
                f"Target={target}",
                f"Arguments={arguments}",
            ]
        )
        path.write_text(content, encoding="utf-8")
        return
    cmd = [
        "$WshShell = New-Object -ComObject WScript.Shell",
        f"$Shortcut = $WshShell.CreateShortcut({_ps_quote(str(path))})",
        f"$Shortcut.TargetPath = {_ps_quote(target)}",
        f"$Shortcut.Arguments = {_ps_quote(arguments)}",
    ]
    if working_dir:
        cmd.append(f"$Shortcut.WorkingDirectory = {_ps_quote(working_dir)}")
    if description:
        cmd.append(f"$Shortcut.Description = {_ps_quote(description)}")
    cmd.append("$Shortcut.Save()")
    res = subprocess.run(["powershell", "-NoProfile", "-Command", "; ".join(cmd)], check=False)
    if getattr(res, "returncode", 1) != 0:
        raise RuntimeError("Windows ショートカットの作成に失敗しました。")


def _run_windows_elevated(cmd: List[str]) -> int:
    """
    Windows で UAC 昇格を伴うコマンドを実行する。
    Start-Process -Verb RunAs を使い、ユーザーに確認ダイアログを出す。
    """
    if not platforms.is_windows():
        return 1
    if not cmd:
        return 1
    try:
        arg_line = subprocess.list2cmdline(cmd[1:])
    except Exception:
        arg_line = " ".join(cmd[1:])
    ps = (
        "Start-Process -FilePath "
        + repr(cmd[0])
        + " -ArgumentList "
        + repr(arg_line)
        + " -Verb RunAs -Wait -PassThru | ForEach-Object { exit $_.ExitCode }"
    )
    res = subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False)
    return getattr(res, "returncode", 1)


def _windows_task_xml(install_dir: Path, py_exec: str) -> str:
    """
    Task Scheduler 用の XML を生成する。
    ログオン時に起動し、失敗時は一定回数再起動する。
    """
    user = os.environ.get("USERNAME") or "User"
    domain = os.environ.get("USERDOMAIN")
    user_id = f"{domain}\\{user}" if domain else user
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-16"?>',
            '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">',
            "  <RegistrationInfo>",
            "    <Description>AWS Conn Tray autostart</Description>",
            "  </RegistrationInfo>",
            "  <Triggers>",
            "    <LogonTrigger>",
            "      <Enabled>true</Enabled>",
            "    </LogonTrigger>",
            "  </Triggers>",
            "  <Principals>",
            '    <Principal id="Author">',
            f"      <UserId>{user_id}</UserId>",
            "      <LogonType>InteractiveToken</LogonType>",
            "      <RunLevel>HighestAvailable</RunLevel>",
            "    </Principal>",
            "  </Principals>",
            "  <Settings>",
            "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
            "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
            "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
            "    <StartWhenAvailable>true</StartWhenAvailable>",
            "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>",
            "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",
            "    <RestartOnFailure>",
            "      <Interval>PT5S</Interval>",
            "      <Count>999</Count>",
            "    </RestartOnFailure>",
            "  </Settings>",
            '  <Actions Context="Author">',
            "    <Exec>",
            f"      <Command>{py_exec}</Command>",
            f"      <Arguments>\"{install_dir / 'tray_app.py'}\"</Arguments>",
            f"      <WorkingDirectory>{install_dir}</WorkingDirectory>",
            "    </Exec>",
            "  </Actions>",
            "</Task>",
        ]
    )


def refresh_windows_path_env() -> None:
    """
    Windows の最新 PATH をレジストリから読み込み、現在プロセスの PATH に反映する。
    winget などで PATH が更新された直後でも、同一プロセス内の preinstall_check が拾えるようにする。
    """
    if not platforms.is_windows():
        return
    try:
        import winreg
    except Exception:
        return

    def _read_reg_path(root, subkey: str) -> str:
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
                return value or ""
        except Exception:
            return ""

    machine = _read_reg_path(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")
    user = _read_reg_path(winreg.HKEY_CURRENT_USER, r"Environment")
    combined = ";".join([p for p in [machine, user] if p])
    if not combined:
        return
    # %SystemRoot% などを展開
    parts = [os.path.expandvars(p) for p in combined.split(";") if p]
    os.environ["PATH"] = ";".join(parts)


def preinstall_check(sso_profile: Optional[str] = None) -> PreinstallResult:
    """
    Python/awscli/session-manager-plugin/venv を確認し、足りない場合は推奨インストールコマンドを提示する。
    sso_profile が指定されていれば、AWS config に SSO 設定があるかを確認する。
    """
    issues: List[str] = []
    suggested: List[List[str]] = []

    if _which("python") is None and not sys.executable:
        issues.append("python が見つかりません。")

    if _which("aws") is None:
        issues.append("aws CLI v2 が見つかりません。")
    if _which("session-manager-plugin") is None:
        issues.append("session-manager-plugin が見つかりません。")
    # venv が利用可能かを確認
    try:
        import venv  # noqa: F401
    except Exception:
        issues.append("Python venv モジュールが利用できません。")

    if sso_profile:
        ok_sso, sso_issue = _check_sso_profile(sso_profile)
        if not ok_sso:
            issues.append(sso_issue or f"{sso_profile}: SSO 設定が見つかりません。")

    if issues:
        suggested = _build_install_suggestions(sso_profile if sso_profile and not ok_sso else None)
    return PreinstallResult(ok=len(issues) == 0, issues=issues, suggested_installs=suggested)


def _build_install_suggestions(sso_profile: Optional[str] = None) -> List[List[str]]:
    """
    OS ごとに不足コンポーネントを導入するためのコマンド例を返す。
    sso_profile が指定された場合は最後に `aws configure sso --profile <name>` も追加。
    """
    suggestions: List[List[str]] = []
    py_win = "python"
    py_unix = "python3"
    if platforms.is_windows():
        suggestions = [
            ["winget", "install", "--id", "Amazon.AWSCLI"],
            ["winget", "install", "--id", "Amazon.SessionManagerPlugin"],
            [py_win, "-m", "pip", "install", "virtualenv"],  # venv 代替案
        ]
    elif platforms.is_macos():
        suggestions = [
            ["brew", "install", "awscli"],
            ["brew", "install", "--cask", "session-manager-plugin"],
            [py_unix, "-m", "pip", "install", "virtualenv"],
        ]
    else:
        mgr = _detect_linux_pkg_mgr()
        if mgr == "dnf":
            base = ["dnf", "install", "-y"]
        elif mgr == "zypper":
            base = ["zypper", "install", "-y"]
        else:  # default apt
            base = ["apt-get", "install", "-y"]
        suggestions = [
            [*base, "awscli", "session-manager-plugin"],
            [*base, "python3-venv"],
        ]
    if sso_profile:
        suggestions.append(["aws", "configure", "sso", "--profile", sso_profile])
    return suggestions


def _check_sso_profile(profile: str) -> Tuple[bool, str]:
    """
    ~/.aws/config を確認し、指定プロファイルの SSO 設定があるかをチェックする。
    """
    if not profile:
        return True, ""
    cfg_path = Path.home() / ".aws" / "config"
    if not cfg_path.exists():
        return False, f"{profile}: ~/.aws/config が見つかりません。"
    try:
        import configparser

        parser = configparser.RawConfigParser(interpolation=None)
        parser.read(cfg_path, encoding="utf-8")
        sessions: Dict[str, Dict[str, str]] = {}
        for section in parser.sections():
            if section.startswith("sso-session"):
                name = section.replace("sso-session ", "").strip()
                sessions[name] = {
                    "sso_start_url": parser.get(section, "sso_start_url", fallback=""),
                    "sso_region": parser.get(section, "sso_region", fallback=""),
                }
        section = "default" if profile == "default" else f"profile {profile}"
        if section not in parser:
            return False, f"{profile}: SSO 設定がありません。aws configure sso を実行してください。"
        start_url = parser.get(section, "sso_start_url", fallback="")
        sso_region = parser.get(section, "sso_region", fallback="")
        sso_session = parser.get(section, "sso_session", fallback="")
        if not start_url and sso_session:
            sess = sessions.get(sso_session)
            if sess:
                start_url = sess.get("sso_start_url", "")
                sso_region = sso_region or sess.get("sso_region", "")
        if not start_url or not sso_region:
            return False, f"{profile}: sso_start_url または sso_region が未設定です。aws configure sso を実行してください。"
        return True, ""
    except Exception as e:
        return False, f"{profile}: SSO 設定の確認に失敗しました: {e}"


def _detect_linux_pkg_mgr() -> str:
    """
    簡易的にディストロを判定し、apt/dnf/zypper のいずれかを返す。
    AWS_CONN_TRAY_OS_ID 環境変数があればそれを優先（テスト用）。
    """
    os_id = os.environ.get("AWS_CONN_TRAY_OS_ID", "").lower()
    if os_id:
        return _map_os_id_to_pkg_mgr(os_id)
    try:
        data = Path("/etc/os-release").read_text(encoding="utf-8").splitlines()
        for line in data:
            if line.startswith("ID="):
                os_id = line.split("=", 1)[1].strip().strip('"').lower()
                break
            if line.startswith("ID_LIKE="):
                os_id = line.split("=", 1)[1].strip().strip('"').lower()
    except Exception:
        os_id = ""
    return _map_os_id_to_pkg_mgr(os_id)


def _map_os_id_to_pkg_mgr(os_id: str) -> str:
    if any(key in os_id for key in ["ubuntu", "debian"]):
        return "apt"
    if any(key in os_id for key in ["fedora", "rhel", "centos", "rocky", "alma"]):
        return "dnf"
    if "suse" in os_id:
        return "zypper"
    return "apt"


def _venv_python_path(install_dir: Path) -> Path:
    """インストール先の venv にある Python パスを返す（存在確認は呼び出し側で行う）。"""
    return install_dir / ".venv" / ("Scripts" if platforms.is_windows() else "bin") / ("python.exe" if platforms.is_windows() else "python")


def run_commands(commands: List[List[str]], runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> List[Tuple[List[str], int]]:
    """
    推奨コマンド列を順に実行し、(cmd, returncode) の結果を返す。失敗しても続行。
    """
    results: List[Tuple[List[str], int]] = []
    for cmd in commands:
        try:
            res = runner(cmd, check=False)
            results.append((cmd, getattr(res, "returncode", 1)))
        except Exception:
            results.append((cmd, 1))
    return results


# ===== インストールメタデータ =====


def _metadata_file() -> Path:
    """
    インストールメタデータを記録するファイルパスを返す。
    """
    return Path.home() / ".aws-conn-tray" / "install_meta.json"


def load_install_metadata() -> Optional[Dict[str, str]]:
    path = _metadata_file()
    if not path.exists():
        return None
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return {"install_dir": data.get("install_dir"), "shortcuts_dir": data.get("shortcuts_dir")}
    except Exception:
        return None


def save_install_metadata(install_dir: Path, shortcuts_dir: Optional[Path]) -> None:
    path = _metadata_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    import json

    payload = {
        "install_dir": str(install_dir),
        "shortcuts_dir": str(shortcuts_dir) if shortcuts_dir else None,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def clear_install_metadata() -> None:
    path = _metadata_file()
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


# ===== インストール/アンインストール先パス =====


def default_paths() -> Dict[str, Path]:
    home = Path.home()
    if platforms.is_windows():
        root = home / "aws-conn-tray"
        start_menu = _windows_user_start_menu_dir()
        return {"install": root, "autostart_dir": start_menu}
    if platforms.is_macos():
        root = home / "aws-conn-tray"
        apps = Path("/Applications") / "aws-conn-tray"
        return {"install": root, "autostart_dir": apps}
    root = home / "aws-conn-tray"
    desktop_dir = home / ".local" / "share" / "applications" / "aws-conn-tray"
    return {"install": root, "autostart_dir": desktop_dir}


# ===== 設定ファイル操作 =====


def prepare_config(target_dir: Path, source_config: Optional[Path] = None, data: Optional[Dict[str, Any]] = None) -> Path:
    """
    config/config.json を target_dir に作成する。
    - source_config が指定されていればコピー
    - それ以外は example からコピー
    - data が与えられれば JSON として書き込む
    """
    cfg_dir = target_dir / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    dst = cfg_dir / "config.json"
    if data is not None:
        import json

        dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    elif source_config:
        shutil.copy2(source_config, dst)
    else:
        shutil.copy2(CONFIG_EXAMPLE_PATH, dst)
    return dst


# ===== 自動起動エントリ生成 =====


def generate_linux_autostart(install_dir: Path, shortcuts_dir: Path, python_executable: Optional[str] = None) -> List[Path]:
    shortcuts_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    py = python_executable or sys.executable or "python3"
    desktop_templates = {
        "aws-conn-tray.desktop": {
            "Name": "AWS Conn Tray",
            "Exec": f"{py} {install_dir / 'tray_app.py'}",
            "Comment": "AWS SSO/SSM tray app",
        },
        "aws-conn-tray-settings.desktop": {
            "Name": "AWS Conn Tray - 設定編集",
            "Exec": f"{py} {install_dir / 'tray_app.py'} --settings",
            "Comment": "設定ダイアログを開く",
        },
        "aws-conn-tray-disable-autostart.desktop": {
            "Name": "AWS Conn Tray - 自動起動解除",
            "Exec": f"{py} {install_dir / 'autostart_gui.py'}",
            "Comment": "自動起動を設定/解除します",
        },
    }
    for name, data in desktop_templates.items():
        path = shortcuts_dir / name
        content = "\n".join(
            [
                "[Desktop Entry]",
                "Type=Application",
                f"Name={data['Name']}",
                f"Exec={data['Exec']}",
                f"Comment={data['Comment']}",
                "Terminal=false",
            ]
        )
        path.write_text(content, encoding="utf-8")
        entries.append(path)
    return entries


def generate_windows_shortcuts(install_dir: Path, shortcuts_dir: Path, python_executable: Optional[str] = None) -> List[Path]:
    """
    Windows のスタートメニュー用 .lnk を生成する。
    非 Windows 環境ではテスト用のプレースホルダを作成する。
    """
    try:
        shortcuts_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        # 管理者権限が必要な場所だった場合は、ユーザー単位のスタートメニューへ退避
        shortcuts_dir = _windows_user_start_menu_dir()
        shortcuts_dir.mkdir(parents=True, exist_ok=True)
    py = python_executable or str(Path(sys.executable or "python").resolve())
    entries = [
        ("AWS Conn Tray (起動).lnk", f"\"{install_dir / 'tray_app.py'}\"", "AWS Conn Tray を起動"),
        ("AWS Conn Tray (設定).lnk", f"\"{install_dir / 'tray_app.py'}\" --settings", "設定ウィンドウを開く"),
        ("AWS Conn Tray (自動起動解除).lnk", f"\"{install_dir / 'autostart_gui.py'}\"", "自動起動の登録/解除"),
    ]
    paths: List[Path] = []
    for name, args, desc in entries:
        p = shortcuts_dir / name
        _create_windows_shortcut(
            p,
            py,
            args,
            working_dir=str(install_dir),
            description=desc,
        )
        paths.append(p)
    return paths


def generate_macos_shortcuts(install_dir: Path, shortcuts_dir: Path, python_executable: Optional[str] = None) -> List[Path]:
    shortcuts_dir.mkdir(parents=True, exist_ok=True)
    py = python_executable or (sys.executable or "python3")
    scripts = {
        "AWS Conn Tray 起動.command": f'#!/bin/bash\n"{py}" "{install_dir / "tray_app.py"}"\n',
        "AWS Conn Tray 設定.command": f'#!/bin/bash\n"{py}" "{install_dir / "tray_app.py"}" --settings\n',
        "AWS Conn Tray 自動起動解除.command": f'#!/bin/bash\n"{py}" "{install_dir / "autostart_gui.py"}"\n',
    }
    paths: List[Path] = []
    for name, content in scripts.items():
        p = shortcuts_dir / name
        p.write_text(content, encoding="utf-8")
        p.chmod(0o755)
        paths.append(p)
    return paths


def generate_autostart_entries(install_dir: Path, shortcuts_dir: Path, python_executable: Optional[str] = None) -> List[Path]:
    if platforms.is_windows():
        return generate_windows_shortcuts(install_dir, shortcuts_dir, python_executable=python_executable)
    if platforms.is_macos():
        return generate_macos_shortcuts(install_dir, shortcuts_dir, python_executable=python_executable)
    return generate_linux_autostart(install_dir, shortcuts_dir, python_executable=python_executable)


# ===== パッケージング =====


def create_zip(src_root: Path, build_dir: Path, zip_name: str = "aws-conn-tray-installer.zip") -> Path:
    """
    build_dir に ZIP を作成する。既存のファイルは上書き。
    - src_root が build_dir 配下にある場合でも中身を正しく含める。
      （build_dir 自体を除外したいのは、リポジトリ直下を zip にするケースのみ）
    """
    build_dir.mkdir(parents=True, exist_ok=True)
    zip_path = build_dir / zip_name
    build_dir_resolved = build_dir.resolve()
    src_root_resolved = src_root.resolve()
    src_in_build = src_root_resolved.is_relative_to(build_dir_resolved)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in src_root.rglob("*"):
            # build ディレクトリ自身は含めない（ただし src_root 配下の場合は含める）
            if build_dir_resolved in path.resolve().parents and not src_in_build:
                continue
            zf.write(path, path.relative_to(src_root))
    return zip_path


def create_build_tree(src_root: Path, build_dir: Path, dist_name: str = "aws-conn-tray") -> Path:
    """
    配布用の展開ディレクトリを build_dir に作成する。build_dir/dist_name を返す。
    """
    def _copy_with_windows_eol(src: Path, dst: Path) -> None:
        """
        Windows バッチ (.bat/.cmd) を CRLF で書き出す。
        Linux で生成した配布物でも cmd.exe が正しく解釈できるようにする。
        """
        text = src.read_text(encoding="utf-8-sig")
        # いったん LF に正規化し、CRLF で書き戻す
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        with dst.open("w", encoding="utf-8-sig", newline="\r\n") as f:
            f.write(text)

    allowed_dirs = {"aws_conn_tray", "config", "icons", "systemd", "doc"}
    allowed_files = {
        "tray_app.py",
        "app_utils.py",
        "installer.py",
        "installer_ui.py",
        "uninstaller.py",
        "uninstaller_ui.py",
        "autostart_gui.py",
        "requirements.txt",
        "README.md",
    }

    def _include_path(rel: Path) -> bool:
        if not rel.parts:
            return False
        if "__pycache__" in rel.parts:
            return False
        if rel.suffix.lower() in {".pyc", ".pyo"}:
            return False
        top = rel.parts[0]
        if top in allowed_dirs:
            if top == "doc":
                return len(rel.parts) > 1 and rel.parts[1] == "distribution_guide"
            return True
        if top in allowed_files:
            return True
        name = rel.name
        return name.startswith("run_installer_") or name.startswith("run_uninstaller_")

    build_dir.mkdir(parents=True, exist_ok=True)
    dist_dir = build_dir / dist_name
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)
    for path in src_root.rglob("*"):
        if build_dir in path.parents:
            continue
        rel = path.relative_to(src_root)
        if not _include_path(rel):
            continue
        dst = dist_dir / rel
        if path.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix.lower() in {".bat", ".cmd"}:
                _copy_with_windows_eol(path, dst)
            else:
                shutil.copy2(path, dst)
    return dist_dir


# ===== インストール・アンインストール スケルトン =====


def install_app(
    install_dir: Path,
    shortcuts_dir: Optional[Path] = None,
    config_source: Optional[Path] = None,
    config_data: Optional[Dict[str, Any]] = None,
    python_executable: Optional[str] = None,
) -> List[Path]:
    """
    指定されたディレクトリにアプリをコピーし、ショートカット/自動起動用のファイルを生成する。
    - 配布物に含めるのは実行に必要な最低限のファイルのみ（テスト・ドキュメントは除外）
    """
    install_dir = install_dir.expanduser()
    install_dir.mkdir(parents=True, exist_ok=True)
    runtime_dirs = {"aws_conn_tray", "config", "icons", "systemd"}
    runtime_files = {
        "tray_app.py",
        "app_utils.py",
        "uninstaller.py",
        "uninstaller_ui.py",
        "autostart_gui.py",
        "requirements.txt",
        "README.md",
    }

    for item in os.listdir(APP_ROOT):
        if item.startswith("."):
            continue
        src = Path(APP_ROOT) / item
        dst = install_dir / item
        if src.is_dir():
            if item in runtime_dirs:
                shutil.copytree(src, dst, dirs_exist_ok=True)
            continue
        if item in runtime_files:
            shutil.copy2(src, dst)

    prepare_config(install_dir, source_config=config_source, data=config_data)

    shortcuts_dir = shortcuts_dir or default_paths().get("autostart_dir", install_dir / "shortcuts")
    entries = generate_autostart_entries(install_dir, shortcuts_dir, python_executable=python_executable)
    return entries


def setup_venv_and_install_deps(install_dir: Path, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> Tuple[bool, List[str], List[List[str]]]:
    """
    インストール先に venv を作成し、requirements.txt をインストールする。
    返り値: (ok, issues, executed_commands)
    """
    issues: List[str] = []
    executed: List[List[str]] = []
    venv_dir = install_dir / ".venv"
    # 既存の venv がある場合は壊れた状態を避けるため一旦削除して作り直す
    if venv_dir.exists():
        try:
            shutil.rmtree(venv_dir)
        except Exception as e:
            issues.append(f"既存の仮想環境を削除できませんでした: {e}")
            return False, issues, executed
    python_cmd = sys.executable or "python3"
    cmd_venv = [python_cmd, "-m", "venv", str(venv_dir)]
    executed.append(cmd_venv)
    try:
        res = runner(cmd_venv, check=False)
        if getattr(res, "returncode", 0) != 0:
            issues.append("仮想環境の作成に失敗しました。")
            return False, issues, executed
    except Exception as e:
        issues.append(f"仮想環境の作成に失敗しました: {e}")
        return False, issues, executed

    pip_path = venv_dir / ("Scripts" if platforms.is_windows() else "bin") / ("pip.exe" if platforms.is_windows() else "pip")
    req = install_dir / "requirements.txt"
    if not req.exists():
        issues.append("requirements.txt が見つかりません。")
        return False, issues, executed
    cmd_pip = [str(pip_path), "install", "-r", str(req)]
    executed.append(cmd_pip)
    try:
        res = runner(cmd_pip, check=False)
        if getattr(res, "returncode", 0) != 0:
            issues.append("依存パッケージのインストールに失敗しました。")
            return False, issues, executed
    except Exception as e:
        issues.append(f"依存パッケージのインストールに失敗しました: {e}")
        return False, issues, executed

    return True, issues, executed


def uninstall_app(install_dir: Path, shortcuts_dir: Optional[Path] = None) -> None:
    """
    自動起動エントリを削除し、インストールディレクトリを削除する。
    """
    install_dir = install_dir.expanduser()
    if shortcuts_dir:
        shutil.rmtree(shortcuts_dir, ignore_errors=True)
    defaults = default_paths()
    for maybe in (shortcuts_dir, defaults.get("autostart_dir")):
        if maybe:
            shutil.rmtree(maybe, ignore_errors=True)
    shutil.rmtree(install_dir, ignore_errors=True)


# ===== 自動起動登録/解除 =====


def enable_autostart(install_dir: Path, dry_run: bool = False, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> List[List[str]]:
    """
    OS ごとに自動起動を登録する。
    - Windows: Task Scheduler で ONLOGON 起動（失敗時は再起動）
    - macOS: ~/Library/LaunchAgents に plist を生成し launchctl で load
    - Linux: ~/.config/systemd/user に service を生成し daemon-reload + enable --now
    いずれもインストール先の .venv/python と tray_app.py を Exec に埋め込む。
    dry_run=True ならコマンド配列だけ返す。
    """
    cmds: List[List[str]] = []
    venv_python = _venv_python_path(install_dir)
    py_exec = str(venv_python)
    executed: List[List[str]] = []
    if platforms.is_windows():
        task_xml = _windows_task_xml(install_dir, py_exec)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xml")
        try:
            tmp.write(task_xml.encode("utf-16"))
            tmp.close()
            task_cmd = ["schtasks", "/Create", "/TN", "AWS Conn Tray", "/XML", tmp.name, "/F"]
            cmds.append(task_cmd)
            if dry_run:
                return cmds
            rc = _run_windows_elevated(task_cmd)
            executed.append(["elevated"] + task_cmd)
            if rc != 0:
                executed.append(["elevated", "failed"])
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
        return executed
    elif platforms.is_macos():
        plist_dir = Path.home() / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True, exist_ok=True)
        plist = plist_dir / "aws.conn.tray.plist"
        plist.write_text(
            "\n".join(
                [
                    "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
                    "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">",
                    "<plist version=\"1.0\">",
                    "<dict>",
                    "  <key>Label</key><string>aws.conn.tray</string>",
                    "  <key>ProgramArguments</key>",
                    "  <array>",
                    f"    <string>{py_exec}</string>",
                    f"    <string>{install_dir / 'tray_app.py'}</string>",
                    "  </array>",
                    "  <key>RunAtLoad</key><true/>",
                    "  <key>KeepAlive</key>",
                    "  <dict>",
                    "    <key>SuccessfulExit</key><false/>",
                    "  </dict>",
                    "</dict>",
                    "</plist>",
                ]
            ),
            encoding="utf-8",
        )
        cmds.append(["launchctl", "load", str(plist)])
    else:
        user_systemd = Path.home() / ".config" / "systemd" / "user"
        user_systemd.mkdir(parents=True, exist_ok=True)
        service_path = user_systemd / "aws-conn-tray.service"
        content = "\n".join(
            [
                "[Unit]",
                "Description=AWS Connection Tray Application",
                "After=network.target",
                "",
                "[Service]",
                f"ExecStart={py_exec} {install_dir / 'tray_app.py'}",
                "Restart=always",
                "RestartSec=5",
                "",
                "[Install]",
                "WantedBy=default.target",
            ]
        )
        service_path.write_text(content, encoding="utf-8")
        cmds.append(["systemctl", "--user", "daemon-reload"])
        cmds.append(["systemctl", "--user", "enable", "--now", "aws-conn-tray.service"])

    if dry_run:
        return cmds
    for cmd in cmds:
        try:
            runner(cmd, check=False)
            executed.append(cmd)
        except Exception:
            executed.append(cmd)
    return executed


def disable_autostart(dry_run: bool = False, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> List[List[str]]:
    """
    OS ごとに自動起動登録を解除する。
    - Windows: schtasks /Delete
    - macOS: launchctl unload + LaunchAgent を削除
    - Linux: systemctl --user disable --now + service を削除
    dry_run=True ならコマンド配列だけ返す。
    """
    cmds: List[List[str]] = []
    cleanup_targets: List[Path] = []
    if platforms.is_windows():
        cmds.append(["schtasks", "/Delete", "/TN", "AWS Conn Tray", "/F"])
    elif platforms.is_macos():
        plist = Path.home() / "Library" / "LaunchAgents" / "aws.conn.tray.plist"
        cmds.append(["launchctl", "unload", str(plist)])
        cleanup_targets.append(plist)
    else:
        cmds.append(["systemctl", "--user", "disable", "--now", "aws-conn-tray.service"])
        cmds.append(["systemctl", "--user", "daemon-reload"])
        cleanup_targets.append(Path.home() / ".config" / "systemd" / "user" / "aws-conn-tray.service")

    executed: List[List[str]] = []
    if dry_run:
        return cmds
    for cmd in cmds:
        try:
            if platforms.is_windows() and cmd[0] == "schtasks":
                rc = _run_windows_elevated(cmd)
                executed.append(["elevated"] + cmd)
                if rc != 0:
                    executed.append(["elevated", "failed"])
            else:
                runner(cmd, check=False)
                executed.append(cmd)
        except Exception:
            executed.append(cmd)
    for path in cleanup_targets:
        try:
            if path.exists():
                path.unlink()
        except Exception:
            continue
    return executed


# ===== インストールフロー（UI/CLI 共通で利用できるようにまとめる） =====


@dataclass
class InstallOptions:
    install_dir: Path
    config_source: Optional[Path] = None
    use_example_config: bool = True
    config_data: Optional[Dict[str, Any]] = None
    autostart: bool = True
    shortcuts_dir: Optional[Path] = None
    auto_install_missing: bool = False
    preview: bool = False
    sso_profile: Optional[str] = None
    simulate_precheck_fail: bool = False


@dataclass
class InstallResult:
    ok: bool
    issues: List[str] = field(default_factory=list)
    entries: List[Path] = field(default_factory=list)
    executed_commands: List[List[str]] = field(default_factory=list)


@dataclass
class UninstallOptions:
    install_dir: Path
    shortcuts_dir: Optional[Path] = None
    preview: bool = False


@dataclass
class UninstallResult:
    ok: bool
    issues: List[str] = field(default_factory=list)
    targets: List[Path] = field(default_factory=list)
    executed_commands: List[List[str]] = field(default_factory=list)


def perform_install(opts: InstallOptions, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> InstallResult:
    """
    インストールのメインフローをまとめた関数。
    1) 前提チェック（simulate_precheck_fail で強制失敗も可）
    2) preview の場合は推奨コマンドだけ返して終了
    3) 配布ファイルを必要最小限コピー（tests/doc 等は除外）、config を生成
    4) インストール先に venv を作成し requirements を導入（失敗時はクリーンアップ）
    5) 自動起動エントリを生成・有効化（autostart が True の場合）
    6) メタデータを書き出し、結果を InstallResult で返す
    """
    issues: List[str] = []
    executed: List[List[str]] = []
    pre = preinstall_check(sso_profile=opts.sso_profile)
    if opts.simulate_precheck_fail:
        pre = PreinstallResult(ok=False, issues=["(テスト用) simulate_precheck_fail"], suggested_installs=pre.suggested_installs)
    if not pre.ok:
        if opts.auto_install_missing and pre.suggested_installs:
            run_commands(pre.suggested_installs, runner=runner)
            refresh_windows_path_env()
            pre = preinstall_check()
        if not pre.ok:
            return InstallResult(ok=False, issues=pre.issues, entries=[], executed_commands=executed)

    if opts.preview:
        return InstallResult(ok=True, issues=[], entries=[], executed_commands=pre.suggested_installs)

    venv_python = Path(opts.install_dir) / ".venv" / ("Scripts" if platforms.is_windows() else "bin") / ("python.exe" if platforms.is_windows() else "python")
    entries = install_app(
        opts.install_dir,
        shortcuts_dir=opts.shortcuts_dir,
        config_source=None if opts.use_example_config else opts.config_source,
        config_data=opts.config_data if not opts.use_example_config else None,
        python_executable=str(venv_python),
    )
    ok_venv, venv_issues, venv_cmds = setup_venv_and_install_deps(opts.install_dir, runner=runner)
    executed.extend(venv_cmds)
    if venv_issues:
        issues.extend(venv_issues)
        # 依存導入に失敗した場合はクリーンアップして終了
        try:
            uninstall_app(opts.install_dir, shortcuts_dir=opts.shortcuts_dir or default_paths().get("autostart_dir"))
            clear_install_metadata()
        except Exception:
            pass
        return InstallResult(ok=False, issues=issues, entries=entries, executed_commands=executed)
    if opts.autostart:
        executed.extend(enable_autostart(opts.install_dir, dry_run=False, runner=runner))
    # インストール完了後にメタデータを記録
    try:
        save_install_metadata(opts.install_dir, opts.shortcuts_dir or default_paths().get("autostart_dir"))
    except Exception:
        pass
    return InstallResult(ok=True, issues=issues, entries=entries, executed_commands=executed)


def perform_uninstall(opts: UninstallOptions, runner: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> UninstallResult:
    """
    自動起動解除 → ファイル削除を行う。preview=True の場合は削除せず対象のみ返す。
    - install_dir/shortcuts_dir はメタデータ・デフォルトを考慮して呼び出し元で決定する
    - preview ならコマンド実行も行わず、削除予定パスとコマンドを返すだけ
    """
    install_dir = opts.install_dir.expanduser()
    shortcuts_dir = opts.shortcuts_dir or default_paths().get("autostart_dir")
    targets: List[Path] = []
    if shortcuts_dir:
        targets.append(shortcuts_dir)
    targets.append(install_dir)

    executed = disable_autostart(dry_run=opts.preview, runner=runner)
    if opts.preview:
        return UninstallResult(ok=True, issues=[], targets=targets, executed_commands=executed)

    uninstall_app(install_dir, shortcuts_dir=shortcuts_dir)
    try:
        meta = load_install_metadata()
        if meta:
            meta_install = Path(meta.get("install_dir", "")).expanduser()
            if meta_install == install_dir:
                clear_install_metadata()
    except Exception:
        pass
    return UninstallResult(ok=True, issues=[], targets=targets, executed_commands=executed)
