from typing import Callable, Optional, Dict, Any
import os
import threading
import webbrowser

try:
    import wx
    import wx.adv
    import wx.html2
except ImportError:
    wx = None

from .paths import ICON_DIR
from .logger import log_message
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
        self.config = config
        if config:
            self.ssm = SsmPortForward(
                instance_id=config.instance_id,
                profile=config.profile,
                region=config.region,
                local_port=config.local_port,
                remote_host=config.remote_host,
                remote_port=config.remote_port,
            )
        # コールバックは dict で受け取る（未設定は None）
        self.tray_icon = WxTrayIcon(
            parent=None,
            on_settings=callbacks.get("settings"),
            on_sso_login=callbacks.get("sso_login"),
            on_reconnect=callbacks.get("reconnect"),
            on_quit=callbacks.get("quit"),
        )
        # 監視タイマー
        if config:
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
    # ここではコールバック未設定（後続で実装）
    app.init_tray(callbacks={}, config=config)
    app.MainLoop()
