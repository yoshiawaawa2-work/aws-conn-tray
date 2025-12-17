"""
自動起動の有効/無効をGUIまたはCLIで切り替える簡易ツール。
- 引数なし: Tkinter GUI を表示
- --enable / --disable: CLI で実行
"""

from __future__ import annotations

import argparse
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from aws_conn_tray import installer_core


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


def run_gui():
    root = tk.Tk()
    root.title("自動起動設定")
    tk.Label(root, text="aws-conn-tray の自動起動を切り替えます。").pack(padx=10, pady=6)

    def on_enable():
        toggle_autostart(True)
        messagebox.showinfo("自動起動", "自動起動を有効にしました。")

    def on_disable():
        toggle_autostart(False)
        messagebox.showinfo("自動起動", "自動起動を無効にしました。")

    btn_row = tk.Frame(root)
    btn_row.pack(pady=8)
    tk.Button(btn_row, text="有効にする", command=on_enable).pack(side="left", padx=5)
    tk.Button(btn_row, text="無効にする", command=on_disable).pack(side="left", padx=5)
    tk.Button(root, text="閉じる", command=root.destroy).pack(pady=6)
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
