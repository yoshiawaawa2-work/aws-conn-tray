import json
from pathlib import Path

import pytest

from aws_conn_tray import config_loader
from aws_conn_tray.config_loader import ForwardTarget


def _example_forward(name="rds"):
    return {
        "name": name,
        "instance_id": "i-123",
        "profile": "prof",
        "region": "ap-northeast-1",
        "local_port": 1111,
        "remote_host": "db.example.com",
        "remote_port": 2222,
    }


def test_load_config_reads_all_fields(monkeypatch, tmp_path: Path):
    cfg_path = tmp_path / "config.json"
    data = {
        "forwards": [_example_forward("rds"), _example_forward("apprunner")],
        "sso_start_url": "https://example.com/start",
        "sso_login_timeout_seconds": 10,
        "sso_auto_refresh_margin_seconds": 20,
        "tunnel_check_interval_seconds": 30,
    }
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config_loader, "CONFIG_PATH", str(cfg_path))

    cfg = config_loader.load_config()

    assert len(cfg.forwards) == 2
    assert cfg.forwards[0].name == "rds"
    assert cfg.forwards[1].local_port == 1111
    assert cfg.sso_start_url == "https://example.com/start"
    assert cfg.sso_login_timeout_seconds == 10
    assert cfg.sso_auto_refresh_margin_seconds == 20
    assert cfg.tunnel_check_interval_seconds == 30


def test_load_config_missing_file(monkeypatch):
    monkeypatch.setattr(config_loader, "CONFIG_PATH", "/no/such/file/config.json")
    with pytest.raises(FileNotFoundError):
        config_loader.load_config()


def test_load_config_requires_forwards(monkeypatch, tmp_path: Path):
    cfg_path = tmp_path / "config.json"
    data = {"sso_start_url": "https://example.com/start", "forwards": []}
    cfg_path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config_loader, "CONFIG_PATH", str(cfg_path))

    with pytest.raises(ValueError):
        config_loader.load_config()


def test_parse_forward_validates_required(monkeypatch):
    # forwards 配列の要素に必須項目が無い場合は ValueError
    entry = {"name": "rds", "instance_id": "i-123"}
    with pytest.raises(ValueError):
        config_loader._parse_forward(entry)


def test_forward_target_types():
    ft = ForwardTarget(
        name="rds",
        instance_id="i-1",
        profile="p",
        region="r",
        local_port=1000,
        remote_host="h",
        remote_port=2000,
    )
    assert ft.name == "rds"
    assert ft.local_port == 1000
