import importlib.util

import pytest

from aws_rds_tray import wx_tray


def skip_if_no_wx():
    if importlib.util.find_spec("wx") is None:
        pytest.skip("wxPython not installed")


def test_validate_settings_input():
    defaults = {
        "local_port": 1111,
        "remote_port": 5432,
        "sso_login_timeout_seconds": 900,
        "sso_auto_refresh_margin_seconds": 300,
        "tunnel_check_interval_seconds": 10,
    }
    data = {
        "instance_id": "i-1",
        "profile": "p",
        "region": "r",
        "remote_host": "host",
        "sso_start_url": "url",
        "local_port": "2222",
        "remote_port": "5432",
        "sso_login_timeout_seconds": "900",
        "sso_auto_refresh_margin_seconds": "300",
        "tunnel_check_interval_seconds": "10",
    }
    ok, errors, parsed = wx_tray.validate_settings_input(data, defaults)
    assert ok
    assert parsed["local_port"] == 2222

    data_bad = {"instance_id": "", "local_port": "-1"}
    ok, errors, parsed = wx_tray.validate_settings_input(data_bad, defaults)
    assert not ok
    assert any("必須" in e for e in errors)


@pytest.mark.skipif(importlib.util.find_spec("wx") is None, reason="wxPython not installed")
def test_monitor_starts_ssm_when_token_ok(monkeypatch):
    # モック環境（DISPLAYが無いと wx が起動できない場合は skip）
    if not wx_tray._has_display():
        pytest.skip("DISPLAY not available")

    calls = {"ssm_start": 0, "icon": []}

    class DummySsm:
        def is_alive(self):
            return False
        def start(self):
            calls["ssm_start"] += 1

    app = wx_tray.WxTrayApp()
    app.ssm = DummySsm()
    app.config = type("C", (), {"profile": "p", "region": "r", "tunnel_check_interval_seconds": 1})()

    def fake_check():
        return True, 100
    def fake_session(profile, region):
        return True
    monkeypatch.setattr(wx_tray, "check_sso_token", fake_check)
    monkeypatch.setattr(wx_tray, "is_sso_session_valid", fake_session)
    monkeypatch.setattr(wx_tray.WxTrayIcon, "set_icon_color", lambda self, c: calls["icon"].append(c))
    app.tray_icon = wx_tray.WxTrayIcon()

    # monitor_once を直接呼ぶ
    app.monitor_once()
    assert calls["ssm_start"] == 1
    assert "yellow" in calls["icon"] or "green" in calls["icon"]
