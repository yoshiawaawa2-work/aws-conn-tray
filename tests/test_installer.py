import os

import pytest

pytest.importorskip("PySimpleGUI")

import installer  # noqa: E402


def test_default_install_dir_linux(monkeypatch):
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.setenv("HOME", "/home/tester")
    monkeypatch.setattr(installer.os.path, "expanduser", lambda p: p.replace("~", "/home/tester"))

    assert installer.default_install_dir() == "/home/tester/aws-conn-tray"


def test_default_install_dir_windows(monkeypatch):
    monkeypatch.setattr(installer.sys, "platform", "win32")
    monkeypatch.setenv("SystemDrive", "D:")

    assert installer.default_install_dir() == os.path.join("D:", "aws-conn-tray")


def test_gather_values_converts_numbers(monkeypatch):
    monkeypatch.setattr(installer.sg, "popup_error", lambda *a, **k: None, raising=False)
    values = {
        "install_dir": "/tmp/aws-conn-tray",
        "forward_name": "rds",
        "instance_id": "i-123",
        "profile": "p",
        "region": "ap-northeast-1",
        "local_port": "15432",
        "remote_host": "db.example.com",
        "remote_port": "5432",
        "sso_start_url": "https://start",
        "sso_login_timeout_seconds": "900",
    }

    ok, data = installer.gather_values(values)

    assert ok is True
    assert isinstance(data["local_port"], int)
    assert isinstance(data["remote_port"], int)
    assert isinstance(data["sso_login_timeout_seconds"], int)


def test_gather_values_missing(monkeypatch):
    called = {}

    def fake_popup(msg, keep_on_top=None):
        called["msg"] = msg

    monkeypatch.setattr(installer.sg, "popup_error", fake_popup, raising=False)

    ok, data = installer.gather_values({"install_dir": "/tmp"})

    assert ok is False
    assert data == {}
    assert "未入力" in called.get("msg", "")
