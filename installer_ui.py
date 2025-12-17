"""
Tkinter ベースのウィザード形式インストーラ。
- ステップ: ようこそ/前提確認 → インストール先/自動起動 → 設定編集 → 確認 → 進捗 → 完了
- --preview で確認のみ、--headless で GUI 無し実行が可能。
"""

from __future__ import annotations

import argparse
import threading
import sys
import os
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from typing import Any, Dict, List, Optional

import shutil

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
    p.add_argument("--sso-profile", default=None, help="SSO プロファイル名")
    p.add_argument("--simulate-precheck-fail", action="store_true", help="テスト用: 前提チェックを強制失敗させる")
    p.add_argument("--allow-unsupported-os", action="store_true", help="未対応OSでも起動を許可（テスト用）")
    p.add_argument("--simulate-unsupported-os", action="store_true", help="未対応OSをシミュレートして終了動作を確認（テスト用）")
    return p.parse_args()


def _get_sso_profiles(config_path: Optional[Path] = None) -> List[str]:
    """
    ~/.aws/config (もしくは AWS_CONFIG_FILE) から SSO 設定を持つプロファイル一覧を返す。
    """
    profiles: List[str] = []
    cfg_path = Path(config_path) if config_path else Path(os.environ.get("AWS_CONFIG_FILE", Path.home() / ".aws" / "config"))
    if not cfg_path.exists():
        return profiles
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
        for section in parser.sections():
            if section == "default":
                prof = "default"
            elif section.startswith("profile "):
                prof = section.replace("profile ", "", 1)
            else:
                continue
            start_url = parser.get(section, "sso_start_url", fallback="")
            sso_region = parser.get(section, "sso_region", fallback="")
            sso_session = parser.get(section, "sso_session", fallback="")
            if not start_url and sso_session:
                sess = sessions.get(sso_session)
                if sess:
                    start_url = sess.get("sso_start_url", "")
                    sso_region = sso_region or sess.get("sso_region", "")
            if start_url and sso_region:
                profiles.append(prof)
    except Exception:
        pass
    return profiles


def _load_sso_profile_config(profile: str, config_path: Optional[Path] = None) -> Dict[str, str]:
    """
    プロファイルの SSO 設定を取得する。sso_session 経由の場合もセッションを解決して返す。
    戻り値: {"sso_start_url":..., "sso_region":..., "sso_account_id":..., "sso_role_name":..., "sso_session":...}
    """
    cfg_path = Path(config_path) if config_path else Path(os.environ.get("AWS_CONFIG_FILE", Path.home() / ".aws" / "config"))
    if not cfg_path.exists() or not profile:
        return {}
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
            return {}
        sso_session = parser.get(section, "sso_session", fallback="")
        start_url = parser.get(section, "sso_start_url", fallback="")
        sso_region = parser.get(section, "sso_region", fallback="")
        if not start_url and sso_session:
            sess = sessions.get(sso_session)
            if sess:
                start_url = sess.get("sso_start_url", "")
                sso_region = sso_region or sess.get("sso_region", "")
        return {
            "sso_start_url": start_url,
            "sso_region": sso_region,
            "sso_account_id": parser.get(section, "sso_account_id", fallback=""),
            "sso_role_name": parser.get(section, "sso_role_name", fallback=""),
            "sso_session": sso_session,
        }
    except Exception:
        return {}


def _suggest_new_profile_name(existing: List[str]) -> str:
    """
    既存プロファイル一覧を元に、新規作成時のデフォルト名を返す。
    default が空いていれば default、それ以外は profile-1,2,... を採番。
    """
    if "default" not in existing:
        return "default"
    i = 1
    while True:
        cand = f"profile-{i}"
        if cand not in existing:
            return cand
        i += 1


def _sso_required_missing(values: Dict[str, str]) -> List[str]:
    required = {
        "profile": "プロファイル名",
        "sso_start_url": "SSO Start URL",
        "sso_region": "SSO リージョン",
        "sso_account_id": "SSO アカウントID",
        "sso_role_name": "SSO ロール名",
    }
    missing = []
    for key, label in required.items():
        if not values.get(key, "").strip():
            missing.append(label)
    return missing


def _apply_sso_to_ui(forward_frames: List[Any], global_vars: Dict[str, Any], profile: str, sso_region: str, start_url: str):
    """
    forward_frames/global_vars を直接更新するユーティリティ（テスト容易性のため切り出し）。
    profile/region/start_url を UI バインド変数に反映する。
    """
    if global_vars and "sso_start_url" in global_vars and start_url:
        try:
            global_vars["sso_start_url"].set(start_url)
        except Exception:
            pass
    for ff in forward_frames or []:
        vars_map = ff
        if isinstance(ff, dict) and "vars" in ff:
            vars_map = ff["vars"]
        if not isinstance(vars_map, dict):
            continue
        try:
            if "profile" in vars_map:
                vars_map["profile"].set(profile)
            if sso_region and "region" in vars_map:
                vars_map["region"].set(sso_region)
        except Exception:
            continue

def _ensure_supported_os(args) -> bool:
    if getattr(args, "simulate_unsupported_os", False):
        pass_supported = False
    else:
        pass_supported = installer_core.platforms.is_windows() or installer_core.platforms.is_macos() or installer_core.platforms.is_linux()
    if pass_supported:
        return True
    if getattr(args, "allow_unsupported_os", False):
        return True
    # GUI と headless でメッセージ出力を切り替え
    try:
        messagebox.showerror("未対応OS", "未対応OSです。インストーラを終了します。")
    except Exception:
        pass
    print("未対応OSです。インストーラを終了します。")
    return False


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
        sso_profile=getattr(opts, "sso_profile", None),
        simulate_precheck_fail=getattr(opts, "simulate_precheck_fail", False),
    )
    return installer_core.perform_install(install_opts)


def run_headless(args):
    if not _ensure_supported_os(args):
        return 1
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
    """
    Tk ベースのフル機能インストーラウィザード。
    - ステップ: ようこそ/前提チェック → インストール先/自動起動 → 設定編集 → 確認 → 進捗 → 完了
    - preview=True でファイルを書かずに動作確認、simulate_precheck_fail で前提不足をテスト可能
    - SSO プロファイル選択・ログイン連携、設定保存、本体再起動などを統合
    """
    def __init__(self, root: tk.Tk, preview: bool = False, simulate_precheck_fail: bool = False, sso_profile: Optional[str] = None):
        self.root = root
        self.root.title("aws-conn-tray インストーラ")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.preview = preview
        self.simulate_precheck_fail = simulate_precheck_fail
        # コンテンツ領域（ナビゲーションは別フレームで下部固定）
        self.content = tk.Frame(self.root)
        self.content.pack(fill="both", expand=True)
        self.state: Dict[str, Any] = {
            "install_dir": str(installer_core.default_paths()["install"]),
            "autostart": True,
            "auto_install_missing": False,
            "sso_profile": sso_profile or "",
            "sso_login_ok": False,
            "aws_ok": True,
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
        self.sso_profiles: List[str] = _get_sso_profiles()
        self.sso_status_var: Optional[tk.StringVar] = None
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
        sso_row = tk.Frame(frame)
        sso_row.pack(fill="x", padx=10, pady=2)
        self.sso_status_var = tk.StringVar(value="SSO ログインが完了していない場合、次へ進めません。")
        self.sso_status_label = tk.Label(sso_row, textvariable=self.sso_status_var, fg="red")
        self.sso_status_label.pack(side="right", padx=5)
        tk.Label(sso_row, text="SSOプロファイル").pack(side="left")
        self.sso_profile_var = tk.StringVar(value=self.state.get("sso_profile", ""))
        self.sso_profile_cb = ttk.Combobox(sso_row, textvariable=self.sso_profile_var, values=self.sso_profiles, width=18)
        self.sso_profile_cb.pack(side="left", padx=5)
        self.sso_button = tk.Button(sso_row, text="SSO設定", command=self._open_sso_config_dialog)
        self.sso_button.pack(side="left")
        # プロファイル変更時に状態とUIを更新
        self.sso_profile_var.trace_add("write", lambda *_: self._on_profile_change())
        self.sso_hint_var = tk.StringVar(value="※ SSO設定画面で「ログイン」に成功してから続行してください。")
        self.sso_hint_label = tk.Label(frame, textvariable=self.sso_hint_var, fg="red")
        self.sso_hint_label.pack(anchor="w", padx=10, pady=(0, 6))
        self._run_precheck()
        return frame

    def _run_precheck(self):
        res = installer_core.preinstall_check(sso_profile=self.sso_profile_var.get().strip() or None)
        if self.simulate_precheck_fail:
            # テスト用: 強制的に失敗させ、推奨コマンドも埋める
            suggested = res.suggested_installs or installer_core._build_install_suggestions()  # type: ignore[attr-defined]
            res = installer_core.PreinstallResult(ok=False, issues=["(テスト用) simulate_precheck_fail"], suggested_installs=suggested)
        self.state["precheck_ok"] = res.ok
        self.state["precheck_issues"] = res.issues
        self.state["precheck_suggested"] = res.suggested_installs
        self.state["sso_profile"] = self.sso_profile_var.get().strip()
        self.state["aws_ok"] = not any("aws CLI v2" in issue for issue in res.issues)
        # SSO ログイン成功状態をリセット（設定変更時は再ログインが必要）
        if not res.ok:
            self.state["sso_login_ok"] = False
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
        if hasattr(self, "sso_button"):
            state = "normal" if self.state.get("aws_ok", False) else "disabled"
            self.sso_button.config(state=state)
            if hasattr(self, "sso_profile_cb"):
                cb_state = "readonly"
                if not self.state.get("aws_ok", False):
                    cb_state = "disabled"
                self.sso_profile_cb.config(state=cb_state, values=self.sso_profiles)
        self._update_sso_status_label()

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
        if self.current_step == 0 and self.state.get("sso_profile") and not self.state.get("sso_login_ok"):
            messagebox.showerror("SSO 未完了", "SSO 設定/ログインを実行し、成功させてください。")
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
                # SSO ログイン未完了なら次へ進めない
                if self.current_step == 0 and not self.state.get("sso_login_ok", False):
                    state = "disabled"
                self.next_button.config(text="次へ", state=state)

    def _update_sso_status_label(self):
        if not getattr(self, "sso_status_var", None):
            return
        if self.state.get("sso_login_ok"):
            self.sso_status_var.set("SSO ログイン済みです。")
            if hasattr(self, "sso_status_label"):
                self.sso_status_label.config(fg="green")
            if getattr(self, "sso_hint_var", None):
                self.sso_hint_var.set("")
                if hasattr(self, "sso_hint_label"):
                    self.sso_hint_label.config(fg="green")
        else:
            self.sso_status_var.set("SSO ログインが完了していない場合、次へ進めません。")
            if hasattr(self, "sso_status_label"):
                self.sso_status_label.config(fg="red")
            if getattr(self, "sso_hint_var", None):
                self.sso_hint_var.set("※ SSO設定画面で「ログイン」に成功してから続行してください。")
                if hasattr(self, "sso_hint_label"):
                    self.sso_hint_label.config(fg="red")

    def _on_profile_change(self):
        # プロファイル変更時は未ログイン状態に戻し、必要なら設定をUIに反映
        prof = self.sso_profile_var.get().strip()
        self.state["sso_profile"] = prof
        self.state["sso_login_ok"] = False
        if prof:
            cfg = _load_sso_profile_config(prof)
            if cfg:
                _apply_sso_to_ui(self.forward_frames, self.global_vars, prof, cfg.get("sso_region", ""), cfg.get("sso_start_url", ""))
        self._update_nav_labels()
        self._update_sso_status_label()

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
                sso_profile=self.state.get("sso_profile") or None,
            )
            res = perform(opts, preview=self.preview, config_data=cfg_data)
            self.result = res
            self._log_progress("インストール完了" if res.ok else "インストール失敗")
            self.done_label.config(text="インストールが完了しました！" if res.ok else "エラーが発生しました")
            self.progressbar.stop()
            self._show_step(len(self.frames) - 1)

        threading.Thread(target=worker, daemon=True).start()

    def _open_sso_config_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("aws configure sso")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.focus_set()
        entries = {}
        current_profile = self.sso_profile_var.get().strip()
        if not current_profile:
            current_profile = _suggest_new_profile_name(self.sso_profiles)
            current_cfg = {}
            mode_text_initial = "新規プロファイルを作成"
        else:
            current_cfg = _load_sso_profile_config(current_profile) or {}
            mode_text_initial = "既存プロファイルを編集" if current_cfg else "新規プロファイルを作成"

        mode_var = tk.StringVar(value=mode_text_initial)
        mode_label = tk.Label(dialog, textvariable=mode_var, fg="blue")
        mode_label.pack(anchor="w", padx=10, pady=4)

        def add_field(label, key, default=""):
            row = tk.Frame(dialog)
            row.pack(fill="x", padx=10, pady=3)
            tk.Label(row, text=label, width=18, anchor="w").pack(side="left")
            var = tk.StringVar(value=default)
            entries[key] = var
            tk.Entry(row, textvariable=var, width=40).pack(side="left")
            if key in {"sso_start_url", "sso_region", "sso_account_id", "sso_role_name"}:
                tk.Button(row, text="ペースト", command=lambda v=var: v.set(self._paste_clipboard())).pack(side="left", padx=4)

        add_field("プロファイル名", "profile", current_profile or "default")
        add_field("SSO Start URL", "sso_start_url", current_cfg.get("sso_start_url", ""))
        add_field("SSO リージョン", "sso_region", current_cfg.get("sso_region", ""))
        add_field("SSO アカウントID", "sso_account_id", current_cfg.get("sso_account_id", ""))
        add_field("SSO ロール名", "sso_role_name", current_cfg.get("sso_role_name", ""))
        existing_session = current_cfg.get("sso_session", "")

        log = tk.Text(dialog, height=8, width=60, state="disabled")
        log.pack(padx=10, pady=5)
        error_var = tk.StringVar(value="")
        tk.Label(dialog, textvariable=error_var, fg="red").pack(anchor="w", padx=10, pady=(0, 4))

        def log_line(msg: str):
            log.configure(state="normal")
            log.insert("end", msg + "\n")
            log.see("end")
            log.configure(state="disabled")
            log.update_idletasks()

        initial_values = {
            "profile": entries["profile"].get(),
            "sso_start_url": entries["sso_start_url"].get(),
            "sso_region": entries["sso_region"].get(),
            "sso_account_id": entries["sso_account_id"].get(),
            "sso_role_name": entries["sso_role_name"].get(),
        }

        def set_initial_from_entries():
            for k in initial_values:
                initial_values[k] = entries[k].get()

        def calc_dirty() -> bool:
            current = {k: v.get() for k, v in entries.items() if k in initial_values}
            return any(current[k] != initial_values[k] for k in initial_values)

        def update_buttons():
            missing = _sso_required_missing({k: v.get() for k, v in entries.items()})
            dirty = calc_dirty()
            if dirty:
                self.state["sso_login_ok"] = False
            save_state = "normal" if dirty and not missing else "disabled"
            login_state = "normal" if not missing else "disabled"
            save_btn.config(state=save_state)
            login_btn.config(state=login_state)
            if missing:
                error_var.set("必須項目が未入力です: " + ", ".join(missing))
            else:
                error_var.set("")

        def _write_profile(profile: str):
            if not profile:
                error_var.set("プロファイル名を入力してください。")
                return False
            # 必須チェック
            missing = _sso_required_missing({k: v.get() for k, v in entries.items()})
            if missing:
                error_var.set("必須項目が未入力です: " + ", ".join(missing))
                return False
            cfg_path = Path.home() / ".aws" / "config"
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            import configparser

            parser = configparser.RawConfigParser(interpolation=None)
            if cfg_path.exists():
                parser.read(cfg_path, encoding="utf-8")
            section = "default" if profile == "default" else f"profile {profile}"
            exists = section in parser
            if exists and messagebox.askokcancel("確認", f"{profile} は既に存在します。上書きしますか？") is False:
                return False
            if section not in parser:
                parser.add_section(section)
            parser.set(section, "sso_start_url", entries["sso_start_url"].get().strip())
            parser.set(section, "sso_region", entries["sso_region"].get().strip())
            if existing_session:
                parser.set(section, "sso_session", existing_session)
            if entries["sso_account_id"].get().strip():
                parser.set(section, "sso_account_id", entries["sso_account_id"].get().strip())
            if entries["sso_role_name"].get().strip():
                parser.set(section, "sso_role_name", entries["sso_role_name"].get().strip())
            with cfg_path.open("w", encoding="utf-8") as f:
                parser.write(f)
            log_line(f"~/.aws/config に {section} を保存しました。")
            self.sso_profile_var.set(profile)
            self.state["sso_login_ok"] = False  # 保存だけの場合は未ログイン扱い
            # プロファイル一覧を更新
            self.sso_profiles = _get_sso_profiles()
            if hasattr(self, "sso_profile_cb"):
                self.sso_profile_cb.config(values=self.sso_profiles)
            # 設定編集画面へ反映
            if self.global_vars and "sso_start_url" in self.global_vars and entries["sso_start_url"].get().strip():
                self.global_vars["sso_start_url"].set(entries["sso_start_url"].get().strip())
            if self.forward_frames:
                first = self.forward_frames[0]
                vars_map = first if isinstance(first, dict) and "vars" not in first else first.get("vars", first)
                if "profile" in vars_map and not vars_map["profile"].get().strip():
                    vars_map["profile"].set(profile)
            return True

        def on_new_profile():
            for key in ["profile", "sso_start_url", "sso_region", "sso_account_id", "sso_role_name"]:
                if key == "profile":
                    entries[key].set(_suggest_new_profile_name(self.sso_profiles))
                else:
                    entries[key].set("")
            mode_var.set("新規プロファイルを作成")
            log_line("新規プロファイル作成モードです。")
            set_initial_from_entries()
            update_buttons()

        def on_save_only():
            profile = entries["profile"].get().strip()
            if _write_profile(profile):
                log_line("保存しました（ログインは未実施）。")
                self._run_precheck()
                # 保存後の値を初期値として再設定
                set_initial_from_entries()
                _apply_sso_to_ui(self.forward_frames, self.global_vars, profile, entries["sso_region"].get().strip(), entries["sso_start_url"].get().strip())
                update_buttons()

        def on_login_and_maybe_save():
            profile = entries["profile"].get().strip()
            dirty = calc_dirty()
            if dirty:
                if not _write_profile(profile):
                    return
                for k in initial_values:
                    initial_values[k] = entries[k].get()
            else:
                log_line("変更がないため保存はスキップします。")
            # 保存の有無に関わらず UI へ反映
            _apply_sso_to_ui(
                self.forward_frames,
                self.global_vars,
                profile,
                entries["sso_region"].get().strip(),
                entries["sso_start_url"].get().strip(),
            )
            # aws sso login を実行（ブラウザはユーザー任せ）
            cmd = ["aws", "sso", "login", "--profile", profile]
            log_line("aws sso login を実行しています...")
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True)
                if proc.stdout:
                    log_line(proc.stdout.strip())
                if proc.stderr:
                    log_line(proc.stderr.strip())
                if proc.returncode == 0:
                    log_line("SSO ログインが完了しました。")
                    self.state["sso_login_ok"] = True
                    self._update_sso_status_label()
                else:
                    log_line(f"SSO ログインに失敗しました (rc={proc.returncode})")
            except Exception as e:
                log_line(f"SSO ログイン実行に失敗しました: {e}")
            self._run_precheck()

        btn_row = tk.Frame(dialog)
        btn_row.pack(pady=5)
        tk.Button(btn_row, text="新規作成", command=on_new_profile).pack(side="left", padx=5)
        save_btn = tk.Button(btn_row, text="保存", command=on_save_only)
        save_btn.pack(side="left", padx=5)
        login_btn = tk.Button(btn_row, text="保存してログイン", command=on_login_and_maybe_save)
        login_btn.pack(side="left", padx=5)
        tk.Button(btn_row, text="閉じる", command=dialog.destroy).pack(side="left", padx=5)

        # 変更検知でボタン状態更新
        for var in entries.values():
            var.trace_add("write", lambda *_: update_buttons())
        # 初期メッセージ（新規作成時は強調）
        if mode_text_initial == "新規プロファイルを作成":
            log_line("新規プロファイル作成モードです。")
        update_buttons()

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
        win.transient(self.root)
        win.grab_set()
        win.focus_set()
        tk.Label(win, text="前提条件をインストールしています...").pack(anchor="w", padx=10, pady=5)
        pb = ttk.Progressbar(win, mode="indeterminate")
        pb.pack(fill="x", padx=10, pady=5)
        log = tk.Text(win, height=8, width=60, state="disabled")
        log.pack(padx=10, pady=5)
        close_btn = tk.Button(win, text="閉じる", state="disabled", command=lambda: win.destroy())
        close_btn.pack(pady=5)

        def log_line(msg: str):
            log.configure(state="normal")
            log.insert("end", msg + "\n")
            log.see("end")
            log.configure(state="disabled")

        def worker():
            pb.start(10)
            cmds_to_run = suggested
            if installer_core.platforms.is_linux():
                if shutil.which("pkexec"):
                    cmds_to_run = [["pkexec", "sh", "-c", " ".join(cmd)] for cmd in suggested]
                else:
                    log_line("pkexec が見つからないため自動実行できません。下記コマンドを手動で実行してください。")
                    for cmd in suggested:
                        log_line(" ".join(cmd))
                    pb.stop()
                    close_btn.config(state="normal")
                    return
            results = installer_core.run_commands(cmds_to_run)
            for cmd, rc in results:
                log_line(f"{' '.join(cmd)} -> rc={rc}")
            pb.stop()
            check = installer_core.preinstall_check()
            if check.ok:
                log_line("前提条件チェック: OK")
            else:
                log_line("前提条件チェック: NG")
                for issue in check.issues:
                    log_line(f"- {issue}")
            # メイン画面の表示を更新
            self.state["precheck_ok"] = check.ok
            self.state["precheck_issues"] = check.issues
            self.state["precheck_suggested"] = check.suggested_installs
            self.root.after(0, self._run_precheck)
            close_btn.config(state="normal")

        threading.Thread(target=worker, daemon=True).start()


def run_gui(args):
    """
    Tk ウィザードを起動するエントリポイント。
    - OS 非対応なら即終了
    - ナビゲーションボタンを下部固定でセットし、キャンセル時は確認ダイアログを出す
    """
    if not _ensure_supported_os(args):
        return 1
    root = tk.Tk()
    wizard = InstallerWizard(root, preview=args.preview, simulate_precheck_fail=getattr(args, "simulate_precheck_fail", False))
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
    """
    CLI 入口。--headless のときは非 GUI で実行し、通常は Tk ウィザードを起動。
    """
    args = _parse_args()
    if args.headless:
        sys.exit(run_headless(args))
    run_gui(args)


if __name__ == "__main__":
    main()
