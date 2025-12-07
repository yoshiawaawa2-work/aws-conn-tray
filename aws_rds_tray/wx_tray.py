from typing import Callable, Optional, Dict, Any
import os
import threading
import webbrowser
import json
import subprocess
import sys

try:
    import wx
    import wx.adv
    import wx.html2
except ImportError:
    wx = None

from .paths import ICON_DIR, CONFIG_PATH, CONFIG_EXAMPLE_PATH, APP_ROOT
from .logger import log_message, notify
from .config_loader import Config, load_config
from .sso import check_sso_token, is_sso_session_valid, start_sso_login_browser
from .ssm import SsmPortForward


def _has_display():
    if wx is None:
        return False
    try:
        return wx.Display.GetCount() > 0
    except Exception:
        return False


class WxTrayIcon(wx.adv.TaskBarIcon):
    """
    トレイアイコンとメニュー。
    """

    def __init__(
        self,
        parent=None,
        on_settings: Optional[Callable[[], None]] = None,
        on_sso_login: Optional[Callable[[], None]] = None,
        on_reconnect: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.parent = parent
        self.cb_settings = on_settings
        self.cb_sso_login = on_sso_login
        self.cb_reconnect = on_reconnect
        self.cb_quit = on_quit
        self.set_icon_color("red")
        # 左右クリックどちらでもメニューを開けるようにする
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DOWN, self._on_taskbar_click)
        self.Bind(wx.adv.EVT_TASKBAR_RIGHT_DOWN, self._on_taskbar_click)

    @staticmethod
    def menu_items():
        """
        メニュー構造の定義を返す（ラベル, ハンドラ名）。
        テストで GUI に依存せず確認できるようにデータ化。
        """
        return [
            ("⚙️ 設定", "on_settings"),
            ("SSO ログイン", "on_sso_login"),
            ("ポートフォワード再接続", "on_reconnect"),
            (None, None),  # separator
            ("終了", "on_quit"),
        ]

    def CreatePopupMenu(self):
        menu = wx.Menu()
        for label, handler_name in self.menu_items():
            if label is None:
                menu.AppendSeparator()
                continue
            item = menu.Append(-1, label)
            handler = getattr(self, handler_name, None)
            if handler:
                self.Bind(wx.EVT_MENU, handler, item)
        return menu

    def _on_taskbar_click(self, event):
        menu = self.CreatePopupMenu()
        if menu:
            self.PopupMenu(menu)
            menu.Destroy()
        event.Skip()

    def set_icon_color(self, color: str):
        try:
            path = os.path.join(ICON_DIR, f"{color}.png")
            icon = wx.Icon(path, wx.BITMAP_TYPE_PNG)
            self.SetIcon(icon, "aws-rds-tray")
        except Exception as e:
            log_message(f"[wx] icon set failed: {e}")

    def on_settings(self, _evt=None):
        if self.cb_settings:
            self.cb_settings()

    def on_sso_login(self, _evt=None):
        if self.cb_sso_login:
            self.cb_sso_login()

    def on_reconnect(self, _evt=None):
        if self.cb_reconnect:
            self.cb_reconnect()

    def on_quit(self, _evt=None):
        if self.cb_quit:
            self.cb_quit()
        wx.CallAfter(wx.GetApp().Exit)


class WxTrayApp:
    """
    wxPython ベースのトレイアプリ。
    wx が無い、または DISPLAY 無しの場合は ImportError/RuntimeError を投げる。
    """

    def __init__(self):
        if wx is None:
            raise ImportError("wxPython is not installed.")
        if not _has_display():
            raise RuntimeError("DISPLAY が利用できません。GUI 環境で実行してください。")
        self._app = wx.App(clearSigInt=False)
        self.tray_icon: Optional[WxTrayIcon] = None
        self.config: Optional[Config] = None
        self.ssm: Optional[SsmPortForward] = None
        self.timer: Optional[wx.Timer] = None

    def OnInit(self):
        return True

    def init_tray(self, callbacks: dict, config: Optional[Config] = None):
        self.config = config or load_config()
        self.ssm = SsmPortForward(
            instance_id=self.config.instance_id,
            profile=self.config.profile,
            region=self.config.region,
            local_port=self.config.local_port,
            remote_host=self.config.remote_host,
            remote_port=self.config.remote_port,
        )
        # コールバックは dict で受け取る（未設定は None）
        callbacks = callbacks or {}
        callbacks.setdefault("settings", self.on_settings)
        callbacks.setdefault("sso_login", self.on_sso_login)
        callbacks.setdefault("reconnect", self.on_reconnect)
        callbacks.setdefault("quit", self.on_quit)
        self.tray_icon = WxTrayIcon(
            parent=None,
            on_settings=callbacks.get("settings"),
            on_sso_login=callbacks.get("sso_login"),
            on_reconnect=callbacks.get("reconnect"),
            on_quit=callbacks.get("quit"),
        )
        # 監視タイマー
        self._start_monitor()

    def _start_monitor(self):
        if self.timer:
            try:
                self.timer.Stop()
            except Exception:
                pass
        self.timer = wx.Timer(self._app)
        self._app.Bind(wx.EVT_TIMER, lambda evt: self.monitor_once(), self.timer)
        self.timer.Start(self.config.tunnel_check_interval_seconds * 1000)
        self.monitor_once()

    def MainLoop(self):
        self._app.MainLoop()

    def IsMainLoopRunning(self):
        return self._app.IsMainLoopRunning()

    def shutdown(self):
        if self.tray_icon:
            try:
                self.tray_icon.Destroy()
            except Exception:
                pass
        if self.timer:
            try:
                self.timer.Stop()
            except Exception:
                pass
        self._app.Exit()

    # ===== 監視ロジック =====
    def monitor_once(self):
        if not self.config:
            return

        token_ok, remaining = check_sso_token()
        if token_ok and not is_sso_session_valid(self.config.profile, self.config.region):
            token_ok = False

        tunnel_ok = self.ssm.is_alive() if self.ssm else False
        log_message(f"[wx-monitor] token_ok={token_ok} remaining={remaining} tunnel_ok={tunnel_ok}")

        if token_ok:
            if not tunnel_ok and self.ssm:
                self.tray_icon.set_icon_color("yellow")
                self.ssm.start()
            elif tunnel_ok:
                self.tray_icon.set_icon_color("green")
        else:
            # トークン無効時はブラウザログインを案内
            start_sso_login_browser(self.config.profile, self.config.region)
            self.tray_icon.set_icon_color("red")

    # ===== メニュー操作ハンドラ =====
    def on_settings(self):
        defaults = _load_example_config()
        current = _config_to_dict(self.config)
        new_data = self._show_settings_dialog(current, defaults)
        if not new_data:
            return
        ok, errors, parsed = validate_settings_input(new_data, defaults)
        if not ok:
            notify("入力エラー", "\n".join(errors))
            return
        if parsed != current:
            _save_config(parsed)
            notify("設定を保存しました", "アプリを再起動します。")
            try:
                subprocess.Popen([sys.executable, os.path.join(APP_ROOT, "tray_app.py")])
            except Exception as e:
                log_message(f"[wx-settings] restart failed: {e}")
            self.shutdown()

    def on_sso_login(self):
        start_sso_login_browser(self.config.profile, self.config.region)

    def on_reconnect(self):
        if self.ssm and not self.ssm.is_alive():
            self.tray_icon.set_icon_color("yellow")
            self.ssm.start()
        else:
            notify("ポートフォワード", "すでにポートフォワードは有効です。")

    def on_quit(self):
        self.shutdown()

    def _show_settings_dialog(self, current: Dict[str, Any], defaults: Dict[str, Any]):
        """
        設定ダイアログ。wx が無い場合や DISPLAY が無い場合は None を返す。
        """
        if wx is None or not _has_display():
            return None
        dlg = wx.Dialog(None, title="設定", size=(400, 400))
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        entries = {}

        def add_text(label, key):
            hs = wx.BoxSizer(wx.HORIZONTAL)
            hs.Add(wx.StaticText(panel, label=label), 0, wx.ALL, 5)
            txt = wx.TextCtrl(panel, value=str(current.get(key, "")))
            entries[key] = txt
            hs.Add(txt, 1, wx.ALL | wx.EXPAND, 5)
            sizer.Add(hs, 0, wx.EXPAND)

        def add_int(label, key):
            hs = wx.BoxSizer(wx.HORIZONTAL)
            hs.Add(wx.StaticText(panel, label=label), 0, wx.ALL, 5)
            txt = wx.TextCtrl(panel, value=str(current.get(key, defaults.get(key, 0))))
            entries[key] = txt
            hs.Add(txt, 1, wx.ALL | wx.EXPAND, 5)
            sizer.Add(hs, 0, wx.EXPAND)

        for key in REQUIRED_KEYS:
            add_text(key, key)
        for key in INT_KEYS:
            add_int(key, key)

        btns = wx.StdDialogButtonSizer()
        btn_ok = wx.Button(dlg, wx.ID_OK)
        btn_cancel = wx.Button(dlg, wx.ID_CANCEL)
        btns.AddButton(btn_ok)
        btns.AddButton(btn_cancel)
        btns.Realize()
        sizer.Add(btns, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        panel.SetSizer(sizer)
        dlg.Fit()
        result = dlg.ShowModal()
        if result != wx.ID_OK:
            dlg.Destroy()
            return None
        data = {k: entries[k].GetValue() for k in entries}
        dlg.Destroy()
        return data


# ===== WebView ヘルパー =====

def open_webview_authorize(url: str) -> bool:
    """
    wx.html2.WebView を使って認可URLを開く。失敗したら False。
    """
    if wx is None:
        return False
    try:
        frame = wx.Frame(None, title="AWS SSO ログイン", size=(800, 600))
        browser = wx.html2.WebView.New(frame)
        browser.LoadURL(url)
        frame.Show()
        return True
    except Exception as e:
        log_message(f"[wx-webview] failed: {e}")
        return False


def start_sso_login_webview(url: str) -> bool:
    """
    認可URLを WebView で開き、失敗時はデフォルトブラウザを開く。
    """
    if open_webview_authorize(url):
        return True
    try:
        notify(
            "SSO ログイン",
            "ブラウザで SSO ログインを開始します。",
            on_click=lambda: webbrowser.open(url),
        )
        webbrowser.open(url)
        return True
    except Exception as e:
        log_message(f"[wx-webview] fallback browser failed: {e}")
        return False


# ===== 設定バリデーション =====

REQUIRED_KEYS = ["instance_id", "profile", "region", "remote_host", "sso_start_url"]
INT_KEYS = [
    "local_port",
    "remote_port",
    "sso_login_timeout_seconds",
    "sso_auto_refresh_margin_seconds",
    "tunnel_check_interval_seconds",
]


def validate_settings_input(data: Dict[str, Any], defaults: Dict[str, Any]):
    errors = []
    parsed: Dict[str, Any] = {}

    for key in REQUIRED_KEYS:
        val = str(data.get(key, "")).strip()
        if not val:
            errors.append(f"{key}: 必須項目です。")
        parsed[key] = val

    for key in INT_KEYS:
        raw = str(data.get(key, "")).strip()
        try:
            num = int(raw)
            if num < 0:
                raise ValueError()
            parsed[key] = num
        except Exception:
            default_val = defaults.get(key, "")
            errors.append(f"{key}: 0以上の整数を入力してください。（デフォルト: {default_val}）")

    return len(errors) == 0, errors, parsed


# ===== エントリポイント =====

def run_wx_app():
    """
    エントリポイント。
    """
    config = load_config()
    app = WxTrayApp()
    app.init_tray(callbacks={}, config=config)
    app.MainLoop()


# ===== 共通ヘルパー =====


def _load_example_config() -> Dict[str, Any]:
    with open(CONFIG_EXAMPLE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _config_to_dict(cfg: Optional[Config]) -> Dict[str, Any]:
    if cfg is None:
        return {}
    return {
        "instance_id": cfg.instance_id,
        "profile": cfg.profile,
        "region": cfg.region,
        "remote_host": cfg.remote_host,
        "remote_port": cfg.remote_port,
        "local_port": cfg.local_port,
        "sso_start_url": cfg.sso_start_url,
        "sso_login_timeout_seconds": cfg.sso_login_timeout_seconds,
        "sso_auto_refresh_margin_seconds": cfg.sso_auto_refresh_margin_seconds,
        "tunnel_check_interval_seconds": cfg.tunnel_check_interval_seconds,
    }


def _save_config(data: Dict[str, Any]):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
