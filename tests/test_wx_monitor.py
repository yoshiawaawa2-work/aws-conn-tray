import importlib.util
import types

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


def test_start_sso_login_webview_fallback(monkeypatch):
    # open_pywebview_authorize が False を返した場合にブラウザフォールバックと通知が呼ばれるか
    called = {}
    monkeypatch.setattr(wx_tray, "open_pywebview_authorize", lambda url: False)
    monkeypatch.setattr(wx_tray, "webbrowser", types.SimpleNamespace(open=lambda url: called.setdefault("opened", url)))

    def fake_notify(title, message, on_click=None):
        called["notify"] = (title, message)
        if on_click:
            on_click()

    monkeypatch.setattr(wx_tray, "notify", fake_notify)

    ok = wx_tray.start_sso_login_webview("https://example.com/auth")
    assert ok is True
    assert called.get("opened") == "https://example.com/auth"
    assert called.get("notify")[0] == "SSO ログイン"


def test_parse_authorize_url():
    text = "Please visit: https://oidc.example.com/authorize?code=ABC\nYour code is 1A2B-3C4D."
    url, code = wx_tray._parse_authorize_url(text)
    assert "authorize" in url
    assert code == "1A2B-3C4D"


def test_notify_token_invalid_message(monkeypatch):
    # トークン無効時のメッセージがメニュー誘導になっているか確認
    called = {}
    monkeypatch.setattr(wx_tray, "notify", lambda t, m, on_click=None: called.update(title=t, message=m, on_click=on_click))

    app = wx_tray.WxTrayApp.__new__(wx_tray.WxTrayApp)  # App生成を避けるため __new__ で裸のインスタンス
    app.config = type("C", (), {"profile": "p", "region": "r", "tunnel_check_interval_seconds": 1, "sso_auto_refresh_margin_seconds": 300})()
    app.tray_icon = type("T", (), {"set_icon_color": lambda self, c: None})()
    app.ssm = None
    app._refreshing = False
    app._notified_invalid = False

    monkeypatch.setattr(wx_tray, "check_sso_token", lambda: (False, -1))
    monkeypatch.setattr(wx_tray, "is_sso_session_valid", lambda p, r: False)

    wx_tray.WxTrayApp.monitor_once(app)
    assert called["title"] == "SSO トークンが無効です"
    assert "メニューの「SSO ログイン」" in called["message"]
    assert called["on_click"] is None


def test_is_request_approved():
    assert wx_tray._is_request_approved("", "http://127.0.0.1:12345/oauth/callback?code=abc", "") is True
    assert wx_tray._is_request_approved("", "https://127.0.0.1/oauth/callback?code=xyz", "") is True
    assert wx_tray._is_request_approved("", "https://example.com/ok", "") is False


def test_open_pywebview_authorize(monkeypatch):
    # pywebview.create_window が呼ばれ、storage_path または user_data_dir が指定されることを確認
    # navigation/loaded イベントで destroy が呼ばれることを確認（プロセスは同期実行に置き換え）
    created = {}

    def fake_create_window(title, url, width=0, height=0, storage_path=None, **kwargs):
        # storage_path を持つシグネチャにしておく
        if storage_path is not None:
            kwargs.setdefault("storage_path", storage_path)
        class EventList(list):
            def __iadd__(self, other):
                # pywebview はコールバックを追加する形。ここでは callable を単体で受け付ける
                if callable(other):
                    self.append(other)
                    return self
                return super().__iadd__(other)
        class Events:
            def __init__(self):
                self.loaded = EventList()
                self.navigation = EventList()
        class Win:
            def __init__(self):
                self._destroyed = False
                self.events = Events()
                self._url = url
            def get_current_url(self):
                return self._url
            def destroy(self):
                self._destroyed = True
        win = Win()
        created.update(title=title, url=url, width=width, height=height, kwargs=kwargs, win=win)
        return win
    def fake_start():
        created["started"] = True

    class DummyProc:
        def __init__(self, target, daemon):
            self.target = target
        def start(self):
            self.target()

    monkeypatch.setattr(wx_tray, "webview", types.SimpleNamespace(create_window=fake_create_window, start=fake_start))
    monkeypatch.setattr(wx_tray.multiprocessing, "Process", DummyProc)

    ok = wx_tray.open_pywebview_authorize("https://example.com/auth", storage_dir="/tmp/test-webview")
    assert ok is True
    assert created["url"] == "https://example.com/auth"
    # storage_path または user_data_dir のどちらかで永続化が設定される
    assert any(k in created["kwargs"] for k in ("storage_path", "user_data_dir"))
    assert created.get("started") is True
    # イベントが登録され、URL が callback を含むと destroy が呼ばれることを確認
    for handler in created["win"].events.loaded + getattr(created["win"].events, "navigation", []):
        # simulate navigation to callback URL
        created["win"]._url = "http://127.0.0.1:1234/oauth/callback?code=abc"
        handler(None)
    assert created["win"]._destroyed is True


def _bare_app():
    app = wx_tray.WxTrayApp.__new__(wx_tray.WxTrayApp)
    app.tray_icon = type("T", (), {"set_icon_color": lambda self, c: None})()
    app.ssm = None
    app._refreshing = False
    app._notified_invalid = False
    return app


def test_monitor_triggers_refresh_when_margin(monkeypatch):
    app = _bare_app()
    app.config = type(
        "C",
        (),
        {
            "profile": "p",
            "region": "r",
            "tunnel_check_interval_seconds": 1,
            "sso_auto_refresh_margin_seconds": 300,
        },
    )()
    called = {}
    monkeypatch.setattr(wx_tray, "check_sso_token", lambda: (True, 100))
    monkeypatch.setattr(wx_tray, "is_sso_session_valid", lambda p, r: True)
    monkeypatch.setattr(wx_tray.WxTrayApp, "_start_sso_refresh", lambda self: called.setdefault("refresh", True))
    wx_tray.WxTrayApp.monitor_once(app)
    assert called.get("refresh") is True


def test_refresh_webview_fallback(monkeypatch):
    app = _bare_app()
    app.config = type(
        "C",
        (),
        {
            "profile": "p",
            "region": "r",
            "sso_start_url": "https://start.example.com",
            "sso_login_timeout_seconds": 10,
        },
    )()
    called = {}

    def fake_thread(target, daemon):
        class Dummy:
            def start(self_inner):
                target()
        return Dummy()

    monkeypatch.setattr(wx_tray.threading, "Thread", fake_thread)
    monkeypatch.setattr(wx_tray, "_fetch_authorize_url", lambda *a, **k: (None, None))
    monkeypatch.setattr(wx_tray, "start_sso_login_webview", lambda url: False)
    monkeypatch.setattr(wx_tray, "notify", lambda t, m, on_click=None: called.setdefault("notify", (t, m)))
    wx_tray.WxTrayApp._start_sso_refresh(app)
    assert called["notify"][0].startswith("SSO トークン更新")


def test_ssm_reconnect_and_icon(monkeypatch):
    app = _bare_app()
    calls = {"icon": []}

    class DummySsm:
        def __init__(self, alive):
            self._alive = alive
            self.starts = 0
        def is_alive(self):
            return self._alive
        def start(self):
            self.starts += 1
            self._alive = True

    app.ssm = DummySsm(alive=False)
    app.tray_icon = type("T", (), {"set_icon_color": lambda self, c: calls["icon"].append(c)})()
    app.config = type(
        "C",
        (),
        {
            "profile": "p",
            "region": "r",
            "tunnel_check_interval_seconds": 1,
            "sso_auto_refresh_margin_seconds": 300,
        },
    )()
    monkeypatch.setattr(wx_tray, "check_sso_token", lambda: (True, 1000))
    monkeypatch.setattr(wx_tray, "is_sso_session_valid", lambda p, r: True)
    wx_tray.WxTrayApp.monitor_once(app)
    assert app.ssm.starts == 1
    assert "yellow" in calls["icon"] or "green" in calls["icon"]

    # トンネルが生きている場合は緑
    app.ssm._alive = True
    calls["icon"].clear()
    wx_tray.WxTrayApp.monitor_once(app)
    assert "green" in calls["icon"]
