import os
import threading
import time
from typing import Optional

import gi
gi.require_version("Gtk", "3.0")
# AppIndicator は環境によって Ayatana と GNOME の2種類があるためフォールバックする
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
except (ValueError, ImportError):
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3 as AppIndicator

from gi.repository import Gtk, GObject, GLib

from .config_loader import Config
from .logger import notify, log_message
from .paths import ICON_DIR
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
        self.indicator.set_menu(menu)

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
