from types import SimpleNamespace

import tray_app


def test_settings_only_saves(monkeypatch):
    called = {}

    class DummyLock:
        def __init__(self, *a, **k):
            pass

        def acquire(self):
            return True

        def release(self):
            called["released"] = True

    monkeypatch.setattr(tray_app, "SingleInstanceLock", DummyLock)
    monkeypatch.setattr(tray_app, "ensure_supported_os", lambda: None)
    monkeypatch.setattr(tray_app, "notify", lambda *a, **k: called.setdefault("notify", True))
    monkeypatch.setattr(tray_app, "_parse_args", lambda argv=None: SimpleNamespace(settings=True))
    monkeypatch.setattr(tray_app, "load_config", lambda: {"forwards": []})

    dummy_wx_tray = SimpleNamespace()
    dummy_wx_tray._load_example_config = lambda: {"remote_port": 1, "local_port": 2, "sso_login_timeout_seconds": 1, "sso_auto_refresh_margin_seconds": 1, "tunnel_check_interval_seconds": 1}
    dummy_wx_tray._config_to_dict = lambda cfg: {"forwards": []}
    dummy_wx_tray.validate_settings_input = lambda data, defaults: (True, [], data)
    dummy_wx_tray._save_config = lambda data: called.setdefault("saved", data)

    class DummyApp:
        def __init__(self):
            pass

        def _show_settings_dialog(self, current, defaults):
            called["dialog"] = True
            return {
                "forwards": [],
                "sso_start_url": "https://example.com",
                "sso_login_timeout_seconds": 1,
                "sso_auto_refresh_margin_seconds": 1,
                "tunnel_check_interval_seconds": 1,
            }

    dummy_wx_tray.WxTrayApp = DummyApp
    monkeypatch.setattr(tray_app, "wx_tray", dummy_wx_tray, raising=False)
    monkeypatch.setattr(tray_app, "_is_main_running", lambda: False)

    tray_app.main([])

    assert "dialog" in called
    assert called.get("saved") is not None
    assert called.get("released") is True


def test_settings_only_cancel(monkeypatch):
    called = {}

    class DummyLock:
        def __init__(self, *a, **k):
            pass

        def acquire(self):
            return True

        def release(self):
            called["released"] = True

    monkeypatch.setattr(tray_app, "SingleInstanceLock", DummyLock)
    monkeypatch.setattr(tray_app, "ensure_supported_os", lambda: None)
    monkeypatch.setattr(tray_app, "notify", lambda *a, **k: called.setdefault("notify", True))
    monkeypatch.setattr(tray_app, "_parse_args", lambda argv=None: SimpleNamespace(settings=True))
    monkeypatch.setattr(tray_app, "load_config", lambda: {"forwards": []})

    dummy_wx_tray = SimpleNamespace()
    dummy_wx_tray._load_example_config = lambda: {}
    dummy_wx_tray._config_to_dict = lambda cfg: {"forwards": []}
    dummy_wx_tray.validate_settings_input = lambda data, defaults: (True, [], data)
    dummy_wx_tray._save_config = lambda data: called.setdefault("saved", data)

    class DummyApp:
        def __init__(self):
            pass

        def _show_settings_dialog(self, current, defaults):
            called["dialog"] = True
            return None  # cancel/close

    dummy_wx_tray.WxTrayApp = DummyApp
    monkeypatch.setattr(tray_app, "wx_tray", dummy_wx_tray, raising=False)
    monkeypatch.setattr(tray_app, "_is_main_running", lambda: False)

    tray_app.main([])

    assert "dialog" in called
    assert "saved" not in called
    assert called.get("released") is True


def test_settings_only_restart_when_running(monkeypatch):
    called = {}

    class DummyLock:
        def __init__(self, *a, **k):
            pass

        def acquire(self):
            return True

        def release(self):
            called["released"] = True

    monkeypatch.setattr(tray_app, "SingleInstanceLock", DummyLock)
    monkeypatch.setattr(tray_app, "ensure_supported_os", lambda: None)
    monkeypatch.setattr(tray_app, "notify", lambda *a, **k: called.setdefault("notify", True))
    monkeypatch.setattr(tray_app, "_parse_args", lambda argv=None: SimpleNamespace(settings=True))
    monkeypatch.setattr(tray_app, "load_config", lambda: {"forwards": []})
    monkeypatch.setattr(tray_app, "_is_main_running", lambda: True)

    dummy_wx_tray = SimpleNamespace()
    dummy_wx_tray._load_example_config = lambda: {"remote_port": 1, "local_port": 2, "sso_login_timeout_seconds": 1, "sso_auto_refresh_margin_seconds": 1, "tunnel_check_interval_seconds": 1}
    dummy_wx_tray._config_to_dict = lambda cfg: {"forwards": []}
    dummy_wx_tray.validate_settings_input = lambda data, defaults: (True, [], data)
    dummy_wx_tray._save_config = lambda data: called.setdefault("saved", data)

    class DummyApp:
        def __init__(self):
            pass

        def _show_settings_dialog(self, current, defaults):
            called["dialog"] = True
            return {
                "forwards": [],
                "sso_start_url": "https://example.com",
                "sso_login_timeout_seconds": 1,
                "sso_auto_refresh_margin_seconds": 1,
                "tunnel_check_interval_seconds": 1,
            }

    dummy_wx_tray.WxTrayApp = DummyApp
    monkeypatch.setattr(tray_app, "wx_tray", dummy_wx_tray, raising=False)

    restarted = {}
    monkeypatch.setattr(tray_app, "_restart_running_app", lambda: restarted.setdefault("called", True))

    tray_app.main([])

    assert "dialog" in called
    assert called.get("saved") is not None
    assert called.get("released") is True
    assert restarted.get("called") is True
