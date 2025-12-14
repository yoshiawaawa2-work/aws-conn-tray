import importlib.util
import time

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

    def fake_check(*args, **kwargs):
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
    # open_qt_webview_authorize が False を返した場合にミニダイアログ経由のブラウザ誘導になるか
    called_prompt = {}
    stub_app = type("App", (), {})()
    stub_app._tray_app = type("Tray", (), {"_show_browser_prompt": lambda self, url, msg: called_prompt.setdefault("msg", msg)})()
    monkeypatch.setattr(wx_tray.wx, "GetApp", lambda: stub_app)
    monkeypatch.setattr(wx_tray, "open_qt_webview_authorize", lambda url, storage_dir=None, on_fail=None: False)
    monkeypatch.setattr(wx_tray, "notify", lambda *a, **k: called_prompt.setdefault("notify", True))

    ok = wx_tray.start_sso_login_webview("https://example.com/auth")
    assert ok is False
    assert "msg" in called_prompt
    assert "WebView を開けませんでした" in called_prompt["msg"]


def test_qt_webview_crash_triggers_browser_fallback(monkeypatch):
    # Qt WebView プロセスがクラッシュ(rc=-11など)したときにブラウザフォールバック誘導が呼ばれるか
    called_prompt = {}

    class DummyProc:
        def __init__(self):
            self._rc = None
            self.returncode = None
        def poll(self):
            return self._rc
        def communicate(self, timeout=None):
            time.sleep(0.6)  # allow main thread to pass early-exit check
            self._rc = -11
            self.returncode = -11
            return ("", "segfault")

    def fake_popen(args, stdout=None, stderr=None, text=None, env=None):
        return DummyProc()

    monkeypatch.setattr(wx_tray.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(wx_tray, "log_message", lambda *a, **k: None)
    monkeypatch.setattr(wx_tray, "cleanup_qt_webview_procs", lambda force=False: None)
    monkeypatch.setattr(wx_tray.webbrowser, "open", lambda url: called_prompt.setdefault("opened", url))
    stub_app = type("App", (), {})()
    stub_app._tray_app = type("Tray", (), {"_show_browser_prompt": lambda self, url, msg: called_prompt.setdefault("msg", msg)})()
    monkeypatch.setattr(wx_tray.wx, "GetApp", lambda: stub_app)

    ok = wx_tray.open_qt_webview_authorize("https://example.com/auth", storage_dir="/tmp/webview", on_fail=lambda: stub_app._tray_app._show_browser_prompt("https://example.com/auth", "fallback"))

    assert ok is True
    time.sleep(0.8)
    assert "msg" in called_prompt


def test_wrap_text_for_prompt():
    from aws_rds_tray.wx_tray import _wrap_text_for_prompt

    msg = "これはとても長い日本語の文面です ブラウザ フォールバック を 促します"
    wrapped = _wrap_text_for_prompt(msg, width=20)
    lines = wrapped.splitlines()
    assert all(len(line) <= 20 for line in lines)
    # 英単語は途中で切られない
    assert any("フォールバック" in line for line in lines)


def test_show_prompts_wrap_text(monkeypatch):
    app = wx_tray.WxTrayApp.__new__(wx_tray.WxTrayApp)
    app._mini_prompt = None
    app._webview_running = False
    called = {}

    # スタブ MiniPrompt で渡されたテキストを記録
    class DummyPrompt:
        def __init__(self, message, on_click=None, button_label="開く"):
            called["message"] = message
            called["button"] = button_label
        def show_bottom_right(self):
            called["shown"] = True

    monkeypatch.setattr(wx_tray, "MiniPrompt", DummyPrompt)
    monkeypatch.setattr(wx_tray, "_wrap_text_for_prompt", lambda msg, width=20: f"WRAP:{msg}")
    # _launch_webview をスタブ
    app._launch_webview = lambda url: called.setdefault("launched", url)

    app._show_sso_prompt("https://auth", "これは長い文言です")
    assert called.get("message", "").startswith("WRAP:")
    assert called.get("button") == "開く"

    called.clear()
    app._show_browser_prompt("https://auth", "ブラウザで開きます")
    assert called.get("message", "").startswith("WRAP:")
    assert called.get("button") == "開く"




def test_on_sso_login_uses_webview(monkeypatch):
    # メニュー「SSO ログイン」で認可URLを取得し、_launch_webview が呼ばれること
    app = _bare_app()
    app.config = type(
        "C",
        (),
        {
            "profile": "p",
            "region": "r",
            "sso_start_url": "https://start.example.com",
            "sso_login_timeout_seconds": 30,
        },
    )()

    called = {}
    app._launch_webview = lambda url: called.setdefault("url", url)
    monkeypatch.setattr(wx_tray, "notify", lambda *a, **k: called.setdefault("notified", True))

    app.on_sso_login()
    assert called.get("url") == "https://start.example.com"
    assert called.get("notified") is True


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
    app.config = type(
        "C",
        (),
            {
                "profile": "p",
                "region": "r",
                "tunnel_check_interval_seconds": 1,
                "sso_auto_refresh_margin_seconds": 300,
                "sso_start_url": "https://example.com/start",
                "local_port": 1111,
            },
        )()
    app.tray_icon = type("T", (), {"set_icon_color": lambda self, c: None})()
    app.ssm = None
    app._refreshing = False
    app._notified_invalid = False
    called_prompt = {}
    monkeypatch.setattr(wx_tray.WxTrayApp, "_show_sso_prompt", lambda self, url, msg: called_prompt.setdefault("msg", msg))

    monkeypatch.setattr(wx_tray, "check_sso_token", lambda *a, **k: (False, -1))
    monkeypatch.setattr(wx_tray, "is_sso_session_valid", lambda p, r: False)

    wx_tray.WxTrayApp.monitor_once(app)
    # ミニダイアログ誘導が呼ばれていること
    assert called_prompt["msg"].startswith("SSO トークンが無効です")


def test_is_request_approved():
    assert wx_tray._is_request_approved("", "http://127.0.0.1:12345/oauth/callback?code=abc", "") is True
    assert wx_tray._is_request_approved("", "https://127.0.0.1/oauth/callback?code=xyz", "") is True
    assert wx_tray._is_request_approved("", "https://example.com/ok", "") is False


def test_open_pywebview_authorize(monkeypatch):
    # Qt WebView サブプロセスの起動コマンドを組み立てているか確認
    created = {}

    class DummyPopen:
        def __init__(self, args, stdout=None, stderr=None, **kwargs):
            created["args"] = args
            created["env"] = kwargs.get("env")
            self._rc = None
        def poll(self):
            return self._rc
        def communicate(self, timeout=None):
            return ("", "")
        def __enter__(self):  # not used
            return self

    monkeypatch.setattr(wx_tray.subprocess, "Popen", DummyPopen)

    ok = wx_tray.open_qt_webview_authorize("https://example.com/auth", storage_dir="/tmp/test-webview")
    assert ok is True
    cmd = created["args"]
    assert "-m" in cmd and "aws_rds_tray.qwebview_runner" in cmd
    assert "/tmp/test-webview" in cmd
    env = created["env"]
    assert env is not None
    # デフォルトで GPU/VA-API/Sandbox を無効化し、X11 + ソフトウェアレンダを強制する
    flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS")
    assert "--disable-gpu" in flags
    assert "--disable-features=VaapiVideoDecoder" in flags
    assert "--disable-dev-shm-usage" in flags
    assert "--no-sandbox" not in flags
    assert env.get("LIBVA_DRIVER_NAME") == "mesa"
    assert env.get("LIBGL_ALWAYS_SOFTWARE") == "1"
    assert env.get("QTWEBENGINE_DISABLE_SANDBOX") == "1"
    assert env.get("QT_QUICK_BACKEND") == "software"
    assert env.get("QT_QPA_PLATFORM") == "xcb"
    assert env.get("QT_OPENGL") == "software"


def _bare_app():
    app = wx_tray.WxTrayApp.__new__(wx_tray.WxTrayApp)
    app.tray_icon = type("T", (), {"set_icon_color": lambda self, c: None})()
    app.ssm = None
    app._refreshing = False
    app._notified_invalid = False
    app._mini_prompt = None
    app._show_sso_prompt = lambda *a, **k: None  # wx.App 未生成環境での PyNoAppError 回避
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
            "sso_start_url": "https://example.com/start",
            "local_port": 1111,
        },
    )()
    called = {}
    monkeypatch.setattr(wx_tray, "check_sso_token", lambda *a, **k: (True, 100))
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
    called = {"opened": 0, "prompt": None}

    def fake_thread(target, daemon):
        class Dummy:
            def start(self_inner):
                target()
        return Dummy()

    monkeypatch.setattr(wx_tray.threading, "Thread", fake_thread)
    # URL 取得成功 → 自動で WebView を開く
    monkeypatch.setattr(wx_tray, "_fetch_authorize_url", lambda *a, **k: ("https://auth.example.com/authorize", None))
    def fake_start(url):
        called["opened"] += 1
        return True
    monkeypatch.setattr(wx_tray, "start_sso_login_webview", fake_start)
    # CallAfter で UI 呼び出しを記録し、即時実行
    def fake_callafter(fn, *a, **k):
        called["callafter"] = True
        fn()
    monkeypatch.setattr(wx_tray.wx, "CallAfter", fake_callafter)
    monkeypatch.setattr(wx_tray.wx, "GetApp", lambda: object())
    app._show_sso_prompt = lambda *a, **k: called.setdefault("prompt", True)

    wx_tray.WxTrayApp._start_sso_refresh(app)
    assert called["opened"] == 1
    assert called["prompt"] is None  # 成功時はダイアログ不要
    assert called.get("callafter") is True


def test_refresh_webview_fetch_fail(monkeypatch):
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
    def fake_prompt(url, msg, btn="開く"):
        called.setdefault("prompt", (url, msg, btn))
    app._show_sso_prompt = fake_prompt
    def fake_callafter(fn, *a, **k):
        called["callafter"] = True
        fn()
    monkeypatch.setattr(wx_tray.wx, "CallAfter", fake_callafter)
    monkeypatch.setattr(wx_tray.wx, "GetApp", lambda: object())

    wx_tray.WxTrayApp._start_sso_refresh(app)
    assert "prompt" in called  # 認可URL取得失敗で再試行ダイアログが呼ばれる
    assert called.get("callafter") is True


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
            "sso_start_url": "https://example.com/start",
            "local_port": 1111,
        },
    )()
    monkeypatch.setattr(wx_tray, "check_sso_token", lambda *a, **k: (True, 1000))
    monkeypatch.setattr(wx_tray, "is_sso_session_valid", lambda p, r: True)
    wx_tray.WxTrayApp.monitor_once(app)
    assert app.ssm.starts == 1
    assert "yellow" in calls["icon"] or "green" in calls["icon"]

    # トンネルが生きている場合は緑
    app.ssm._alive = True
    calls["icon"].clear()
    wx_tray.WxTrayApp.monitor_once(app)
    assert "green" in calls["icon"]
