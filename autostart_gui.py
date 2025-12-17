"""
自動起動の有効/無効をGUIまたはCLIで切り替える簡易ツール。
- 引数なし: Tkinter GUI を表示
- --enable / --disable: CLI で実行
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tkinter as tk
from tkinter import messagebox

from aws_conn_tray import installer_core
from aws_conn_tray import platforms


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="aws-conn-tray 自動起動設定")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--enable", action="store_true")
    g.add_argument("--disable", action="store_true")
    p.add_argument("--install-dir", help="インストール先（省略時はこのスクリプトの親）")
    return p.parse_args(argv)


def _get_install_dir(install_dir: str | Path | None = None) -> Path:
    if install_dir:
        return Path(install_dir).expanduser()
    return Path(__file__).resolve().parent


def toggle_autostart(enable: bool, install_dir: str | Path | None = None):
    install_dir = _get_install_dir(install_dir)
    if enable:
        return installer_core.enable_autostart(install_dir, dry_run=False)
    return installer_core.disable_autostart(dry_run=False)


def is_autostart_enabled(install_dir: str | Path | None = None, runner=subprocess.run) -> bool:
    """
    現在の自動起動登録状態をざっくり確認する。
    失敗時は False を返す。
    """
    install_dir = _get_install_dir(install_dir)
    try:
        if platforms.is_windows():
            res = runner(
                ["schtasks", "/Query", "/TN", "AWS Conn Tray"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return getattr(res, "returncode", 1) == 0
        if platforms.is_macos():
            plist = Path.home() / "Library" / "LaunchAgents" / "aws.conn.tray.plist"
            if not plist.exists():
                return False
            res = runner(
                ["launchctl", "list", "aws.conn.tray"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return getattr(res, "returncode", 1) == 0
        # Linux
        service = Path.home() / ".config" / "systemd" / "user" / "aws-conn-tray.service"
        if not service.exists():
            return False
        res = runner(
            ["systemctl", "--user", "is-enabled", "aws-conn-tray.service"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return getattr(res, "returncode", 1) == 0
    except Exception:
        return False


def run_gui():
    root = tk.Tk()
    root.title("自動起動設定")
    tk.Label(root, text="aws-conn-tray の自動起動を切り替えます。").pack(padx=10, pady=6)
    install_dir = _get_install_dir()

    status_state = tk.StringVar()
    button_text = tk.StringVar()

    def refresh_status():
        enabled = is_autostart_enabled(install_dir)
        status_state.set("登録中" if enabled else "未登録")
        button_text.set("自動起動解除" if enabled else "自動起動登録")
        status_state_label.configure(fg="#006400" if enabled else "#666666")

    def on_enable():
        try:
            toggle_autostart(True, install_dir=install_dir)
            messagebox.showinfo("自動起動", "自動起動を有効にしました。")
        finally:
            refresh_status()

    def on_disable():
        try:
            toggle_autostart(False, install_dir=install_dir)
            messagebox.showinfo("自動起動", "自動起動を無効にしました。")
        finally:
            refresh_status()

    def on_toggle():
        if is_autostart_enabled(install_dir):
            on_disable()
        else:
            on_enable()

    status_row = tk.Frame(root)
    status_row.pack(padx=10, pady=(0, 6))
    font_main = ("TkDefaultFont", 11)
    font_bold = ("TkDefaultFont", 11, "bold")
    tk.Label(status_row, text="現在: 自動起動", font=font_main).pack(side="left", padx=(0, 6))
    status_state_label = tk.Label(status_row, textvariable=status_state, font=font_bold, fg="#006400")
    status_state_label.pack(side="left")

    btn_row = tk.Frame(root)
    btn_row.pack(pady=8)
    tk.Button(btn_row, textvariable=button_text, command=on_toggle, width=16, font=font_bold).pack(side="left", padx=5)
    tk.Button(root, text="閉じる", command=root.destroy).pack(pady=6)
    refresh_status()
    root.mainloop()


def main(argv=None):
    args = _parse_args(argv)
    if args.enable or args.disable:
        toggle_autostart(args.enable, install_dir=args.install_dir)
        return 0
    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
