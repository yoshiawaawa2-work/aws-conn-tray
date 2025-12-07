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
