import aws_conn_tray.ssm as ssm
from aws_conn_tray.ssm import SsmPortForward
from aws_conn_tray.config_loader import ForwardTarget
from aws_conn_tray import ssm as ssm_module


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
    monkeypatch.setattr(ssm_module, "PSUTIL_AVAILABLE", False)
    ssm_module._cleanup_lingering_sessions("test")

    assert (123, ssm.signal.SIGTERM) in calls
    assert (456, ssm.signal.SIGTERM) in calls


def test_cleanup_lingering_sessions_windows(monkeypatch):
    monkeypatch.setattr(ssm_module, "PSUTIL_AVAILABLE", False)
    invoked = []
    monkeypatch.setattr(ssm_module.platforms, "is_windows", lambda: True)
    monkeypatch.setattr(ssm_module.platforms, "is_linux", lambda: False)
    monkeypatch.setattr(ssm_module.platforms, "is_macos", lambda: False)
    monkeypatch.setattr(ssm_module, "log_message", lambda *a, **k: None)

    def fake_run(cmd, capture_output=None, text=None, timeout=None):
        invoked.append(cmd)
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(ssm_module.subprocess, "run", fake_run)

    ssm_module._cleanup_lingering_sessions("test")

    assert any(cmd and cmd[0] == "taskkill" for cmd in invoked)


def test_log_port_occupant_skips_on_non_linux(monkeypatch):
    monkeypatch.setattr(ssm_module.platforms, "is_linux", lambda: False)
    called = {}

    def fake_run(*a, **k):
        called["run"] = True
        return type("R", (), {"returncode": 1, "stdout": ""})()

    monkeypatch.setattr(ssm_module.subprocess, "run", fake_run)

    ssm_module._log_port_occupant(5432)
    assert "run" not in called


def test_cleanup_lingering_sessions_psutil(monkeypatch):
    # psutil が使える場合はプラットフォーム分岐に入らないことを確認
    monkeypatch.setattr(ssm_module, "PSUTIL_AVAILABLE", True)
    fake_procs = []

    class DummyProc:
        def __init__(self, pid, name, cmd):
            self.pid = pid
            self._name = name
            self._cmd = cmd
            fake_procs.append(self)

        def info(self):
            return {}

        def name(self):
            return self._name

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    def fake_process_iter(attrs):
        return [
            type(
                "P",
                (),
                {
                    "pid": 123,
                    "info": {"name": "session-manager-plugin", "cmdline": ["session-manager-plugin"]},
                    "terminate": lambda self=None: None,
                    "wait": lambda self=None, timeout=None: None,
                    "kill": lambda self=None: None,
                },
            )()
        ]

    monkeypatch.setattr(ssm_module, "psutil", type("X", (), {"process_iter": fake_process_iter}), raising=False)
    monkeypatch.setattr(ssm_module, "log_message", lambda *a, **k: None)

    ssm_module._cleanup_lingering_sessions("test")
    # taskkill/pgrep にフォールバックしないことを以って成功とする


def test_log_port_occupant_psutil(monkeypatch):
    monkeypatch.setattr(ssm_module, "PSUTIL_AVAILABLE", True)
    called = {"log": []}

    class DummyLaddr:
        def __init__(self, port):
            self.port = port

    class Conn:
        def __init__(self, pid, port):
            self.laddr = DummyLaddr(port)
            self.pid = pid
            self.status = "LISTEN"

    def fake_net_connections(kind):
        return [Conn(pid=111, port=15432)]

    def fake_process(pid):
        return type("P", (), {"name": lambda self=None: "dummy"})()

    monkeypatch.setattr(ssm_module, "psutil", type("X", (), {"net_connections": fake_net_connections, "Process": fake_process}), raising=False)
    monkeypatch.setattr(ssm_module, "log_message", lambda msg: called["log"].append(msg))
    monkeypatch.setattr(ssm_module.platforms, "is_linux", lambda: False)

    ssm_module._log_port_occupant(15432)

    assert any("psutil" in msg for msg in called["log"])
