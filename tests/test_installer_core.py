import os
from pathlib import Path

import pytest

from aws_conn_tray import installer_core
from aws_conn_tray import platforms


def test_preinstall_builds_suggestions_when_missing(monkeypatch):
    monkeypatch.setattr(installer_core, "_which", lambda cmd: None)
    monkeypatch.setattr(installer_core, "platforms", platforms)
    monkeypatch.setattr(installer_core.platforms, "is_windows", lambda: True)
    monkeypatch.setattr(installer_core.platforms, "is_macos", lambda: False)
    monkeypatch.setattr(installer_core.platforms, "is_linux", lambda: False)

    res = installer_core.preinstall_check()

    assert res.ok is False
    assert any("aws CLI" in i or "psutil" in i for i in res.issues)
    assert res.suggested_installs  # winget コマンド群


def test_prepare_config_uses_example(tmp_path, monkeypatch):
    example = tmp_path / "dummy_config.json"
    monkeypatch.setattr(installer_core, "CONFIG_EXAMPLE_PATH", example)
    example.write_text('{"foo": "bar"}', encoding="utf-8")

    dst = installer_core.prepare_config(tmp_path, source_config=None)

    assert dst.exists()
    assert '"foo": "bar"' in dst.read_text(encoding="utf-8")


def test_generate_linux_autostart(tmp_path, monkeypatch):
    monkeypatch.setattr(installer_core.platforms, "is_windows", lambda: False)
    monkeypatch.setattr(installer_core.platforms, "is_macos", lambda: False)
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    (install_dir / "tray_app.py").write_text("print('x')", encoding="utf-8")

    entries = installer_core.generate_autostart_entries(install_dir, tmp_path / "shortcuts")

    assert len(entries) == 3
    assert any("aws-conn-tray.desktop" in str(p) for p in entries)


def test_generate_windows_autostart(tmp_path, monkeypatch):
    monkeypatch.setattr(installer_core.platforms, "is_windows", lambda: True)
    monkeypatch.setattr(installer_core.platforms, "is_macos", lambda: False)
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    (install_dir / "tray_app.py").write_text("print('x')", encoding="utf-8")

    entries = installer_core.generate_autostart_entries(install_dir, tmp_path / "shortcuts")

    assert any(p.suffix == ".cmd" for p in entries)


def test_generate_macos_autostart(tmp_path, monkeypatch):
    monkeypatch.setattr(installer_core.platforms, "is_windows", lambda: False)
    monkeypatch.setattr(installer_core.platforms, "is_macos", lambda: True)
    install_dir = tmp_path / "app"
    install_dir.mkdir()
    (install_dir / "tray_app.py").write_text("print('x')", encoding="utf-8")

    entries = installer_core.generate_autostart_entries(install_dir, tmp_path / "shortcuts")

    assert any(p.suffix == ".command" for p in entries)


def test_create_zip_excludes_build(tmp_path):
    src = tmp_path / "src"
    build = tmp_path / "src" / "build"
    src.mkdir()
    build.mkdir()
    (src / "tray_app.py").write_text("print('hi')", encoding="utf-8")
    (build / "skip.txt").write_text("skip", encoding="utf-8")

    zip_path = installer_core.create_zip(src, build)

    assert zip_path.exists()
    # 確認: build/skip.txt は含まれない
    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert "build/skip.txt" not in names


def test_install_and_uninstall_creates_shortcuts(tmp_path, monkeypatch):
    monkeypatch.setattr(installer_core.platforms, "is_windows", lambda: False)
    monkeypatch.setattr(installer_core.platforms, "is_macos", lambda: False)
    # 模擬的に APP_ROOT を tmp にする
    monkeypatch.setattr(installer_core, "APP_ROOT", tmp_path / "src")
    (installer_core.APP_ROOT).mkdir()
    (installer_core.APP_ROOT / "tray_app.py").write_text("print('hi')", encoding="utf-8")
    (installer_core.APP_ROOT / "config").mkdir()
    (installer_core.APP_ROOT / "config" / "config.example.json").write_text("{}", encoding="utf-8")

    install_dir = tmp_path / "dst"
    shortcuts_dir = tmp_path / "shortcuts"

    entries = installer_core.install_app(install_dir, shortcuts_dir=shortcuts_dir)
    assert install_dir.exists()
    assert (install_dir / "config" / "config.json").exists()
    assert entries and all(p.exists() for p in entries)

    installer_core.uninstall_app(install_dir, shortcuts_dir=shortcuts_dir)
    assert not install_dir.exists()
    assert not shortcuts_dir.exists()
