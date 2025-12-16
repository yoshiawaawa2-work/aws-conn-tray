from pathlib import Path

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
    assert res.suggested_installs  # winget コマンド群


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
    import zipfile

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert "build/skip.txt" not in names


def test_enable_autostart_windows(monkeypatch):
    monkeypatch.setattr(installer_core.platforms, "is_windows", lambda: True)
    monkeypatch.setattr(installer_core.platforms, "is_macos", lambda: False)
    seen = []

    def fake_run(cmd, check=False):
        seen.append(cmd)
        return type("R", (), {"returncode": 0})()

    installer_core.enable_autostart(Path("C:/app"), dry_run=False, runner=fake_run)

    assert any("schtasks" in c for c in seen[0])


def test_perform_install_preview(monkeypatch, tmp_path):
    monkeypatch.setattr(installer_core, "preinstall_check", lambda: installer_core.PreinstallResult(ok=True, issues=[], suggested_installs=[]))
    called = {}
    monkeypatch.setattr(installer_core, "install_app", lambda *a, **k: called.setdefault("install", True))

    opts = installer_core.InstallOptions(install_dir=tmp_path, preview=True)
    res = installer_core.perform_install(opts)

    assert res.ok is True
    assert called == {}  # preview では install_app を呼ばない
