import aws_conn_tray.ssm as ssm
from aws_conn_tray.ssm import SsmPortForward
from aws_conn_tray.config_loader import ForwardTarget


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
    target = ForwardTarget(
        name="rds",
        instance_id="i-123",
        profile="prof",
        region="reg",
        local_port=1111,
        remote_host="db.example.com",
        remote_port=2222,
    )
    ssm_pf = SsmPortForward(target)
    ssm_pf.start()
    assert called["cmd"][0:3] == ["aws", "ssm", "start-session"]
    assert "AWS-StartPortForwardingSessionToRemoteHost" in called["cmd"]


def test_ssm_is_alive(monkeypatch):
    class P:
        def __init__(self, ret): self._ret = ret
        def poll(self): return self._ret
    target = ForwardTarget(
        name="rds",
        instance_id="i-123",
        profile="prof",
        region="reg",
        local_port=1111,
        remote_host="db.example.com",
        remote_port=2222,
    )
    ssm_pf = SsmPortForward(target)
    ssm_pf.proc = P(None)
    assert ssm_pf.is_alive()
    ssm_pf.proc = P(0)
    assert not ssm_pf.is_alive()


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
