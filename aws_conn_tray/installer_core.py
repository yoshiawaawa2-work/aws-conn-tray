"""
クロスプラットフォームでインストール/アンインストール/自動起動登録を行うための共通ヘルパー。
実際の UI（PySimpleGUI など）から呼び出す想定で、ここではファイル生成とコマンド組み立てを担当する。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
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
    py_win = "python"
    py_unix = "python3"
    if platforms.is_windows():
        return [
            ["winget", "install", "--id", "Amazon.AWSCLI"],
            ["winget", "install", "--id", "Amazon.SessionManagerPlugin"],
            [py_win, "-m", "pip", "install", "virtualenv"],  # venv 代替案
        ]
    if platforms.is_macos():
        return [
            ["brew", "install", "awscli"],
            ["brew", "install", "--cask", "session-manager-plugin"],
            [py_unix, "-m", "pip", "install", "virtualenv"],
        ]
    mgr = _detect_linux_pkg_mgr()
    if mgr == "dnf":
        base = ["dnf", "install", "-y"]
    elif mgr == "zypper":
        base = ["zypper", "install", "-y"]
    else:  # default apt
        base = ["apt-get", "install", "-y"]
    return [
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
        root = Path(os.environ.get("SystemDrive", "C:")) / "aws-conn-tray"
        start_menu = Path(os.environ.get("ProgramData", home / "AppData" / "Roaming")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "aws-conn-tray"
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
            "Exec": "systemctl --user disable --now aws-conn-tray.service",
            "Comment": "自動起動を無効化します",
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
    実際の .lnk ではなく .cmd のラッパーを配置する（テスト環境で安全に扱えるように）。
    """
    shortcuts_dir.mkdir(parents=True, exist_ok=True)
    py = python_executable or str(Path(sys.executable or "python").resolve())
    scripts = {
        "AWS Conn Tray (起動).cmd": f'@echo off\n"{py}" "{install_dir / "tray_app.py"}"\n',
        "AWS Conn Tray (設定).cmd": f'@echo off\n"{py}" "{install_dir / "tray_app.py"}" --settings\n',
        "AWS Conn Tray (自動起動解除).cmd": '@echo off\nschtasks /Delete /TN "AWS Conn Tray" /F\n',
    }
    paths: List[Path] = []
    for name, content in scripts.items():
        p = shortcuts_dir / name
        p.write_text(content, encoding="utf-8")
        paths.append(p)
    return paths


def generate_macos_shortcuts(install_dir: Path, shortcuts_dir: Path, python_executable: Optional[str] = None) -> List[Path]:
    shortcuts_dir.mkdir(parents=True, exist_ok=True)
    py = python_executable or (sys.executable or "python3")
    scripts = {
        "AWS Conn Tray 起動.command": f'#!/bin/bash\n"{py}" "{install_dir / "tray_app.py"}"\n',
        "AWS Conn Tray 設定.command": f'#!/bin/bash\n"{py}" "{install_dir / "tray_app.py"}" --settings\n',
        "AWS Conn Tray 自動起動解除.command": '#!/bin/bash\nlaunchctl unload ~/Library/LaunchAgents/aws.conn.tray.plist\n',
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
    """
    build_dir.mkdir(parents=True, exist_ok=True)
    zip_path = build_dir / zip_name
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in src_root.rglob("*"):
            # build ディレクトリ自身は含めない
            if build_dir in path.parents:
                continue
            zf.write(path, path.relative_to(src_root))
    return zip_path


def create_build_tree(src_root: Path, build_dir: Path, dist_name: str = "aws-conn-tray") -> Path:
    """
    配布用の展開ディレクトリを build_dir に作成する。build_dir/dist_name を返す。
    """
    build_dir.mkdir(parents=True, exist_ok=True)
    dist_dir = build_dir / dist_name
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
    dist_dir.mkdir(parents=True, exist_ok=True)
    for path in src_root.rglob("*"):
        if build_dir in path.parents:
            continue
        rel = path.relative_to(src_root)
        dst = dist_dir / rel
        if path.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
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
    runtime_files = {"tray_app.py", "app_utils.py", "uninstaller.py", "uninstaller_ui.py", "requirements.txt", "README.md"}

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
    cmds: List[List[str]] = []
    venv_python = install_dir / ".venv" / ("Scripts" if platforms.is_windows() else "bin") / ("python.exe" if platforms.is_windows() else "python")
    py_exec = str(venv_python) if venv_python.exists() else str(Path(sys.executable or "python").resolve())
    if platforms.is_windows():
        task_cmd = [
            "schtasks",
            "/Create",
            "/TN",
            "AWS Conn Tray",
            "/SC",
            "ONLOGON",
            "/TR",
            f'"{py_exec}" "{install_dir / "tray_app.py"}"',
        ]
        cmds.append(task_cmd)
    elif platforms.is_macos():
        plist = Path.home() / "Library" / "LaunchAgents" / "aws.conn.tray.plist"
        cmds.append(["launchctl", "load", str(plist)])
    else:
        cmds.append(["systemctl", "--user", "enable", "--now", "aws-conn-tray.service"])

    executed: List[List[str]] = []
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
    cmds: List[List[str]] = []
    if platforms.is_windows():
        cmds.append(["schtasks", "/Delete", "/TN", "AWS Conn Tray", "/F"])
    elif platforms.is_macos():
        plist = Path.home() / "Library" / "LaunchAgents" / "aws.conn.tray.plist"
        cmds.append(["launchctl", "unload", str(plist)])
    else:
        cmds.append(["systemctl", "--user", "disable", "--now", "aws-conn-tray.service"])

    executed: List[List[str]] = []
    if dry_run:
        return cmds
    for cmd in cmds:
        try:
            runner(cmd, check=False)
            executed.append(cmd)
        except Exception:
            executed.append(cmd)
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
    issues: List[str] = []
    executed: List[List[str]] = []
    pre = preinstall_check(sso_profile=opts.sso_profile)
    if opts.simulate_precheck_fail:
        pre = PreinstallResult(ok=False, issues=["(テスト用) simulate_precheck_fail"], suggested_installs=pre.suggested_installs)
    if not pre.ok:
        if opts.auto_install_missing and pre.suggested_installs:
            run_commands(pre.suggested_installs, runner=runner)
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
