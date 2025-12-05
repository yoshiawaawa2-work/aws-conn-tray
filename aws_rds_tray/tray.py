import json
import os
import sys
import subprocess
import threading
import time
from typing import Optional, Dict, Any

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
# AppIndicator は環境によって Ayatana と GNOME の2種類があるためフォールバックする
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
except (ValueError, ImportError):
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3 as AppIndicator

from gi.repository import Gtk, GObject, GLib, Gdk

from .config_loader import Config
from .logger import notify, log_message
from .paths import ICON_DIR, CONFIG_PATH, CONFIG_EXAMPLE_PATH, APP_ROOT
from .sso import (
    check_sso_token,
    is_sso_session_valid,
    run_sso_login,
    start_sso_login_browser,
    token_cache_exists,
    open_webview_minimized,
    bring_webview_to_front,
    LAST_DEVICE_URL,
    LAST_DEVICE_CODE,
)
from .ssm import SsmPortForward

class TrayApp:
    def __init__(self, config: Config):
        self.config = config
        self.ssm = SsmPortForward(
            instance_id=config.instance_id,
            profile=config.profile,
            region=config.region,
            local_port=config.local_port,
            remote_host=config.remote_host,
            remote_port=config.remote_port,
        )

        self.indicator = AppIndicator.Indicator.new(
            "aws-rds-tray",
            os.path.join(ICON_DIR, "red.png"),
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()
        settings_item = Gtk.MenuItem.new_with_label("⚙️ 設定")
        settings_item.connect("activate", self.on_settings_clicked)
        menu.append(settings_item)

        open_sso_item = Gtk.MenuItem.new_with_label("SSO ログイン")
        open_sso_item.connect("activate", self.on_sso_login_clicked)
        menu.append(open_sso_item)

        reconnect_item = Gtk.MenuItem.new_with_label("ポートフォワード再接続")
        reconnect_item.connect("activate", self.on_reconnect_clicked)
        menu.append(reconnect_item)

        separator = Gtk.SeparatorMenuItem()
        menu.append(separator)

        quit_item = Gtk.MenuItem.new_with_label("終了")
        quit_item.connect("activate", self.on_quit_clicked)
        menu.append(quit_item)

        menu.show_all()
        self.menu = menu
        self.indicator.set_menu(menu)
        # 設定ダイアログ中に使う空メニュー（クリック無効化用）
        self.disabled_menu = Gtk.Menu()
        self.disabled_menu.set_sensitive(False)
        self.disabled_menu.show_all()
        self.settings_dialog_open = False
        # 設定ダイアログ中はメニューを無効化するためのフラグを Indicator 側でも参照できるようにする
        self.indicator.set_secondary_activate_target(None)

        self.auto_refresh_in_progress = False
        self.notified_token_invalid = False
        self.reauth_in_progress = False
        self.initial_cache_missing = not token_cache_exists()

        self._stop_event = threading.Event()
        self.thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.thread.start()

    def on_sso_login_clicked(self, _menu_item):
        def worker():
            notify("SSO ログイン", "ブラウザで SSO ログインを開始します。")
            started = start_sso_login_browser(self.config.profile, self.config.region)
            if not started:
                notify("SSO ログインエラー", "aws コマンドまたはネットワークを確認してください。")
        threading.Thread(target=worker, daemon=True).start()

    def on_settings_clicked(self, _menu_item):
        defaults = _load_example_config()
        current = _config_to_dict(self.config)

        # 設定ダイアログ中はメニューを無効化
        if hasattr(self, "menu"):
            self.menu.set_sensitive(False)
            # アイコンクリック時のメニュー表示も無効化
            self.indicator.set_menu(self.disabled_menu)
        self.settings_dialog_open = True

        dialog = Gtk.Dialog(title="設定", transient_for=None, flags=Gtk.DialogFlags.MODAL)
        dialog.add_button("キャンセル", Gtk.ResponseType.CANCEL)
        dialog.add_button("OK", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        grid = Gtk.Grid(column_spacing=6, row_spacing=6, margin=12)
        content.add(grid)

        entries: Dict[str, Gtk.Entry] = {}

        # 上部中央に「アプリ終了」ボタンを配置し、その下に区切り線を入れる
        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        quit_btn = Gtk.Button(label="アプリ終了")
        quit_btn.set_relief(Gtk.ReliefStyle.NORMAL)
        quit_btn.connect("clicked", lambda _b, dlg=None: (_b.get_toplevel().destroy() if _b.get_toplevel() else None) or Gtk.main_quit())
        quit_btn.set_name("quit-button")
        # 強調スタイル: 赤背景＋太字（Gtkテーマに依存しない簡易指定）
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            #quit-button {
                background: #cc0000;
                color: #ffffff;
                font-weight: bold;
                font-size: 14px;
                padding: 2px 6px;
            }
        """)
        style_context = quit_btn.get_style_context()
        style_context.add_provider(css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        quit_btn.set_halign(Gtk.Align.START)
        top_bar.pack_start(quit_btn, False, False, 0)
        top_bar.pack_start(Gtk.Label(), True, True, 0)  # 右余白
        top_bar.show_all()
        grid.attach(top_bar, 0, 0, 3, 1)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        grid.attach(sep, 0, 1, 3, 1)

        def add_text_row(row, key, label_text, with_paste=False):
            label = Gtk.Label(label=label_text, halign=Gtk.Align.START)
            entry = Gtk.Entry()
            entry.set_text(str(current.get(key, "")))
            entries[key] = entry
            grid.attach(label, 0, row, 1, 1)
            grid.attach(entry, 1, row, 1, 1)
            if with_paste:
                btn = Gtk.Button(label="ペースト")
                btn.connect("clicked", lambda _b: _paste_to_entry(entry))
                grid.attach(btn, 2, row, 1, 1)
            return row + 1

        def add_int_row(row, key, label_text):
            label = Gtk.Label(label=label_text, halign=Gtk.Align.START)
            entry = Gtk.Entry()
            entry.set_text(str(current.get(key, defaults.get(key, 0))))
            entries[key] = entry
            grid.attach(label, 0, row, 1, 1)
            grid.attach(entry, 1, row, 1, 1)
            btn = Gtk.Button(label="デフォルトに戻す")
            btn.connect("clicked", lambda _b: entry.set_text(str(defaults.get(key, ""))))
            grid.attach(btn, 2, row, 1, 1)
            return row + 1

        row = 2  # 0: アプリ終了ボタン, 1: 区切り, 2から項目開始
        row = add_text_row(row, "instance_id", "instance_id", with_paste=True)
        row = add_text_row(row, "profile", "profile", with_paste=True)
        row = add_text_row(row, "region", "region", with_paste=True)
        row = add_text_row(row, "remote_host", "remote_host", with_paste=True)
        row = add_text_row(row, "sso_start_url", "sso_start_url", with_paste=True)

        row = add_int_row(row, "local_port", "local_port")
        row = add_int_row(row, "remote_port", "remote_port")
        row = add_int_row(row, "sso_login_timeout_seconds", "sso_login_timeout_seconds")
        row = add_int_row(row, "sso_auto_refresh_margin_seconds", "sso_auto_refresh_margin_seconds")
        row = add_int_row(row, "tunnel_check_interval_seconds", "tunnel_check_interval_seconds")

        dialog.show_all()
        try:
            dialog.set_keep_above(True)
            dialog.present()
        except Exception:
            pass
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            new_data = {k: entries[k].get_text().strip() for k in entries}
            valid, errors, parsed = validate_config_input(new_data, defaults)
            if not valid:
                msg = "\n".join(errors)
                m = Gtk.MessageDialog(
                    transient_for=dialog,
                    flags=0,
                    message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.OK,
                    text="入力エラーがあります。",
                )
                m.format_secondary_text(msg)
                m.run()
                m.destroy()
            else:
                if parsed != current:
                    _save_config(parsed)
                    notify("設定を保存しました", "アプリを再起動します。")
                    try:
                        subprocess.Popen([sys.executable, os.path.join(APP_ROOT, "tray_app.py")])
                    except Exception:
                        pass
                    Gtk.main_quit()
                dialog.destroy()
        else:
            dialog.destroy()
        self.settings_dialog_open = False
        if hasattr(self, "menu"):
            self.menu.set_sensitive(True)
            # メニューを戻す
            self.indicator.set_menu(self.menu)

    def on_reconnect_clicked(self, _menu_item):
        if self.ssm.is_alive():
            notify("ポートフォワード", "すでにポートフォワードは有効です。")
        else:
            notify("ポートフォワード", "ポートフォワード再接続を試行します。")
            self.ssm.start()

    def on_quit_clicked(self, _menu_item):
        self._stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        Gtk.main_quit()

    def set_icon_color(self, color: str):
        icon_path = os.path.join(ICON_DIR, f"{color}.png")
        if not os.path.exists(icon_path):
            return

        def _update_icon():
            if self._stop_event.is_set():
                return False
            try:
                self.indicator.set_icon(icon_path)
            except Exception:
                return False
            return False

        GLib.idle_add(_update_icon, priority=GObject.PRIORITY_DEFAULT)

    def monitor_loop(self):
        while not self._stop_event.is_set():
            token_ok, remaining = check_sso_token()
            if token_ok and not is_sso_session_valid(self.config.profile, self.config.region):
                token_ok = False
            tunnel_ok = self.ssm.is_alive()

            if token_ok and remaining is not None:
                self.notified_token_invalid = False
                self.reauth_in_progress = False
                self.initial_cache_missing = False
                if remaining <= self.config.sso_auto_refresh_margin_seconds and not self.auto_refresh_in_progress:
                    self.auto_refresh_in_progress = True
                    threading.Thread(target=self._try_auto_refresh, daemon=True).start()
            else:
                if not self.notified_token_invalid:
                    msg = "ブラウザで SSO ログインを行ってください。"
                    if self.initial_cache_missing:
                        msg = "ここをクリックして SSO ログインを行ってください。"
                    open_webview_minimized(LAST_DEVICE_URL or self.config.sso_start_url)
                    bring_webview_to_front()
                    notify("AWS SSO トークンが無効です", msg, on_click=bring_webview_to_front)
                    self.notified_token_invalid = True
                if not self.reauth_in_progress:
                    self.reauth_in_progress = True
                    threading.Thread(target=self._force_reauth, daemon=True).start()

            if token_ok and not tunnel_ok:
                self.set_icon_color("yellow")
                self.ssm.start()
            elif token_ok and tunnel_ok:
                self.set_icon_color("green")
            else:
                self.set_icon_color("red")

            for _ in range(self.config.tunnel_check_interval_seconds):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    def _try_auto_refresh(self):
        success = run_sso_login(
            profile=self.config.profile,
            region=self.config.region,
            sso_start_url=self.config.sso_start_url,
            timeout=self.config.sso_login_timeout_seconds,
        )
        self.auto_refresh_in_progress = False
        if success and not self.ssm.is_alive():
            self.ssm.start()

    def _force_reauth(self):
        success = run_sso_login(
            profile=self.config.profile,
            region=self.config.region,
            sso_start_url=self.config.sso_start_url,
            timeout=self.config.sso_login_timeout_seconds,
        )
        self.reauth_in_progress = False
        if success:
            self.notified_token_invalid = False
            if not self.ssm.is_alive():
                self.ssm.start()


def _paste_to_entry(entry: Gtk.Entry):
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    text = clipboard.wait_for_text()
    if text:
        entry.set_text(text.strip())


def _load_example_config() -> Dict[str, Any]:
    try:
        with open(CONFIG_EXAMPLE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _config_to_dict(cfg: Config) -> Dict[str, Any]:
    return {
        "instance_id": cfg.instance_id,
        "profile": cfg.profile,
        "region": cfg.region,
        "local_port": cfg.local_port,
        "remote_host": cfg.remote_host,
        "remote_port": cfg.remote_port,
        "sso_start_url": cfg.sso_start_url,
        "sso_login_timeout_seconds": cfg.sso_login_timeout_seconds,
        "sso_auto_refresh_margin_seconds": cfg.sso_auto_refresh_margin_seconds,
        "tunnel_check_interval_seconds": cfg.tunnel_check_interval_seconds,
    }


def validate_config_input(data: Dict[str, str], defaults: Dict[str, Any]):
    errors = []
    parsed: Dict[str, Any] = {}

    for key in ["instance_id", "profile", "region", "remote_host", "sso_start_url"]:
        value = data.get(key, "").strip()
        if not value:
            errors.append(f"{key}: 必須項目です。")
        parsed[key] = value

    for key in [
        "local_port",
        "remote_port",
        "sso_login_timeout_seconds",
        "sso_auto_refresh_margin_seconds",
        "tunnel_check_interval_seconds",
    ]:
        raw = data.get(key, "").strip()
        try:
            num = int(raw)
            if num < 0:
                raise ValueError()
            parsed[key] = num
        except Exception:
            default_val = defaults.get(key, "")
            errors.append(f"{key}: 0以上の整数を入力してください。（デフォルト: {default_val}）")

    return len(errors) == 0, errors, parsed


def _save_config(new_data: Dict[str, Any]):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
        log_message("config.json を更新しました。")
    except Exception as e:
        notify("設定保存に失敗しました", str(e))
        log_message(f"Failed to save config: {e}")
