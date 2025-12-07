try:
    import wx
except ImportError:
    wx = None


class WxTrayApp:
    """
    wxPython ベースの最小スケルトン。
    wx が無い環境では生成できないため、明示的に例外を投げる。
    """

    def __init__(self):
        if wx is None:
            raise ImportError("wxPython is not installed.")
        self._app = wx.App()

    def OnInit(self):
        return True

    def MainLoop(self):
        self._app.MainLoop()

    def IsMainLoopRunning(self):
        return self._app.IsMainLoopRunning()

    def shutdown(self):
        self._app.Exit()


def run_wx_app():
    """
    スケルトンのエントリポイント。
    wx が無い場合は ImportError を送出する。
    """
    app = WxTrayApp()
    app.MainLoop()


if wx is not None:
    import wx.adv

    class WxTrayIcon(wx.adv.TaskBarIcon):
        """
        トレイアイコンとメニューの雛形。
        まだハンドラはプレースホルダで、後続で実装する。
        """

        def __init__(self, parent=None):
            super().__init__()
            self.parent = parent
            try:
                icon = wx.ArtProvider.GetIcon(wx.ART_INFORMATION, wx.ART_OTHER, (16, 16))
                self.SetIcon(icon)
            except Exception:
                pass

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

        # 以下は後で実装予定のハンドラのダミー
        def on_settings(self, _evt=None):
            pass

        def on_sso_login(self, _evt=None):
            pass

        def on_reconnect(self, _evt=None):
            pass

        def on_quit(self, _evt=None):
            if self.parent:
                try:
                    self.parent.shutdown()
                except Exception:
                    pass
            wx.CallAfter(wx.GetApp().Exit)
