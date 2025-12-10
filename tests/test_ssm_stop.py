import builtins
from types import SimpleNamespace
from unittest import mock

import aws_rds_tray.ssm as ssm


def make_proc(rc=None, pid=1234):
    proc = SimpleNamespace(pid=pid)
    proc.poll = mock.Mock(return_value=rc)
    proc.terminate = mock.Mock()
    proc.kill = mock.Mock()
    proc.wait = mock.Mock()
    return proc


def test_start_uses_start_new_session_and_calls_stop(monkeypatch):
    pf = ssm.SsmPortForward(
        instance_id="i-123",
        profile="p",
        region="r",
        local_port=15432,
        remote_host="h",
        remote_port=5432,
    )
    # stop should be invoked
    monkeypatch.setattr(pf, "stop", mock.Mock())
    # port not in use
    monkeypatch.setattr(ssm, "_is_port_in_use", mock.Mock(return_value=False))
    popen_mock = mock.Mock(return_value=make_proc(rc=None, pid=999))
    monkeypatch.setattr(ssm.subprocess, "Popen", popen_mock)

    ok = pf.start()

    pf.stop.assert_called_once()
    popen_mock.assert_called_once()
    _, kwargs = popen_mock.call_args
    assert kwargs.get("start_new_session") is True
    assert ok is True


def test_start_port_in_use_abort(monkeypatch):
    pf = ssm.SsmPortForward(
        instance_id="i-123",
        profile="p",
        region="r",
        local_port=15432,
        remote_host="h",
        remote_port=5432,
    )
    monkeypatch.setattr(pf, "stop", mock.Mock())
    monkeypatch.setattr(ssm, "_is_port_in_use", mock.Mock(return_value=True))
    monkeypatch.setattr(ssm, "notify", mock.Mock())

    ok = pf.start()

    assert ok is False
    ssm.notify.assert_called_once()


def test_stop_killpg_on_running(monkeypatch):
    pf = ssm.SsmPortForward(
        instance_id="i-123",
        profile="p",
        region="r",
        local_port=15432,
        remote_host="h",
        remote_port=5432,
    )
    proc = make_proc(rc=None, pid=111)
    pf.proc = proc

    killpg_mock = mock.Mock()
    getpgid_mock = mock.Mock(return_value=222)
    monkeypatch.setattr(ssm.os, "killpg", killpg_mock)
    monkeypatch.setattr(ssm.os, "getpgid", getpgid_mock)

    pf.stop()

    killpg_mock.assert_called()
    proc.wait.assert_called()
    assert pf.proc is None
