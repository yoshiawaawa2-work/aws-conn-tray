import importlib.util

import pytest

from aws_conn_tray import wx_tray


@pytest.mark.skipif(importlib.util.find_spec("wx") is None, reason="wxPython not installed")
def test_menu_items_structure():
    items = wx_tray.WxTrayIcon.menu_items()
    labels = [label for label, _ in items if label is not None]
    assert labels == ["⚙️ 設定", "SSO ログイン", "全フォワード再接続", "終了"]
