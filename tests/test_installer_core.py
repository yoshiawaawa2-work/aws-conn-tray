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
    monkeypatch.setattr(installer_core, "setup_venv_and_install_deps", lambda *a, **k: (True, [], []))

    opts = installer_core.InstallOptions(install_dir=tmp_path, preview=True)
    res = installer_core.perform_install(opts)

    assert res.ok is True
    assert called == {}  # preview では install_app を呼ばない


def test_install_app_copies_runtime_only(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    # allowed items
    (src / "tray_app.py").write_text("print('x')", encoding="utf-8")
    (src / "app_utils.py").write_text("x", encoding="utf-8")
    (src / "uninstaller.py").write_text("x", encoding="utf-8")
    (src / "uninstaller_ui.py").write_text("x", encoding="utf-8")
    (src / "requirements.txt").write_text("req", encoding="utf-8")
    (src / "README.md").write_text("readme", encoding="utf-8")
    for d in ["aws_conn_tray", "config", "icons", "systemd"]:
        p = src / d
        p.mkdir()
        (p / "keep.txt").write_text("keep", encoding="utf-8")
    # disallowed items
    (src / "tests").mkdir()
    (src / "doc").mkdir()
    (src / "installer_ui.py").write_text("y", encoding="utf-8")

    monkeypatch.setattr(installer_core, "APP_ROOT", src)
    monkeypatch.setattr(installer_core, "generate_autostart_entries", lambda *_a, **_k: [])

    dest = tmp_path / "dest"
    shortcuts = tmp_path / "auto"
    installer_core.install_app(dest, shortcuts_dir=shortcuts, config_data={"forwards": []})

    # allowed present
    assert (dest / "tray_app.py").exists()
    assert (dest / "aws_conn_tray").exists()
    # disallowed absent
    assert not (dest / "tests").exists()
    assert not (dest / "doc").exists()
    assert not (dest / "installer_ui.py").exists()


def test_perform_install_runs_venv(monkeypatch, tmp_path):
    monkeypatch.setattr(installer_core, "preinstall_check", lambda: installer_core.PreinstallResult(ok=True, issues=[], suggested_installs=[]))
    monkeypatch.setattr(installer_core, "install_app", lambda *a, **k: [])
    venv_called = {}

    def fake_setup(dirpath, runner=None):
        venv_called["dir"] = dirpath
        return True, [], [["venv"], ["pip"]]

    monkeypatch.setattr(installer_core, "setup_venv_and_install_deps", fake_setup)
    monkeypatch.setattr(installer_core, "enable_autostart", lambda *a, **k: [])

    opts = installer_core.InstallOptions(install_dir=tmp_path, preview=False)
    res = installer_core.perform_install(opts)

    assert res.ok is True
    assert venv_called["dir"] == tmp_path
    assert ["venv"] in res.executed_commands


def test_perform_install_fail_venv(monkeypatch, tmp_path):
    monkeypatch.setattr(installer_core, "preinstall_check", lambda: installer_core.PreinstallResult(ok=True, issues=[], suggested_installs=[]))
    install_dir = tmp_path / "app"
    shortcuts_dir = tmp_path / "auto"

    def fake_install_app(install_dir_arg, shortcuts_dir=None, config_source=None, config_data=None, python_executable=None):
        install_dir_arg.mkdir(parents=True, exist_ok=True)
        if shortcuts_dir:
            shortcuts_dir.mkdir(parents=True, exist_ok=True)
        (install_dir_arg / "tray_app.py").write_text("x", encoding="utf-8")
        return []

    monkeypatch.setattr(installer_core, "install_app", fake_install_app)

    def fake_setup(dirpath, runner=None):
        return False, ["err"], [["venv"]]

    monkeypatch.setattr(installer_core, "setup_venv_and_install_deps", fake_setup)
    opts = installer_core.InstallOptions(install_dir=install_dir, shortcuts_dir=shortcuts_dir, preview=False)
    res = installer_core.perform_install(opts)

    assert res.ok is False
    assert "err" in res.issues
    assert ["venv"] in res.executed_commands
    # 失敗時はクリーンアップされる
    assert not install_dir.exists()
    assert not shortcuts_dir.exists()


def test_simulate_precheck_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(installer_core, "install_app", lambda *a, **k: [])
    monkeypatch.setattr(installer_core, "setup_venv_and_install_deps", lambda *a, **k: (True, [], []))
    opts = installer_core.InstallOptions(install_dir=tmp_path, simulate_precheck_fail=True)
    res = installer_core.perform_install(opts)
    assert res.ok is False
    assert any("simulate_precheck_fail" in s for s in res.issues)


def test_detect_linux_pkg_mgr_by_env(monkeypatch):
    monkeypatch.setenv("AWS_CONN_TRAY_OS_ID", "fedora")
    mgr = installer_core._detect_linux_pkg_mgr()
    assert mgr == "dnf"
    monkeypatch.setenv("AWS_CONN_TRAY_OS_ID", "opensuse")
    mgr = installer_core._detect_linux_pkg_mgr()
    assert mgr == "zypper"


def test_build_install_suggestions_python_cmd(monkeypatch):
    monkeypatch.setattr(installer_core.platforms, "is_windows", lambda: True)
    monkeypatch.setattr(installer_core.platforms, "is_macos", lambda: False)
    monkeypatch.setattr(installer_core.platforms, "is_linux", lambda: False)
    suggestions = installer_core._build_install_suggestions()
    assert any(cmd[:3] == ["python", "-m", "pip"] for cmd in suggestions)
