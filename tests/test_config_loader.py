import json
from pathlib import Path

import pytest

from aws_rds_tray import config_loader


def test_load_config_reads_all_fields(monkeypatch, tmp_path: Path):
    cfg_path = tmp_path / "config.json"
    data = {
        "instance_id": "i-123",
        "profile": "prof",
        "region": "ap-northeast-1",
        "local_port": 1111,
        "remote_host": "db.example.com",
        "remote_port": 2222,
        "sso_start_url": "https://example.com/start",
        "sso_login_timeout_seconds": 10,
        "sso_auto_refresh_margin_seconds": 20,
        "tunnel_check_interval_seconds": 30,
    }
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config_loader, "CONFIG_PATH", str(cfg_path))

    cfg = config_loader.load_config()

    assert cfg.instance_id == "i-123"
    assert cfg.profile == "prof"
    assert cfg.region == "ap-northeast-1"
    assert cfg.local_port == 1111
    assert cfg.remote_host == "db.example.com"
    assert cfg.remote_port == 2222
    assert cfg.sso_start_url == "https://example.com/start"
    assert cfg.sso_login_timeout_seconds == 10
    assert cfg.sso_auto_refresh_margin_seconds == 20
    assert cfg.tunnel_check_interval_seconds == 30


def test_load_config_missing_file(monkeypatch):
    monkeypatch.setattr(config_loader, "CONFIG_PATH", "/no/such/file/config.json")
    with pytest.raises(FileNotFoundError):
        config_loader.load_config()


def test_load_config_uses_defaults(monkeypatch, tmp_path: Path):
    cfg_path = tmp_path / "config.json"
    # optional fields are omitted; loader should fill defaults
    data = {
        "instance_id": "i-123",
        "profile": "prof",
        "region": "ap-northeast-1",
        "local_port": 1111,
        "remote_port": 2222,
        "sso_start_url": "https://example.com/start",
    }
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config_loader, "CONFIG_PATH", str(cfg_path))

    cfg = config_loader.load_config()

    assert cfg.remote_host == ""
    assert cfg.sso_login_timeout_seconds == 900
    assert cfg.sso_auto_refresh_margin_seconds == 300
    assert cfg.tunnel_check_interval_seconds == 10
