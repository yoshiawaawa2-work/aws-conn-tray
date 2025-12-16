import argparse
from types import SimpleNamespace

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
