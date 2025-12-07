from typing import Callable, Optional

try:
    import wx
    import wx.adv
except ImportError:
    wx = None

from .paths import ICON_DIR
from .logger import log_message


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

    def OnInit(self):
        return True

    def init_tray(self, callbacks: dict):
        # コールバックは dict で受け取る（未設定は None）
        self.tray_icon = WxTrayIcon(
            parent=None,
            on_settings=callbacks.get("settings"),
            on_sso_login=callbacks.get("sso_login"),
            on_reconnect=callbacks.get("reconnect"),
            on_quit=callbacks.get("quit"),
        )

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
        self._app.Exit()


def run_wx_app():
    """
    エントリポイント。
    """
    app = WxTrayApp()
    app.init_tray(callbacks={})
    app.MainLoop()
