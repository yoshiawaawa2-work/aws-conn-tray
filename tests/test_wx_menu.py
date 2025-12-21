import importlib.util

import pytest

from aws_conn_tray import wx_tray


@pytest.mark.skipif(importlib.util.find_spec("wx") is None, reason="wxPython not installed")
def test_menu_items_structure():
    items = wx_tray.WxTrayIcon.menu_items()
    labels = [label for label, _ in items if label is not None]
    assert labels == ["⚙️ 設定", "SSO ログイン", "全フォワード再接続", "自動起動解除して終了（最終手段）", "終了"]


@pytest.mark.skipif(importlib.util.find_spec("wx") is None, reason="wxPython not installed")
def test_status_menu_item_style(monkeypatch):
    import wx
    from aws_conn_tray import wx_tray
    if not wx_tray._has_display():
        pytest.skip("DISPLAY not available")
    app = wx.App(clearSigInt=False)
    tray = wx_tray.WxTrayIcon()
    tray.forward_statuses = {"rds": "red", "apprunner": "yellow"}
    menu = tray.CreatePopupMenu()
    labels = [item.GetItemLabelText() for item in menu.GetMenuItems()]
    # メインメニュー上にヘッダーが存在し、インジケータが並んでいること
    assert any("フォワード状態" in lbl for lbl in labels)
    assert any("🟥 rds" in lbl for lbl in labels)
    app.ExitMainLoop()
