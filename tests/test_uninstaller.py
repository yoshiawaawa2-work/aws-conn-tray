from pathlib import Path

import aws_conn_tray.installer_core as installer_core
import uninstaller
import uninstaller_ui
import json


def test_perform_uninstall_preview(tmp_path, monkeypatch):
    called = {}

    def fake_disable_autostart(dry_run=False, runner=None):
        called["dry_run"] = dry_run
        return [["dummy", "cmd"]]

    meta_path = tmp_path / "meta.json"
    monkeypatch.setattr(installer_core, "_metadata_file", lambda: meta_path)
    meta_path = tmp_path / "meta.json"
    monkeypatch.setattr(installer_core, "_metadata_file", lambda: meta_path)
    monkeypatch.setattr(installer_core, "disable_autostart", fake_disable_autostart)
    monkeypatch.setattr(installer_core, "default_paths", lambda: {"install": install_dir, "autostart_dir": shortcuts_dir})

    install_dir = tmp_path / "install"
    shortcuts_dir = tmp_path / "autostart"
    install_dir.mkdir()
    shortcuts_dir.mkdir()
    (install_dir / "dummy.txt").write_text("x")
    (shortcuts_dir / "dummy.desktop").write_text("y")

    opts = installer_core.UninstallOptions(install_dir=install_dir, shortcuts_dir=shortcuts_dir, preview=True)
    res = installer_core.perform_uninstall(opts)

    assert res.ok
    assert install_dir.exists()
    assert shortcuts_dir.exists()
    assert called["dry_run"] is True
    assert set(res.targets) == {install_dir, shortcuts_dir}
    assert res.executed_commands == [["dummy", "cmd"]]


def test_perform_uninstall_removes_files(tmp_path, monkeypatch):
    calls = []

    def fake_disable_autostart(dry_run=False, runner=None):
        calls.append(dry_run)
        return [["disable"]]

    monkeypatch.setattr(installer_core, "disable_autostart", fake_disable_autostart)
    monkeypatch.setattr(installer_core, "default_paths", lambda: {"install": install_dir, "autostart_dir": shortcuts_dir})

    install_dir = tmp_path / "install"
    shortcuts_dir = tmp_path / "autostart"
    install_dir.mkdir()
    shortcuts_dir.mkdir()
    (install_dir / "dummy.txt").write_text("x")
    (shortcuts_dir / "dummy.desktop").write_text("y")

    opts = installer_core.UninstallOptions(install_dir=install_dir, shortcuts_dir=shortcuts_dir, preview=False)
    res = installer_core.perform_uninstall(opts)

    assert res.ok
    assert not install_dir.exists()
    assert not shortcuts_dir.exists()
    assert calls == [False]
    assert set(res.targets) == {install_dir, shortcuts_dir}
    assert res.executed_commands == [["disable"]]


def test_uninstaller_cli_preview(monkeypatch, capsys, tmp_path):
    def fake_perform(opts):
        assert opts.preview is True
        return installer_core.UninstallResult(ok=True, issues=[], targets=[tmp_path], executed_commands=[["cmd"]])

    meta_path = tmp_path / "meta.json"
    monkeypatch.setattr(installer_core, "_metadata_file", lambda: meta_path)
    monkeypatch.setattr(installer_core, "perform_uninstall", fake_perform)
    monkeypatch.setattr(installer_core, "default_paths", lambda: {"install": tmp_path, "autostart_dir": tmp_path / "auto"})

    rc = uninstaller.main(["--preview"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "プレビュー" in out
    assert "cmd" in out


def test_uninstaller_ui_headless_preview(monkeypatch, capsys, tmp_path):
    def fake_perform(opts):
        assert opts.preview is True
        assert opts.install_dir == tmp_path
        return installer_core.UninstallResult(ok=True, issues=[], targets=[tmp_path], executed_commands=[["cmd"]])

    meta_path = tmp_path / "meta.json"
    monkeypatch.setattr(installer_core, "_metadata_file", lambda: meta_path)
    monkeypatch.setattr(installer_core, "perform_uninstall", fake_perform)
    monkeypatch.setattr(installer_core, "default_paths", lambda: {"install": tmp_path, "autostart_dir": tmp_path / "auto"})
    monkeypatch.setattr(installer_core, "load_install_metadata", lambda: {"install_dir": tmp_path, "shortcuts_dir": tmp_path / "auto"})

    args = uninstaller_ui._parse_args(["--preview", "--headless"])
    rc = uninstaller_ui.run_headless(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "プレビュー" in out


def test_resolve_uninstall_paths_uses_metadata(tmp_path, monkeypatch):
    meta_path = tmp_path / "meta.json"
    monkeypatch.setattr(installer_core, "_metadata_file", lambda: meta_path)
    meta_path.write_text(json.dumps({"install_dir": str(tmp_path / "app"), "shortcuts_dir": str(tmp_path / "auto")}), encoding="utf-8")
    monkeypatch.setattr(installer_core, "default_paths", lambda: {"install": tmp_path / "def", "autostart_dir": tmp_path / "def_auto"})

    install_dir, shortcuts_dir, locked = uninstaller_ui.resolve_uninstall_paths(None, None)
    assert install_dir == tmp_path / "app"
    assert shortcuts_dir == tmp_path / "auto"
    assert locked is True


def test_resolve_uninstall_paths_without_metadata(tmp_path, monkeypatch):
    meta_path = tmp_path / "meta.json"
    monkeypatch.setattr(installer_core, "_metadata_file", lambda: meta_path)
    monkeypatch.setattr(installer_core, "default_paths", lambda: {"install": tmp_path / "def", "autostart_dir": tmp_path / "def_auto"})

    install_dir, shortcuts_dir, locked = uninstaller_ui.resolve_uninstall_paths(None, None)
    assert install_dir == tmp_path / "def"
    assert shortcuts_dir == tmp_path / "def_auto"
    assert locked is False


def test_uninstaller_nav_labels():
    class DummyBtn:
        def __init__(self):
            self.kwargs = {}

        def config(self, **kwargs):
            self.kwargs.update(kwargs)

    wiz = uninstaller_ui.UninstallWizard.__new__(uninstaller_ui.UninstallWizard)
    wiz.frames = [None, None, None, None]  # 4 steps
    wiz.back_button = DummyBtn()
    wiz.next_button = DummyBtn()

    # 確認画面（index=1）で「アンインストール」
    wiz.current_step = 1
    uninstaller_ui.UninstallWizard._update_nav_labels(wiz)
    assert wiz.next_button.kwargs.get("text") == "アンインストール"
    assert wiz.next_button.kwargs.get("state") == "normal"

    # 進捗画面（index=2）で処理中 disabled
    wiz.current_step = 2
    uninstaller_ui.UninstallWizard._update_nav_labels(wiz)
    assert wiz.next_button.kwargs.get("state") == "disabled"


def test_metadata_saved_and_cleared_on_install_uninstall(tmp_path, monkeypatch):
    meta_path = tmp_path / "meta.json"
    monkeypatch.setattr(installer_core, "_metadata_file", lambda: meta_path)

    def fake_install_app(install_dir, shortcuts_dir=None, config_source=None, config_data=None, **_):
        install_dir.mkdir(parents=True, exist_ok=True)
        if shortcuts_dir:
            shortcuts_dir.mkdir(parents=True, exist_ok=True)
        return []

    monkeypatch.setattr(installer_core, "install_app", fake_install_app)
    monkeypatch.setattr(installer_core, "enable_autostart", lambda *a, **k: [])
    monkeypatch.setattr(installer_core, "setup_venv_and_install_deps", lambda *a, **k: (True, [], []))
    install_dir = tmp_path / "app"
    opts = installer_core.InstallOptions(
        install_dir=install_dir,
        config_source=None,
        use_example_config=True,
        config_data=None,
        autostart=False,
        preview=False,
    )
    installer_core.perform_install(opts)
    meta = installer_core.load_install_metadata()
    assert meta and Path(meta["install_dir"]) == install_dir

    def fake_disable_autostart(dry_run=False, runner=None):
        return []

    monkeypatch.setattr(installer_core, "disable_autostart", fake_disable_autostart)
    un_opts = installer_core.UninstallOptions(install_dir=install_dir, shortcuts_dir=None, preview=False)
    installer_core.perform_uninstall(un_opts)
    assert not meta_path.exists()
