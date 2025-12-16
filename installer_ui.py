"""
Tkinter ベースの簡易インストーラ UI。
- --preview / --headless オプションで、実処理なしの確認や CLI 実行が可能。
"""

from __future__ import annotations

import argparse
import sys
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
from typing import Optional

from aws_conn_tray import installer_core


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--preview", action="store_true", help="処理を実行せずプレビューのみ")
    p.add_argument("--headless", action="store_true", help="GUI を出さずに実行")
    p.add_argument("--install-dir", default=None)
    p.add_argument("--config", default=None, help="既存 config.json のパス")
    p.add_argument("--no-autostart", action="store_true")
    p.add_argument("--auto-install-missing", action="store_true")
    return p.parse_args()


def perform(opts, preview: bool) -> installer_core.InstallResult:
    cfg_path: Optional[Path] = Path(opts.config) if opts.config else None
    install_dir = Path(opts.install_dir) if opts.install_dir else installer_core.default_paths()["install"]
    install_opts = installer_core.InstallOptions(
        install_dir=install_dir,
        config_source=cfg_path,
        use_example_config=cfg_path is None,
        autostart=not opts.no_autostart,
        auto_install_missing=opts.auto_install_missing,
        preview=preview,
    )
    return installer_core.perform_install(install_opts)


def run_headless(args):
    res = perform(args, preview=args.preview)
    if res.ok:
        print("プレビュー完了" if args.preview else "インストール完了")
    else:
        print("前提条件エラー:")
        for issue in res.issues:
            print(" -", issue)
    return 0 if res.ok else 1


def run_gui(args):
    defaults = installer_core.default_paths()
    root = tk.Tk()
    root.title("aws-conn-tray インストーラ")

    install_var = tk.StringVar(value=str(defaults["install"]))
    autostart_var = tk.BooleanVar(value=True)
    autostart_only_var = tk.BooleanVar(value=False)
    auto_install_missing_var = tk.BooleanVar(value=False)
    cfg_mode_var = tk.StringVar(value="sample")
    cfg_path_var = tk.StringVar(value="")

    tk.Label(root, text="インストール先").grid(row=0, column=0, sticky="w", padx=5, pady=5)
    tk.Entry(root, textvariable=install_var, width=50).grid(row=0, column=1, padx=5, pady=5)
    tk.Button(root, text="参照", command=lambda: install_var.set(filedialog.askdirectory() or install_var.get())).grid(row=0, column=2, padx=5, pady=5)

    tk.Checkbutton(root, text="ログイン時に自動起動を登録する", variable=autostart_var).grid(row=1, column=0, columnspan=3, sticky="w", padx=5)
    tk.Checkbutton(root, text="自動起動登録のみを実行", variable=autostart_only_var).grid(row=2, column=0, columnspan=3, sticky="w", padx=5)

    frame_cfg = tk.LabelFrame(root, text="設定ファイル")
    frame_cfg.grid(row=3, column=0, columnspan=3, sticky="we", padx=5, pady=5)
    tk.Radiobutton(frame_cfg, text="サンプルから生成", variable=cfg_mode_var, value="sample").grid(row=0, column=0, sticky="w")
    tk.Radiobutton(frame_cfg, text="既存 config.json を指定", variable=cfg_mode_var, value="import").grid(row=1, column=0, sticky="w")
    tk.Entry(frame_cfg, textvariable=cfg_path_var, width=45).grid(row=1, column=1, padx=5, pady=3)
    tk.Button(frame_cfg, text="参照", command=lambda: cfg_path_var.set(filedialog.askopenfilename(filetypes=[("JSON", "*.json")]) or cfg_path_var.get())).grid(row=1, column=2, padx=5)

    tk.Checkbutton(root, text="前提不足時に推奨コマンドを自動実行（自己責任）", variable=auto_install_missing_var).grid(row=4, column=0, columnspan=3, sticky="w", padx=5)

    log_text = tk.Text(root, height=10, width=70, state="disabled")
    log_text.grid(row=6, column=0, columnspan=3, padx=5, pady=5)

    def append_log(msg: str):
        log_text.configure(state="normal")
        log_text.insert("end", msg + "\n")
        log_text.see("end")
        log_text.configure(state="disabled")

    def do_install(preview: bool):
        cfg_path = cfg_path_var.get() if cfg_mode_var.get() == "import" else None
        opts = argparse.Namespace(
            install_dir=install_var.get(),
            config=cfg_path,
            no_autostart=not autostart_var.get(),
            auto_install_missing=auto_install_missing_var.get(),
        )
        if autostart_only_var.get():
            cmds = installer_core.enable_autostart(Path(install_var.get()), dry_run=preview)
            msg = "自動起動登録のみ実行しました。" if not preview else "自動起動登録コマンド（プレビュー）: " + " | ".join(" ".join(c) for c in cmds)
            messagebox.showinfo("完了", msg)
            append_log(msg)
            return
        res = perform(opts, preview=preview)
        if res.ok:
            msg = "プレビュー完了" if preview else "インストールが完了しました。"
            messagebox.showinfo("完了", msg)
            append_log(msg)
        else:
            messagebox.showerror("エラー", "\n".join(res.issues))
            append_log("\n".join(res.issues))

    def do_disable(preview: bool):
        cmds = installer_core.disable_autostart(dry_run=preview)
        msg = "自動起動解除を実行しました。" if not preview else "自動起動解除コマンド（プレビュー）: " + " | ".join(" ".join(c) for c in cmds)
        messagebox.showinfo("完了", msg)
        append_log(msg)

    tk.Button(root, text="インストール", command=lambda: do_install(preview=False)).grid(row=5, column=0, padx=5, pady=5, sticky="we")
    tk.Button(root, text="プレビュー", command=lambda: do_install(preview=True)).grid(row=5, column=1, padx=5, pady=5, sticky="we")
    tk.Button(root, text="自動起動解除", command=lambda: do_disable(preview=False)).grid(row=5, column=2, padx=5, pady=5, sticky="we")

    root.mainloop()


def main():
    args = _parse_args()
    if args.headless:
        sys.exit(run_headless(args))
    run_gui(args)


if __name__ == "__main__":
    main()
