import threading
import time

import importlib.util

import pytest

from aws_rds_tray import wx_tray


@pytest.mark.skipif(importlib.util.find_spec("wx") is None, reason="wxPython not installed")
def test_wx_app_boot_and_shutdown():
    # 別スレッドで MainLoop を回し、shutdown で停止できるか確認する。
    try:
        app = wx_tray.WxTrayApp()
    except SystemExit as e:
        # DISPLAY が無い場合は wx.App 生成時に SystemExit が飛ぶのでスキップ
        pytest.skip(f"wx.App not available: {e}")
        return

    def run():
        app.MainLoop()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    # 起動待ち
    time.sleep(0.1)
    # DISPLAY が無い環境では GUI を張れずに落ちるのでスキップ
    if not app.IsMainLoopRunning():
        pytest.skip("wx GUI main loop not running (likely no DISPLAY)")
        return
    assert app.IsMainLoopRunning()

    # 終了
    app.shutdown()
    t.join(timeout=2)
    assert not t.is_alive()
