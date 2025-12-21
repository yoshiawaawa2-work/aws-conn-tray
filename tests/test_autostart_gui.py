import autostart_gui


def test_toggle_autostart_enable(monkeypatch, tmp_path):
    called = {}

    def fake_enable(install_dir, dry_run=False, runner=None):
        called["dir"] = install_dir
        return [["enable"]]

    monkeypatch.setattr(autostart_gui.installer_core, "enable_autostart", fake_enable)
    cmds = autostart_gui.toggle_autostart(True, install_dir=tmp_path)
    assert called["dir"] == tmp_path
    assert cmds == [["enable"]]


def test_toggle_autostart_disable(monkeypatch):
    called = {}

    def fake_disable(dry_run=False, runner=None):
        called["called"] = True
        return [["disable"]]

    monkeypatch.setattr(autostart_gui.installer_core, "disable_autostart", fake_disable)
    monkeypatch.setattr(autostart_gui, "_request_app_exit", lambda *_a, **_k: called.setdefault("exit", True))
    cmds = autostart_gui.toggle_autostart(False, install_dir=None)
    assert called["called"] is True
    assert called["exit"] is True
    assert cmds == [["disable"]]


def test_is_autostart_enabled_linux(monkeypatch, tmp_path):
    monkeypatch.setattr(autostart_gui.platforms, "is_windows", lambda: False)
    monkeypatch.setattr(autostart_gui.platforms, "is_macos", lambda: False)
    monkeypatch.setattr(autostart_gui.Path, "home", lambda: tmp_path)
    # create service file to simulate enabled setup
    service = tmp_path / ".config" / "systemd" / "user" / "aws-conn-tray.service"
    service.parent.mkdir(parents=True, exist_ok=True)
    service.write_text("x", encoding="utf-8")

    called = {}

    def fake_runner(cmd, check=False, **kwargs):
        called["cmd"] = cmd
        return type("R", (), {"returncode": 0})()

    assert autostart_gui.is_autostart_enabled(tmp_path, runner=fake_runner) is True
    assert called["cmd"][:3] == ["systemctl", "--user", "is-enabled"]


def test_is_autostart_enabled_windows(monkeypatch):
    monkeypatch.setattr(autostart_gui.platforms, "is_windows", lambda: True)
    monkeypatch.setattr(autostart_gui.platforms, "is_macos", lambda: False)
    seen = {}

    def fake_runner(cmd, check=False, **kwargs):
        seen["cmd"] = cmd
        return type("R", (), {"returncode": 0})()

    assert autostart_gui.is_autostart_enabled(runner=fake_runner) is True
    assert seen["cmd"][:2] == ["schtasks", "/Query"]


def test_is_autostart_enabled_windows_disabled(monkeypatch):
    monkeypatch.setattr(autostart_gui.platforms, "is_windows", lambda: True)
    monkeypatch.setattr(autostart_gui.platforms, "is_macos", lambda: False)

    def fake_runner(cmd, check=False, **kwargs):
        return type("R", (), {"returncode": 1})()

    assert autostart_gui.is_autostart_enabled(runner=fake_runner) is False
