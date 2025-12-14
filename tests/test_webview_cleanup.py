import types

import pytest

from aws_conn_tray import wx_tray


class DummyProc:
    def __init__(self, pid: int, rc=None):
        self.pid = pid
        self._rc = rc
        self.calls = []

    def poll(self):
        return self._rc

    def terminate(self):
        self.calls.append("terminate")

    def wait(self, timeout=None):
        self.calls.append(("wait", timeout))
        # stay running if rc is None
        if self._rc is None:
            self._rc = 0

    def kill(self):
        self.calls.append("kill")
        self._rc = -9


def test_cleanup_qt_webview_procs_force(monkeypatch):
    running = DummyProc(pid=1, rc=None)
    finished = DummyProc(pid=2, rc=0)
    wx_tray._qt_webview_procs = [running, finished]

    wx_tray.cleanup_qt_webview_procs(force=True, timeout=0.1)

    # running は terminate→wait が呼ばれてリストから消える
    assert "terminate" in running.calls
    assert ("wait", pytest.approx(0.1)) in running.calls
    assert wx_tray._qt_webview_procs == []


def test_cleanup_qt_webview_procs_non_force(monkeypatch):
    running = DummyProc(pid=1, rc=None)
    finished = DummyProc(pid=2, rc=0)
    wx_tray._qt_webview_procs = [running, finished]

    wx_tray.cleanup_qt_webview_procs(force=False)

    # running は残り、終了済みは除去される
    assert wx_tray._qt_webview_procs == [running]


def test_shutdown_calls_webview_cleanup(monkeypatch):
    called = {"cleanup": 0, "lingering": 0, "exit": 0}

    def fake_cleanup(force=False):
        called["cleanup"] += 1

    def fake_lingering(reason):
        called["lingering"] += 1

    monkeypatch.setattr(wx_tray, "cleanup_qt_webview_procs", fake_cleanup)
    monkeypatch.setattr(wx_tray, "_cleanup_lingering_sessions", fake_lingering)

    app = wx_tray.WxTrayApp.__new__(wx_tray.WxTrayApp)
    app.tray_icon = None
    app._frame = None
    app.timer = None
    app.ssm = None
    app._app = types.SimpleNamespace(ExitMainLoop=lambda: called.__setitem__("exit", called["exit"] + 1))

    wx_tray.WxTrayApp.shutdown(app)

    assert called["cleanup"] == 1
    assert called["lingering"] == 1
    assert called["exit"] == 1
