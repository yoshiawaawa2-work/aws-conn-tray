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
from typing import Callable, Dict, List, Optional, Tuple

from . import platforms
from .paths import APP_ROOT, CONFIG_EXAMPLE_PATH


@dataclass
class PreinstallResult:
    ok: bool
    issues: List[str] = field(default_factory=list)
    suggested_installs: List[List[str]] = field(default_factory=list)  # 各 OS 向けの推奨コマンド列


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def preinstall_check() -> PreinstallResult:
    """
    Python/awscli/session-manager-plugin/psutil を確認し、足りない場合は推奨インストールコマンドを提示する。
    """
    issues: List[str] = []
    suggested: List[List[str]] = []

    if _which("python") is None and not sys.executable:
        issues.append("python が見つかりません。")

    if _which("aws") is None:
        issues.append("aws CLI v2 が見つかりません。")
    if _which("session-manager-plugin") is None:
        issues.append("session-manager-plugin が見つかりません。")
    try:
        import psutil  # noqa: F401
    except Exception:
        issues.append("psutil が見つかりません。")

    if issues:
        suggested = _build_install_suggestions()
    return PreinstallResult(ok=len(issues) == 0, issues=issues, suggested_installs=suggested)


def _build_install_suggestions() -> List[List[str]]:
    if platforms.is_windows():
        return [
            ["winget", "install", "--id", "Amazon.AWSCLI"],
            ["winget", "install", "--id", "Amazon.SessionManagerPlugin"],
            [sys.executable or "python", "-m", "pip", "install", "psutil"],
        ]
    if platforms.is_macos():
        return [
            ["brew", "install", "awscli"],
            ["brew", "install", "--cask", "session-manager-plugin"],
            [sys.executable or "python3", "-m", "pip", "install", "psutil"],
        ]
    # Linux (例: apt 前提。実際の環境に合わせてユーザーが変更することを想定)
    return [
        ["sudo", "apt-get", "install", "-y", "awscli", "session-manager-plugin"],
        [sys.executable or "python3", "-m", "pip", "install", "psutil"],
    ]


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


def prepare_config(target_dir: Path, source_config: Optional[Path] = None) -> Path:
    """
    config/config.json を target_dir に作成する。
    - source_config が指定されていればコピー
    - それ以外は example からコピー
    """
    cfg_dir = target_dir / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    dst = cfg_dir / "config.json"
    if source_config:
        shutil.copy2(source_config, dst)
    else:
        shutil.copy2(CONFIG_EXAMPLE_PATH, dst)
    return dst


# ===== 自動起動エントリ生成 =====


def generate_linux_autostart(install_dir: Path, shortcuts_dir: Path) -> List[Path]:
    shortcuts_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    desktop_templates = {
        "aws-conn-tray.desktop": {
            "Name": "AWS Conn Tray",
            "Exec": f"{sys.executable} {install_dir / 'tray_app.py'}",
            "Comment": "AWS SSO/SSM tray app",
        },
        "aws-conn-tray-settings.desktop": {
            "Name": "AWS Conn Tray - 設定編集",
            "Exec": f"{sys.executable} {install_dir / 'tray_app.py'} --settings",
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


def generate_windows_shortcuts(install_dir: Path, shortcuts_dir: Path) -> List[Path]:
    """
    実際の .lnk ではなく .cmd のラッパーを配置する（テスト環境で安全に扱えるように）。
    """
    shortcuts_dir.mkdir(parents=True, exist_ok=True)
    scripts = {
        "AWS Conn Tray (起動).cmd": f'@echo off\n"{sys.executable}" "{install_dir / "tray_app.py"}"\n',
        "AWS Conn Tray (設定).cmd": f'@echo off\n"{sys.executable}" "{install_dir / "tray_app.py"}" --settings\n',
        "AWS Conn Tray (自動起動解除).cmd": '@echo off\nschtasks /Delete /TN "AWS Conn Tray" /F\n',
    }
    paths: List[Path] = []
    for name, content in scripts.items():
        p = shortcuts_dir / name
        p.write_text(content, encoding="utf-8")
        paths.append(p)
    return paths


def generate_macos_shortcuts(install_dir: Path, shortcuts_dir: Path) -> List[Path]:
    shortcuts_dir.mkdir(parents=True, exist_ok=True)
    scripts = {
        "AWS Conn Tray 起動.command": f'#!/bin/bash\n"{sys.executable}" "{install_dir / "tray_app.py"}"\n',
        "AWS Conn Tray 設定.command": f'#!/bin/bash\n"{sys.executable}" "{install_dir / "tray_app.py"}" --settings\n',
        "AWS Conn Tray 自動起動解除.command": '#!/bin/bash\nlaunchctl unload ~/Library/LaunchAgents/aws.conn.tray.plist\n',
    }
    paths: List[Path] = []
    for name, content in scripts.items():
        p = shortcuts_dir / name
        p.write_text(content, encoding="utf-8")
        p.chmod(0o755)
        paths.append(p)
    return paths


def generate_autostart_entries(install_dir: Path, shortcuts_dir: Path) -> List[Path]:
    if platforms.is_windows():
        return generate_windows_shortcuts(install_dir, shortcuts_dir)
    if platforms.is_macos():
        return generate_macos_shortcuts(install_dir, shortcuts_dir)
    return generate_linux_autostart(install_dir, shortcuts_dir)


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


# ===== インストール・アンインストール スケルトン =====


def install_app(install_dir: Path, shortcuts_dir: Optional[Path] = None, config_source: Optional[Path] = None) -> List[Path]:
    """
    指定されたディレクトリにアプリをコピーし、ショートカット/自動起動用のファイルを生成する。
    """
    install_dir = install_dir.expanduser()
    install_dir.mkdir(parents=True, exist_ok=True)
    # アプリ本体をコピー（build 配布を前提に、APP_ROOT から一式コピー）
    for item in os.listdir(APP_ROOT):
        if item in {"build", "__pycache__", ".git", ".venv"}:
            continue
        src = Path(APP_ROOT) / item
        dst = install_dir / item
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

    prepare_config(install_dir, source_config=config_source)

    shortcuts_dir = shortcuts_dir or default_paths().get("autostart_dir", install_dir / "shortcuts")
    entries = generate_autostart_entries(install_dir, shortcuts_dir)
    return entries


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
