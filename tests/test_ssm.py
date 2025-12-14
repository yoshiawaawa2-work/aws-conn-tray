import aws_conn_tray.ssm as ssm
from aws_conn_tray.ssm import SsmPortForward


def test_ssm_start_calls_popen(monkeypatch):
    called = {}

    monkeypatch.setattr("aws_conn_tray.ssm._is_port_in_use", lambda port: False)
    monkeypatch.setattr("aws_conn_tray.ssm.notify", lambda *a, **k: None)

    def fake_popen(cmd, **kwargs):
        called["cmd"] = cmd
        class P:
            def poll(self): return None
            def terminate(self): pass
        return P()

    monkeypatch.setattr("aws_conn_tray.ssm.subprocess.Popen", fake_popen)
    ssm = SsmPortForward("i-123", "prof", "reg", 1111, "db.example.com", 2222)
    ssm.start()
    assert called["cmd"][0:3] == ["aws", "ssm", "start-session"]
    assert "AWS-StartPortForwardingSessionToRemoteHost" in called["cmd"]


def test_ssm_is_alive(monkeypatch):
    class P:
        def __init__(self, ret): self._ret = ret
        def poll(self): return self._ret
    ssm = SsmPortForward("i-123", "prof", "reg", 1111, "db.example.com", 2222)
    ssm.proc = P(None)
    assert ssm.is_alive()
    ssm.proc = P(0)
    assert not ssm.is_alive()


def test_cleanup_lingering_sessions_kills_found_pids(monkeypatch):
    calls = []

    class DummyResult:
        returncode = 0
        stdout = "123\n456\n"
        stderr = ""

    monkeypatch.setattr("aws_conn_tray.ssm.subprocess.run", lambda *a, **k: DummyResult())
    monkeypatch.setattr("aws_conn_tray.ssm.log_message", lambda *a, **k: None)
    monkeypatch.setattr("aws_conn_tray.ssm.time.sleep", lambda *_: None)

    def fake_kill(pid, sig):
        calls.append((pid, sig))

    monkeypatch.setattr("aws_conn_tray.ssm.os.kill", fake_kill)

    ssm_module = __import__("aws_conn_tray.ssm", fromlist=["_cleanup_lingering_sessions"])
    ssm_module._cleanup_lingering_sessions("test")

    assert (123, ssm.signal.SIGTERM) in calls
    assert (456, ssm.signal.SIGTERM) in calls
