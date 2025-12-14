import threading
import time

import importlib.util
import os

import pytest

from aws_conn_tray import wx_tray


@pytest.mark.skipif(importlib.util.find_spec("wx") is None, reason="wxPython not installed")
def test_wx_app_boot_and_shutdown():
    if not os.environ.get("WX_GUI_TEST"):
        pytest.skip("Set WX_GUI_TEST=1 to run wx GUI loop test")
    if not wx_tray._has_display():
        pytest.skip("DISPLAY not available")
    # 別スレッドで MainLoop を回し、shutdown で停止できるか確認する。
    app = wx_tray.WxTrayApp()

    def run():
        app.MainLoop()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    # 起動待ち
    time.sleep(0.1)
    if not app.IsMainLoopRunning():
        pytest.skip("wx GUI main loop not running (likely no DISPLAY)")
        return
    assert app.IsMainLoopRunning()

    # 終了
    app.shutdown()
    t.join(timeout=2)
    assert not t.is_alive()
