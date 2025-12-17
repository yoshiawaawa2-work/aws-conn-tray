"""
Tkinter ベースのウィザード形式インストーラ。
- ステップ: ようこそ/前提確認 → インストール先/自動起動 → 設定編集 → 確認 → 進捗 → 完了
- --preview で確認のみ、--headless で GUI 無し実行が可能。
"""

from __future__ import annotations

import argparse
import threading
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from typing import Any, Dict, List, Optional

from aws_conn_tray import installer_core
from aws_conn_tray import wx_tray


# ===== ヘルパー =====


def build_config_data(forwards: List[Dict[str, str]], globals_raw: Dict[str, str]) -> Dict[str, Any]:
    """
    Tk の入力値から config.json 相当の dict を生成する。
    wx_tray.validate_settings_input を利用してバリデーションする。
    """
    data = {
        "forwards": forwards,
        "sso_start_url": globals_raw.get("sso_start_url", ""),
        "sso_login_timeout_seconds": globals_raw.get("sso_login_timeout_seconds", ""),
        "sso_auto_refresh_margin_seconds": globals_raw.get("sso_auto_refresh_margin_seconds", ""),
        "tunnel_check_interval_seconds": globals_raw.get("tunnel_check_interval_seconds", ""),
    }
    defaults = {
        "local_port": 15432,
        "remote_port": 5432,
        "sso_login_timeout_seconds": 900,
        "sso_auto_refresh_margin_seconds": 300,
        "tunnel_check_interval_seconds": 10,
    }
    ok, errors, parsed = wx_tray.validate_settings_input(data, defaults)
    if not ok:
        raise ValueError("\n".join(errors))
    return parsed


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--preview", action="store_true", help="処理を実行せずプレビューのみ")
    p.add_argument("--headless", action="store_true", help="GUI を出さずに実行")
    p.add_argument("--install-dir", default=None)
    p.add_argument("--config", default=None, help="既存 config.json のパス")
    p.add_argument("--no-autostart", action="store_true")
    p.add_argument("--auto-install-missing", action="store_true")
    return p.parse_args()


# ===== Headless 実行 =====


def perform(opts, preview: bool, config_data=None) -> installer_core.InstallResult:
    cfg_path: Optional[Path] = Path(opts.config) if opts.config else None
    install_dir = Path(opts.install_dir) if opts.install_dir else installer_core.default_paths()["install"]
    install_opts = installer_core.InstallOptions(
        install_dir=install_dir,
        config_source=cfg_path,
        use_example_config=cfg_path is None and config_data is None,
        config_data=config_data,
        autostart=not opts.no_autostart,
        auto_install_missing=opts.auto_install_missing,
        preview=preview,
    )
    return installer_core.perform_install(install_opts)


def run_headless(args):
    res = perform(args, preview=args.preview, config_data=None)
    if res.ok:
        print("プレビュー完了" if args.preview else "インストール完了")
    else:
        print("前提条件エラー:")
        for issue in res.issues:
            print(" -", issue)
    return 0 if res.ok else 1


# ===== ウィザード UI =====


class InstallerWizard:
    def __init__(self, root: tk.Tk, preview: bool = False):
        self.root = root
        self.root.title("aws-conn-tray インストーラ")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.preview = preview
        # コンテンツ領域（ナビゲーションは別フレームで下部固定）
        self.content = tk.Frame(self.root)
        self.content.pack(fill="both", expand=True)
        self.state: Dict[str, Any] = {
            "install_dir": str(installer_core.default_paths()["install"]),
            "autostart": True,
            "auto_install_missing": False,
            "forwards": [
                {
                    "name": "rds",
                    "instance_id": "",
                    "profile": "",
                    "region": "",
                    "remote_host": "",
                    "remote_port": "5432",
                    "local_port": "15432",
                }
            ],
            "globals": {
                "sso_start_url": "",
                "sso_login_timeout_seconds": "900",
                "sso_auto_refresh_margin_seconds": "300",
                "tunnel_check_interval_seconds": "10",
            },
        }
        self.frames: List[tk.Frame] = []
        self.current_step = 0
        self.progress_var = tk.DoubleVar(value=0)
        self.log_widget: Optional[tk.Text] = None
        self.result: Optional[installer_core.InstallResult] = None
        self.back_button: Optional[tk.Button] = None
        self.next_button: Optional[tk.Button] = None
        self.nav_frame: Optional[tk.Frame] = None
        self._build_steps()
        self._show_step(0)

    # --- UI ビルド ---
    def _build_steps(self):
        self.frames = [
            self._build_welcome(),
            self._build_path_autostart(),
            self._build_config_editor(),
            self._build_confirm(),
            self._build_progress(),
            self._build_done(),
        ]

    def _build_welcome(self) -> tk.Frame:
        frame = tk.Frame(self.content)
        tk.Label(frame, text="ようこそ", font=("Arial", 14, "bold")).pack(anchor="w", padx=10, pady=5)
        self.precheck_text = tk.Text(frame, height=8, width=70, state="disabled")
        self.precheck_text.pack(padx=10, pady=5)
        tk.Button(
            frame,
            text="前提不足時に推奨コマンドを自動実行",
            command=self._auto_install_suggestions,
        ).pack(anchor="w", padx=10, pady=2)
        tk.Button(frame, text="再チェック", command=self._run_precheck).pack(anchor="e", padx=10, pady=5)
        self._run_precheck()
        return frame

    def _run_precheck(self):
        res = installer_core.preinstall_check()
        self.state["precheck_ok"] = res.ok
        self.state["precheck_issues"] = res.issues
        self.state["precheck_suggested"] = res.suggested_installs
        self.precheck_text.configure(state="normal")
        self.precheck_text.delete("1.0", "end")
        if res.ok:
            self.precheck_text.insert("end", "前提条件は満たされています。\n")
        else:
            self.precheck_text.insert("end", "不足している前提条件:\n")
            for i in res.issues:
                self.precheck_text.insert("end", f"- {i}\n")
            if res.suggested_installs:
                self.precheck_text.insert("end", "\n推奨インストールコマンド例:\n")
                for cmd in res.suggested_installs:
                    self.precheck_text.insert("end", " ".join(cmd) + "\n")
        self.precheck_text.configure(state="disabled")
        # ボタン状態を更新（プレチェック未完の場合は「次へ」を無効化）
        self._update_nav_labels()

    def _build_path_autostart(self) -> tk.Frame:
        frame = tk.Frame(self.content)
        tk.Label(frame, text="インストール先", font=("Arial", 12, "bold")).grid(row=0, column=0, sticky="w", padx=5, pady=5, columnspan=2)
        self.install_var = tk.StringVar(value=self.state["install_dir"])
        tk.Entry(frame, textvariable=self.install_var, width=50).grid(row=1, column=0, padx=5, pady=5)
        tk.Button(frame, text="参照", command=self._choose_install_dir).grid(row=1, column=1, padx=5, pady=5)
        self.autostart_var = tk.BooleanVar(value=self.state["autostart"])
        tk.Checkbutton(frame, text="ログイン時に自動起動を登録する", variable=self.autostart_var).grid(row=2, column=0, columnspan=2, sticky="w", padx=5)
        return frame

    def _choose_install_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.install_var.set(path)

    def _build_config_editor(self) -> tk.Frame:
        frame = tk.Frame(self.content)
        tk.Label(frame, text="設定編集", font=("Arial", 12, "bold")).pack(anchor="w", padx=5, pady=5)
        self.forward_frames: List[Dict[str, Any]] = []
        self.forward_notebook = ttk.Notebook(frame)
        self.forward_notebook.pack(fill="both", expand=True, padx=5, pady=5)
        tk.Button(frame, text="フォワード追加", command=self._add_forward).pack(anchor="w", padx=5, pady=5)

        globals_frame = tk.LabelFrame(frame, text="グローバル設定")
        globals_frame.pack(fill="x", padx=5, pady=5)
        self.global_vars: Dict[str, tk.StringVar] = {
            "sso_start_url": tk.StringVar(value=self.state["globals"]["sso_start_url"]),
            "sso_login_timeout_seconds": tk.StringVar(value=self.state["globals"]["sso_login_timeout_seconds"]),
            "sso_auto_refresh_margin_seconds": tk.StringVar(value=self.state["globals"]["sso_auto_refresh_margin_seconds"]),
            "tunnel_check_interval_seconds": tk.StringVar(value=self.state["globals"]["tunnel_check_interval_seconds"]),
        }
        row = 0
        for key, label in [
            ("sso_start_url", "SSO Start URL"),
            ("sso_login_timeout_seconds", "SSO ログインタイムアウト(秒)"),
            ("sso_auto_refresh_margin_seconds", "自動更新マージン(秒)"),
            ("tunnel_check_interval_seconds", "監視間隔(秒)"),
        ]:
            tk.Label(globals_frame, text=label).grid(row=row, column=0, sticky="w", padx=5, pady=2)
            entry = tk.Entry(globals_frame, textvariable=self.global_vars[key], width=40)
            entry.grid(row=row, column=1, padx=5, pady=2)
            btn_frame = tk.Frame(globals_frame)
            btn_frame.grid(row=row, column=2, padx=5, pady=2)
            if key == "sso_start_url":
                tk.Button(btn_frame, text="ペースト", command=lambda v=self.global_vars[key]: v.set(self._paste_clipboard())).pack(side="left")
            if key in {"sso_login_timeout_seconds", "sso_auto_refresh_margin_seconds", "tunnel_check_interval_seconds"}:
                defaults = {
                    "sso_login_timeout_seconds": "900",
                    "sso_auto_refresh_margin_seconds": "300",
                    "tunnel_check_interval_seconds": "10",
                }
                tk.Button(btn_frame, text="デフォルト", command=lambda v=self.global_vars[key], dv=defaults[key]: v.set(dv)).pack(side="left")
            row += 1

        for fw in self.state["forwards"]:
            self._add_forward(initial=fw)

        return frame

    def _add_forward(self, initial: Optional[Dict[str, str]] = None):
        initial = initial or {
            "name": f"forward-{len(self.forward_frames)+1}",
            "instance_id": "",
            "profile": "",
            "region": "",
            "remote_host": "",
            "remote_port": "5432",
            "local_port": "15432",
        }
        tab = tk.Frame(self.forward_notebook)
        vars_map = {k: tk.StringVar(value=str(initial.get(k, ""))) for k in initial}
        labels = [
            ("name", "名前"),
            ("instance_id", "インスタンスID"),
            ("profile", "プロファイル"),
            ("region", "リージョン"),
            ("remote_host", "リモートホスト"),
            ("remote_port", "リモートポート"),
            ("local_port", "ローカルポート"),
        ]
        paste_targets = {"instance_id", "profile", "region", "remote_host"}

        for idx, (key, label) in enumerate(labels):
            tk.Label(tab, text=label).grid(row=idx, column=0, sticky="w", padx=5, pady=2)
            tk.Entry(tab, textvariable=vars_map[key], width=40).grid(row=idx, column=1, padx=5, pady=2)
            btn_box = tk.Frame(tab)
            btn_box.grid(row=idx, column=2, padx=5, pady=2, sticky="w")
            if key in paste_targets:
                tk.Button(btn_box, text="ペースト", command=lambda v=vars_map[key]: v.set(self._paste_clipboard())).pack(side="left")

        tk.Button(tab, text="削除", command=lambda t=tab, v=vars_map: self._remove_forward(t, v)).grid(row=0, column=3, padx=5, pady=2)
        self.forward_notebook.add(tab, text=initial.get("name", "新規"))

        def on_name_change(*_):
            name_val = vars_map["name"].get() or "新規"
            idx = self.forward_notebook.index(tab)
            self.forward_notebook.tab(idx, text=name_val)

        vars_map["name"].trace_add("write", on_name_change)
        self.forward_frames.append({"vars": vars_map, "tab": tab})
        # 新規タブにフォーカスを移動
        self.forward_notebook.select(tab)

    def _remove_forward(self, tab: tk.Frame, vars_map: Dict[str, tk.StringVar]):
        if len(self.forward_frames) <= 1:
            messagebox.showwarning("警告", "フォワードは最低1件必要です。")
            return
        try:
            self.forward_notebook.forget(tab)
        except Exception:
            pass
        self.forward_frames = [item for item in self.forward_frames if item["vars"] is not vars_map]

    def _build_confirm(self) -> tk.Frame:
        frame = tk.Frame(self.content)
        tk.Label(frame, text="確認", font=("Arial", 12, "bold")).pack(anchor="w", padx=5, pady=5)
        self.confirm_text = tk.Text(frame, height=15, width=70, state="disabled")
        self.confirm_text.pack(padx=5, pady=5)
        return frame

    def _update_confirm(self):
        self.confirm_text.configure(state="normal")
        self.confirm_text.delete("1.0", "end")
        self.confirm_text.insert("end", f"インストール先: {self.state['install_dir']}\n")
        self.confirm_text.insert("end", f"自動起動: {'有効' if self.state['autostart'] else '無効'}\n")
        self.confirm_text.insert("end", "フォワード設定:\n")
        for fw in self.state["forwards"]:
            self.confirm_text.insert("end", f"- {fw['name']}: {fw['local_port']} -> {fw['remote_host']}:{fw['remote_port']} ({fw['profile']}/{fw['region']})\n")
        self.confirm_text.insert("end", "グローバル設定:\n")
        for k, v in self.state["globals"].items():
            self.confirm_text.insert("end", f"  {k}: {v}\n")
        self.confirm_text.configure(state="disabled")

    def _build_progress(self) -> tk.Frame:
        frame = tk.Frame(self.content)
        tk.Label(frame, text="インストール中...", font=("Arial", 12, "bold")).pack(anchor="w", padx=5, pady=5)
        self.progressbar = ttk.Progressbar(frame, mode="indeterminate", variable=self.progress_var)
        self.progressbar.pack(fill="x", padx=5, pady=5)
        self.progress_log = tk.Text(frame, height=10, width=70, state="disabled")
        self.progress_log.pack(padx=5, pady=5)
        return frame

    def _log_progress(self, msg: str):
        self.progress_log.configure(state="normal")
        self.progress_log.insert("end", msg + "\n")
        self.progress_log.see("end")
        self.progress_log.configure(state="disabled")

    def _build_done(self) -> tk.Frame:
        frame = tk.Frame(self.content)
        self.done_label = tk.Label(frame, text="", font=("Arial", 12, "bold"))
        self.done_label.pack(anchor="w", padx=5, pady=5)
        tk.Button(frame, text="完了", command=self.root.destroy).pack(pady=10)
        return frame

    # --- ナビゲーション ---
    def _show_step(self, idx: int):
        if idx < 0 or idx >= len(self.frames):
            return
        if hasattr(self, "current_frame"):
            self.current_frame.pack_forget()
        self.current_step = idx
        self.current_frame = self.frames[idx]
        self.current_frame.pack(fill="both", expand=True)
        # 完了画面ではナビゲーションを隠す
        if self.nav_frame:
            if self.current_step == len(self.frames) - 1:
                self.nav_frame.pack_forget()
            else:
                if not self.nav_frame.winfo_manager():
                    self.nav_frame.pack(fill="x", side="bottom")
        self._update_nav_labels()

    def next_step(self):
        if self.current_step == 0 and not self.state.get("precheck_ok", False):
            messagebox.showerror("前提条件エラー", "前提条件を満たしていません。再チェックの上、解消してください。")
            return
        if self.current_step == 1:  # 保存
            self.state["install_dir"] = self.install_var.get()
            self.state["autostart"] = self.autostart_var.get()
        if self.current_step == 2:  # 設定保存 + バリデーション
            forwards = []
            for item in self.forward_frames:
                vars_map = item["vars"]
                forwards.append({k: v.get() for k, v in vars_map.items()})
            for k, var in self.global_vars.items():
                self.state["globals"][k] = var.get()
            try:
                cfg = build_config_data(forwards, self.state["globals"])
                self.state["forwards"] = cfg["forwards"]
                self.state["globals"]["sso_start_url"] = cfg["sso_start_url"]
                self.state["globals"]["sso_login_timeout_seconds"] = str(cfg["sso_login_timeout_seconds"])
                self.state["globals"]["sso_auto_refresh_margin_seconds"] = str(cfg["sso_auto_refresh_margin_seconds"])
                self.state["globals"]["tunnel_check_interval_seconds"] = str(cfg["tunnel_check_interval_seconds"])
            except Exception as e:
                messagebox.showerror("入力エラー", str(e))
                return
        target = min(self.current_step + 1, len(self.frames) - 1)
        if target == 3:
            self._update_confirm()
        if self.current_step == 3:
            self._start_install()
        self._show_step(target)

    def prev_step(self):
        self._show_step(max(self.current_step - 1, 0))

    def _paste_clipboard(self) -> str:
        try:
            return self.root.clipboard_get()
        except Exception:
            return ""

    def set_nav_buttons(self, back_btn: tk.Button, next_btn: tk.Button, nav_frame: Optional[tk.Frame] = None):
        self.back_button = back_btn
        self.next_button = next_btn
        self.nav_frame = nav_frame
        self._update_nav_labels()

    def _on_close(self):
        # 右上の×でも中止確認を出す
        if messagebox.askokcancel("確認", "本当にインストールを中止しますか？"):
            self.root.destroy()

    def _update_nav_labels(self):
        if self.back_button:
            label = "インストール中止" if self.current_step == 0 else "戻る"
            state = "normal" if self.current_step < len(self.frames) - 1 else "disabled"
            self.back_button.config(text=label, state=state)
        if self.next_button:
            if self.current_step == 3:
                self.next_button.config(text="インストール", state="normal")
            elif self.current_step >= len(self.frames) - 1:
                self.next_button.config(text="次へ", state="disabled")
            else:
                state = "normal"
                if self.current_step == 0 and not self.state.get("precheck_ok", False):
                    state = "disabled"
                self.next_button.config(text="次へ", state=state)

    # --- インストール処理 ---
    def _start_install(self):
        self.progressbar.start(10)
        self.progress_var.set(0)
        self._log_progress("インストール開始...")

        def worker():
            try:
                cfg_data = build_config_data(self.state["forwards"], self.state["globals"])
            except Exception as e:
                self._log_progress(f"設定エラー: {e}")
                messagebox.showerror("設定エラー", str(e))
                self._show_step(2)
                return
            opts = argparse.Namespace(
                install_dir=self.state["install_dir"],
                config=None,
                no_autostart=not self.state["autostart"],
                auto_install_missing=self.state.get("auto_install_missing", False),
            )
            res = perform(opts, preview=self.preview, config_data=cfg_data)
            self.result = res
            self._log_progress("インストール完了" if res.ok else "インストール失敗")
            self.done_label.config(text="インストールが完了しました！" if res.ok else "エラーが発生しました")
            self.progressbar.stop()
            self._show_step(len(self.frames) - 1)

        threading.Thread(target=worker, daemon=True).start()

    def _auto_install_suggestions(self):
        suggested = self.state.get("precheck_suggested") or []
        if self.state.get("precheck_ok", False):
            messagebox.showinfo("前提条件", "前提条件はすでに満たされています。")
            return
        if not suggested:
            messagebox.showinfo("前提条件", "実行可能な推奨コマンドはありません。")
            return
        if not messagebox.askokcancel("確認", "推奨コマンドを実行しますか？\n（自己責任で実行してください）"):
            return

        win = tk.Toplevel(self.root)
        win.title("前提条件の自動インストール")
        tk.Label(win, text="前提条件をインストールしています...").pack(anchor="w", padx=10, pady=5)
        pb = ttk.Progressbar(win, mode="indeterminate")
        pb.pack(fill="x", padx=10, pady=5)
        log = tk.Text(win, height=8, width=60, state="disabled")
        log.pack(padx=10, pady=5)

        def log_line(msg: str):
            log.configure(state="normal")
            log.insert("end", msg + "\n")
            log.see("end")
            log.configure(state="disabled")

        def worker():
            pb.start(10)
            results = installer_core.run_commands(suggested)
            for cmd, rc in results:
                log_line(f"{' '.join(cmd)} -> rc={rc}")
            pb.stop()
            win.after(500, win.destroy)
            self.root.after(600, self._run_precheck)

        threading.Thread(target=worker, daemon=True).start()


def run_gui(args):
    root = tk.Tk()
    wizard = InstallerWizard(root, preview=args.preview)
    nav = tk.Frame(root)
    nav.pack(fill="x", side="bottom")
    def on_back_or_cancel():
        if wizard.current_step == 0:
            if messagebox.askokcancel("確認", "本当にインストールを中止しますか？"):
                root.destroy()
        else:
            wizard.prev_step()

    back_btn = tk.Button(nav, text="インストール中止", command=on_back_or_cancel)
    back_btn.pack(side="left", padx=5, pady=5)
    next_btn = tk.Button(nav, text="次へ", command=wizard.next_step)
    next_btn.pack(side="right", padx=5, pady=5)
    wizard.set_nav_buttons(back_btn, next_btn, nav)
    root.mainloop()


def main():
    args = _parse_args()
    if args.headless:
        sys.exit(run_headless(args))
    run_gui(args)


if __name__ == "__main__":
    main()
