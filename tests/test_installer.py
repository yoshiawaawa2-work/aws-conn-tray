import argparse
from types import SimpleNamespace

import pytest

import installer_ui


def test_run_headless_preview(monkeypatch, tmp_path):
    def fake_perform(opts, preview, config_data=None):
        return SimpleNamespace(ok=True, issues=[])

    monkeypatch.setattr(installer_ui, "perform", fake_perform)
    args = argparse.Namespace(
        preview=True,
        headless=True,
        install_dir=str(tmp_path),
        config=None,
        no_autostart=False,
        auto_install_missing=False,
    )
    rc = installer_ui.run_headless(args)
    assert rc == 0


def test_build_config_data(monkeypatch):
    # validate_settings_input を通過することを確認
    forwards = [
        {
            "name": "rds",
            "instance_id": "i-1",
            "profile": "p",
            "region": "r",
            "remote_host": "h",
            "remote_port": "5432",
            "local_port": "15432",
        }
    ]
    globals_raw = {
        "sso_start_url": "https://start",
        "sso_login_timeout_seconds": "900",
        "sso_auto_refresh_margin_seconds": "300",
        "tunnel_check_interval_seconds": "10",
    }
    cfg = installer_ui.build_config_data(forwards, globals_raw)
    assert cfg["forwards"][0]["local_port"] == 15432


def test_build_config_data_invalid_port():
    forwards = [
        {
            "name": "rds",
            "instance_id": "i-1",
            "profile": "p",
            "region": "r",
            "remote_host": "h",
            "remote_port": "5432",
            "local_port": "bad",
        }
    ]
    globals_raw = {
        "sso_start_url": "https://start",
        "sso_login_timeout_seconds": "900",
        "sso_auto_refresh_margin_seconds": "300",
        "tunnel_check_interval_seconds": "10",
    }
    with pytest.raises(ValueError):
        installer_ui.build_config_data(forwards, globals_raw)


def test_next_step_updates_confirm(monkeypatch):
    # InstallerWizard を最小限のダミー状態で検証
    class DummyVar:
        def __init__(self, v):
            self.v = v

        def get(self):
            return self.v

    wiz = installer_ui.InstallerWizard.__new__(installer_ui.InstallerWizard)
    wiz.state = {"globals": {}}
    wiz.current_step = 2  # 設定画面から確認画面に進むケース
    wiz.frames = [None, None, None, None, None, None]

    wiz.forward_frames = [
        {
            "vars": {
                "name": DummyVar("rds"),
                "instance_id": DummyVar("i-1"),
                "profile": DummyVar("p"),
                "region": DummyVar("r"),
                "remote_host": DummyVar("h"),
                "remote_port": DummyVar("5432"),
                "local_port": DummyVar("15432"),
            }
        }
    ]
    wiz.global_vars = {
        "sso_start_url": DummyVar("https://start"),
        "sso_login_timeout_seconds": DummyVar("900"),
        "sso_auto_refresh_margin_seconds": DummyVar("300"),
        "tunnel_check_interval_seconds": DummyVar("10"),
    }
    called = {"confirm": False, "show": None}
    wiz._update_confirm = lambda: called.__setitem__("confirm", True)
    wiz._start_install = lambda: None
    wiz._show_step = lambda idx: called.__setitem__("show", idx)

    installer_ui.InstallerWizard.next_step(wiz)
    # 確認画面に遷移し、_update_confirm が呼ばれていること
    assert called["confirm"] is True
    assert called["show"] == 3


def test_nav_label_changes_to_install():
    class DummyBtn:
        def __init__(self):
            self.kwargs = {}

        def config(self, **kwargs):
            self.kwargs.update(kwargs)

    wiz = installer_ui.InstallerWizard.__new__(installer_ui.InstallerWizard)
    wiz.frames = [None] * 6
    wiz.current_step = 3
    wiz.back_button = DummyBtn()
    wiz.next_button = DummyBtn()
    installer_ui.InstallerWizard._update_nav_labels(wiz)
    assert wiz.next_button.kwargs.get("text") == "インストール"


def test_next_step_blocks_when_precheck_failed(monkeypatch):
    calls = {}

    class DummyBtn:
        def __init__(self):
            self.kwargs = {}

        def config(self, **kwargs):
            self.kwargs.update(kwargs)

    wiz = installer_ui.InstallerWizard.__new__(installer_ui.InstallerWizard)
    wiz.current_step = 0
    wiz.state = {"precheck_ok": False, "globals": {}}
    wiz.frames = [None, None]
    wiz.back_button = DummyBtn()
    wiz.next_button = DummyBtn()
    wiz._update_nav_labels = lambda: None
    wiz._show_step = lambda idx: calls.setdefault("show", idx)
    monkeypatch.setattr(installer_ui, "messagebox", type("MB", (), {"showerror": lambda *a, **k: calls.setdefault("error", True)}))

    installer_ui.InstallerWizard.next_step(wiz)
    assert calls.get("error") is True
    assert "show" not in calls  # 遷移しない


def test_nav_disabled_when_precheck_failed():
    class DummyBtn:
        def __init__(self):
            self.kwargs = {}

        def config(self, **kwargs):
            self.kwargs.update(kwargs)

    wiz = installer_ui.InstallerWizard.__new__(installer_ui.InstallerWizard)
    wiz.frames = [None, None]
    wiz.current_step = 0
    wiz.state = {"precheck_ok": False}
    wiz.back_button = DummyBtn()
    wiz.next_button = DummyBtn()
    installer_ui.InstallerWizard._update_nav_labels(wiz)
    assert wiz.next_button.kwargs.get("state") == "disabled"


def test_simulate_precheck_fail_flag(monkeypatch):
    wiz = installer_ui.InstallerWizard.__new__(installer_ui.InstallerWizard)
    wiz.simulate_precheck_fail = True
    wiz.state = {"precheck_ok": True, "precheck_issues": [], "precheck_suggested": []}
    wiz.sso_profile_var = type("Var", (), {"get": lambda self=None: ""})()
    wiz.precheck_text = type("Dummy", (), {})()
    wiz.precheck_text.configure = lambda **kwargs: None
    wiz.precheck_text.delete = lambda *a, **k: None
    wiz.precheck_text.insert = lambda *a, **k: None
    wiz._update_nav_labels = lambda: None
    installer_ui.InstallerWizard._run_precheck(wiz)
    assert wiz.state["precheck_ok"] is False
    assert "(テスト用) simulate_precheck_fail" in wiz.state["precheck_issues"][0]
    assert wiz.state["precheck_suggested"]  # 推奨コマンドが入る


def test_ensure_supported_os(monkeypatch):
    args = SimpleNamespace(allow_unsupported_os=False)
    monkeypatch.setattr(installer_ui.installer_core.platforms, "is_windows", lambda: False)
    monkeypatch.setattr(installer_ui.installer_core.platforms, "is_macos", lambda: False)
    monkeypatch.setattr(installer_ui.installer_core.platforms, "is_linux", lambda: False)
    assert installer_ui._ensure_supported_os(args) is False


def test_simulate_unsupported_os(monkeypatch):
    args = SimpleNamespace(allow_unsupported_os=False, simulate_unsupported_os=True)
    monkeypatch.setattr(installer_ui.installer_core.platforms, "is_windows", lambda: True)
    # simulate flag should still force unsupported
    assert installer_ui._ensure_supported_os(args) is False


def test_next_step_blocks_sso_not_logged_in(monkeypatch):
    calls = {}

    class DummyBtn:
        def __init__(self):
            self.kwargs = {}

        def config(self, **kwargs):
            self.kwargs.update(kwargs)

    wiz = installer_ui.InstallerWizard.__new__(installer_ui.InstallerWizard)
    wiz.current_step = 0
    wiz.state = {"precheck_ok": True, "precheck_issues": [], "precheck_suggested": [], "sso_profile": "p", "sso_login_ok": False}
    wiz.sso_profile_var = type("Var", (), {"get": lambda self=None: "p"})()
    wiz.precheck_text = type("Dummy", (), {})()
    wiz.precheck_text.configure = lambda **kwargs: None
    wiz.precheck_text.delete = lambda *a, **k: None
    wiz.precheck_text.insert = lambda *a, **k: None
    wiz._update_nav_labels = lambda: None
    wiz._show_step = lambda idx: calls.setdefault("show", idx)
    wiz.back_button = DummyBtn()
    wiz.next_button = DummyBtn()
    monkeypatch.setattr(installer_ui, "messagebox", type("MB", (), {"showerror": lambda *a, **k: calls.setdefault("error", True)}))

    installer_ui.InstallerWizard.next_step(wiz)
    assert calls.get("error") is True
    assert "show" not in calls


def test_nav_disabled_when_sso_not_logged_in(monkeypatch):
    class DummyBtn:
        def __init__(self):
            self.kwargs = {}

        def config(self, **kwargs):
            self.kwargs.update(kwargs)

    wiz = installer_ui.InstallerWizard.__new__(installer_ui.InstallerWizard)
    wiz.frames = [None, None]
    wiz.current_step = 0
    wiz.state = {"precheck_ok": True, "sso_login_ok": False}
    wiz.back_button = DummyBtn()
    wiz.next_button = DummyBtn()
    installer_ui.InstallerWizard._update_nav_labels(wiz)
    assert wiz.next_button.kwargs.get("state") == "disabled"


def test_get_sso_profiles_handles_percent(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.write_text(
        "\n".join(
            [
                "[profile test]",
                "sso_start_url=https://example/",
                "sso_region=us-east-1",
                "credential_process=/bin/echo %s",
            ]
        ),
        encoding="utf-8",
    )
    profs = installer_ui._get_sso_profiles(cfg)
    assert "test" in profs


def test_get_sso_profiles_with_session_reference(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text(
        "\n".join(
            [
                "[profile p1]",
                "sso_session = default",
                "region = ap-northeast-1",
                "",
                "[sso-session default]",
                "sso_start_url = https://start",
                "sso_region = ap-northeast-1",
            ]
        ),
        encoding="utf-8",
    )
    profs = installer_ui._get_sso_profiles(cfg)
    assert "p1" in profs


def test_load_sso_profile_config_with_session(tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text(
        "\n".join(
            [
                "[profile PS-Admin-snapvalue-jp]",
                "sso_session = default",
                "sso_account_id = 061051222627",
                "sso_role_name = PS-Admin",
                "region = ap-northeast-1",
                "",
                "[default]",
                "sso_session = default",
                "sso_account_id = 061051222627",
                "sso_role_name = PS-Admin",
                "region = ap-northeast-1",
                "",
                "[sso-session default]",
                "sso_start_url = https://example",
                "sso_region = ap-northeast-1",
            ]
        ),
        encoding="utf-8",
    )
    cfg_data = installer_ui._load_sso_profile_config("PS-Admin-snapvalue-jp", cfg)
    assert cfg_data["sso_start_url"] == "https://example"
    assert cfg_data["sso_region"] == "ap-northeast-1"
    assert cfg_data["sso_account_id"] == "061051222627"
    assert cfg_data["sso_role_name"] == "PS-Admin"


def test_sso_required_missing():
    vals_ok = {
        "profile": "p",
        "sso_start_url": "u",
        "sso_region": "r",
        "sso_account_id": "a",
        "sso_role_name": "role",
    }
    assert installer_ui._sso_required_missing(vals_ok) == []
    vals_ng = vals_ok.copy()
    vals_ng["sso_region"] = ""
    missing = installer_ui._sso_required_missing(vals_ng)
    assert "SSO リージョン" in missing


def test_apply_sso_to_ui_updates_fields():
    class DummyVar:
        def __init__(self, v=""):
            self.v = v

        def get(self):
            return self.v

        def set(self, v):
            self.v = v

    ff = {"vars": {"profile": DummyVar("old"), "region": DummyVar("")}}
    globals_vars = {"sso_start_url": DummyVar("")}
    installer_ui._apply_sso_to_ui([ff], globals_vars, "newprof", "ap-northeast-1", "https://start")
    assert ff["vars"]["profile"].get() == "newprof"
    assert ff["vars"]["region"].get() == "ap-northeast-1"
    assert globals_vars["sso_start_url"].get() == "https://start"


def test_on_profile_change_resets_login(monkeypatch):
    class DummyBtn:
        def __init__(self):
            self.kwargs = {}

        def config(self, **kwargs):
            self.kwargs.update(kwargs)

    wiz = installer_ui.InstallerWizard.__new__(installer_ui.InstallerWizard)
    wiz.frames = [None, None]
    wiz.current_step = 0
    wiz.state = {"precheck_ok": True, "sso_login_ok": True}
    wiz.forward_frames = [{"profile": None, "region": None}]
    wiz.global_vars = {"sso_start_url": None}
    wiz.back_button = DummyBtn()
    wiz.next_button = DummyBtn()
    wiz.sso_status_var = None
    wiz.sso_profile_var = type("Var", (), {"get": lambda self=None: "p"})()
    installer_ui.InstallerWizard._on_profile_change(wiz)
    assert wiz.state["sso_login_ok"] is False
