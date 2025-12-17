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
    cmds = autostart_gui.toggle_autostart(False, install_dir=None)
    assert called["called"] is True
    assert cmds == [["disable"]]
