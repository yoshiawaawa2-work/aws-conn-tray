"""
Tkinter ベースのアンインストーラ。
- ステップ: ようこそ → 確認 → 進捗 → 完了
- --preview で削除せず確認のみ、--headless で CLI 実行が可能。
"""

from __future__ import annotations

import argparse
import threading
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from typing import Any, List, Optional

from aws_conn_tray import installer_core


def resolve_uninstall_paths(install_arg: Optional[str], shortcuts_arg: Optional[str]):
    """
    インストールメタデータがあればそれを最優先で返し、なければデフォルトパス。
    メタデータが使われた場合は locked=True を返し、GUI 側で編集不可にする。
    """
    meta = installer_core.load_install_metadata()
    defaults = installer_core.default_paths()
    use_meta = meta is not None and not install_arg and not shortcuts_arg
    install_dir = (
        Path(install_arg).expanduser()
        if install_arg
        else Path(meta["install_dir"]).expanduser()
        if meta and meta.get("install_dir")
        else defaults["install"]
    )
    shortcuts_dir = (
        Path(shortcuts_arg).expanduser()
        if shortcuts_arg
        else Path(meta["shortcuts_dir"]).expanduser()
        if meta and meta.get("shortcuts_dir")
        else defaults.get("autostart_dir")
    )
    locked = bool(use_meta)
    return install_dir, shortcuts_dir, locked


# ==== CLI 共通 ====


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="aws-conn-tray アンインストーラ (GUI/CLI)")
    p.add_argument("--preview", action="store_true", help="削除せず対象のみ確認")
    p.add_argument("--headless", action="store_true", help="GUI を起動せず実行")
    p.add_argument("--install-dir", help="インストール先パス（省略時はデフォルト）")
    p.add_argument("--shortcuts-dir", help="ショートカット/自動起動用ディレクトリ（省略時はデフォルト）")
    return p.parse_args(argv)


def run_headless(args) -> int:
    install_dir, shortcuts_dir, _locked = resolve_uninstall_paths(args.install_dir, args.shortcuts_dir)
    opts = installer_core.UninstallOptions(install_dir=install_dir, shortcuts_dir=shortcuts_dir, preview=args.preview)
    res = installer_core.perform_uninstall(opts)
    if res.ok:
        print("プレビュー" if args.preview else "アンインストール完了")
        print("対象パス:")
        for t in res.targets:
            print(f"- {t}")
        if res.executed_commands:
            print("自動起動解除コマンド:")
            for cmd in res.executed_commands:
                print("  " + " ".join(cmd))
        return 0
    print("アンインストールに失敗しました:")
    for issue in res.issues:
        print("- " + issue)
    return 1


# ==== GUI ====


class UninstallWizard:
    def __init__(self, root: tk.Tk, preview: bool = False, install_arg: Optional[str] = None, shortcuts_arg: Optional[str] = None):
        self.root = root
        self.root.title("aws-conn-tray アンインストーラ")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.preview = preview

        self.content = tk.Frame(self.root)
        self.content.pack(fill="both", expand=True)
        install_dir, shortcuts_dir, locked = resolve_uninstall_paths(install_arg, shortcuts_arg)
        self.lock_paths = locked
        self.state: dict[str, Any] = {
            "install_dir": str(install_dir),
            "shortcuts_dir": str(shortcuts_dir),
        }
        self.frames: List[tk.Frame] = []
        self.current_step = 0
        self.progress_var = tk.DoubleVar(value=0)
        self.nav_frame: Optional[tk.Frame] = None
        self.back_button: Optional[tk.Button] = None
        self.next_button: Optional[tk.Button] = None
        self.confirm_text: Optional[tk.Text] = None
        self.progress_log: Optional[tk.Text] = None
        self.done_label: Optional[tk.Label] = None
        self._build_steps()
        self._show_step(0)

    def _build_steps(self):
        self.frames = [
            self._build_welcome(),
            self._build_confirm(),
            self._build_progress(),
            self._build_done(),
        ]

    def _build_welcome(self) -> tk.Frame:
        frame = tk.Frame(self.content)
        tk.Label(frame, text="アンインストール", font=("Arial", 14, "bold")).pack(anchor="w", padx=10, pady=5)
        desc = (
            "インストール先と自動起動用ディレクトリを確認し、アンインストールを実行します。\n"
            "プレビューの場合はファイル削除は行われません。"
        )
        tk.Label(frame, text=desc, justify="left").pack(anchor="w", padx=10, pady=5)

        tk.Label(frame, text="インストール先").pack(anchor="w", padx=10, pady=(5, 0))
        self.install_var = tk.StringVar(value=self.state["install_dir"])
        row1 = tk.Frame(frame)
        row1.pack(fill="x", padx=10)
        tk.Entry(row1, textvariable=self.install_var, width=50, state="readonly" if self.lock_paths else "normal").pack(side="left", fill="x", expand=True, pady=2)
        tk.Button(row1, text="参照", command=self._choose_install_dir, state="disabled" if self.lock_paths else "normal").pack(side="left", padx=5)

        tk.Label(frame, text="自動起動/ショートカット先").pack(anchor="w", padx=10, pady=(5, 0))
        self.shortcut_var = tk.StringVar(value=self.state["shortcuts_dir"])
        row2 = tk.Frame(frame)
        row2.pack(fill="x", padx=10)
        tk.Entry(row2, textvariable=self.shortcut_var, width=50, state="readonly" if self.lock_paths else "normal").pack(side="left", fill="x", expand=True, pady=2)
        tk.Button(row2, text="参照", command=self._choose_shortcut_dir, state="disabled" if self.lock_paths else "normal").pack(side="left", padx=5)

        if self.lock_paths:
            tk.Label(frame, text="記録されたインストール先を使用します（変更不可）。", fg="blue").pack(anchor="w", padx=10, pady=5)
        else:
            tk.Label(frame, text="メタデータが見つかりませんでした。デフォルトパスを初期値にしています（必要に応じて修正可）。", fg="blue").pack(anchor="w", padx=10, pady=5)
            if self.preview:
                tk.Label(frame, text="プレビューモード: 削除は行わず対象のみ表示します。", fg="blue").pack(anchor="w", padx=10, pady=2)
        return frame

    def _choose_install_dir(self):
        if self.lock_paths:
            return
        path = filedialog.askdirectory()
        if path:
            self.install_var.set(path)

    def _choose_shortcut_dir(self):
        if self.lock_paths:
            return
        path = filedialog.askdirectory()
        if path:
            self.shortcut_var.set(path)

    def _build_confirm(self) -> tk.Frame:
        frame = tk.Frame(self.content)
        tk.Label(frame, text="確認", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=5)
        self.confirm_text = tk.Text(frame, height=10, width=70, state="disabled")
        self.confirm_text.pack(padx=10, pady=5)
        return frame

    def _build_progress(self) -> tk.Frame:
        frame = tk.Frame(self.content)
        tk.Label(frame, text="アンインストール中...", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=5)
        self.progressbar = ttk.Progressbar(frame, mode="indeterminate", variable=self.progress_var)
        self.progressbar.pack(fill="x", padx=10, pady=5)
        self.progress_log = tk.Text(frame, height=10, width=70, state="disabled")
        self.progress_log.pack(padx=10, pady=5)
        return frame

    def _build_done(self) -> tk.Frame:
        frame = tk.Frame(self.content)
        self.done_label = tk.Label(frame, text="", font=("Arial", 12, "bold"))
        self.done_label.pack(anchor="w", padx=10, pady=5)
        tk.Button(frame, text="完了", command=self.root.destroy).pack(pady=10)
        return frame

    # --- ナビゲーション ---
    def set_nav_buttons(self, back_btn: tk.Button, next_btn: tk.Button, nav_frame: tk.Frame):
        self.back_button = back_btn
        self.next_button = next_btn
        self.nav_frame = nav_frame
        self._update_nav_labels()

    def _show_step(self, idx: int):
        if idx < 0 or idx >= len(self.frames):
            return
        if hasattr(self, "current_frame"):
            self.current_frame.pack_forget()
        self.current_step = idx
        self.current_frame = self.frames[idx]
        self.current_frame.pack(fill="both", expand=True)
        if self.nav_frame:
            if self.current_step == len(self.frames) - 1:
                self.nav_frame.pack_forget()
            else:
                if not self.nav_frame.winfo_manager():
                    self.nav_frame.pack(fill="x", side="bottom")
        self._update_nav_labels()

    def _update_nav_labels(self):
        if self.back_button:
            label = "アンインストール中止" if self.current_step == 0 else "戻る"
            state = "disabled" if self.current_step == len(self.frames) - 1 else "normal"
            self.back_button.config(text=label, state=state)
        if self.next_button:
            last = len(self.frames) - 1
            confirm_idx = len(self.frames) - 3  # 確認画面
            progress_idx = len(self.frames) - 2
            if self.current_step == confirm_idx:
                self.next_button.config(text="アンインストール", state="normal")
            elif self.current_step == progress_idx:
                self.next_button.config(text="処理中...", state="disabled")
            elif self.current_step == last:
                self.next_button.config(text="次へ", state="disabled")
            else:
                self.next_button.config(text="次へ", state="normal")

    def next_step(self):
        if self.current_step == 0:
            self.state["install_dir"] = self.install_var.get()
            self.state["shortcuts_dir"] = self.shortcut_var.get()
        target = min(self.current_step + 1, len(self.frames) - 1)
        if target == 1:
            self._update_confirm()
        if self.current_step == 1:
            self._start_uninstall()
        self._show_step(target)

    def prev_step(self):
        self._show_step(max(self.current_step - 1, 0))

    # --- ヘルパー ---
    def _update_confirm(self):
        if not self.confirm_text:
            return
        self.confirm_text.configure(state="normal")
        self.confirm_text.delete("1.0", "end")
        self.confirm_text.insert("end", "以下を削除します:\n")
        self.confirm_text.insert("end", f"- インストール先: {self.state['install_dir']}\n")
        self.confirm_text.insert("end", f"- 自動起動/ショートカット: {self.state['shortcuts_dir']}\n")
        if self.preview:
            self.confirm_text.insert("end", "\nプレビュー: 削除は行いません。\n")
        elif self.lock_paths:
            self.confirm_text.insert("end", "\n記録されたパスを削除します。内容をご確認ください。\n")
        else:
            self.confirm_text.insert("end", "\nメタデータ未検出のため、上記パスを手動確認してください。\n")
        self.confirm_text.configure(state="disabled")

    def _log_progress(self, msg: str):
        if not self.progress_log:
            return
        self.progress_log.configure(state="normal")
        self.progress_log.insert("end", msg + "\n")
        self.progress_log.see("end")
        self.progress_log.configure(state="disabled")

    def _start_uninstall(self):
        self.progressbar.start(10)
        self._log_progress("アンインストール開始...")

        def worker():
            opts = installer_core.UninstallOptions(
                install_dir=Path(self.state["install_dir"]).expanduser(),
                shortcuts_dir=Path(self.state["shortcuts_dir"]).expanduser(),
                preview=self.preview,
            )
            res = installer_core.perform_uninstall(opts)
            self._log_progress("完了" if res.ok else "失敗")
            if self.done_label:
                self.done_label.config(text="アンインストールが完了しました！" if res.ok else "エラーが発生しました")
            self.progressbar.stop()
            self._show_step(len(self.frames) - 1)

        threading.Thread(target=worker, daemon=True).start()

    def _on_close(self):
        if messagebox.askokcancel("確認", "本当にアンインストールを中止しますか？"):
            self.root.destroy()


def run_gui(args):
    root = tk.Tk()
    wizard = UninstallWizard(root, preview=args.preview, install_arg=args.install_dir, shortcuts_arg=args.shortcuts_dir)
    nav = tk.Frame(root)
    nav.pack(fill="x", side="bottom")

    def on_back():
        if wizard.current_step == 0:
            if messagebox.askokcancel("確認", "本当にアンインストールを中止しますか？"):
                root.destroy()
        else:
            wizard.prev_step()

    back_btn = tk.Button(nav, text="アンインストール中止", command=on_back)
    back_btn.pack(side="left", padx=5, pady=5)
    next_btn = tk.Button(nav, text="次へ", command=wizard.next_step)
    next_btn.pack(side="right", padx=5, pady=5)
    wizard.set_nav_buttons(back_btn, next_btn, nav)
    root.mainloop()


def main(argv=None):
    args = _parse_args(argv)
    if args.headless:
        sys.exit(run_headless(args))
    run_gui(args)


if __name__ == "__main__":
    main()
