import builtins
import socket
import sys
from types import SimpleNamespace

from aws_conn_tray.single_instance import SingleInstanceLock


def test_single_instance_uses_fasteners(monkeypatch, tmp_path):
    calls = {"acquire": 0, "release": 0}
    fallback_calls = []

    class DummyLock:
        def __init__(self, path):
            self.path = path

        def acquire(self, blocking: bool):
            calls["acquire"] += 1
            return calls["acquire"] == 1

        def release(self):
            calls["release"] += 1

    dummy_fasteners = SimpleNamespace(InterProcessLock=lambda p: DummyLock(p))
    monkeypatch.setitem(sys.modules, "fasteners", dummy_fasteners)
    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: fallback_calls.append(args))

    lock1 = SingleInstanceLock(lock_dir=tmp_path, socket_port=50010)
    assert lock1.acquire() is True
    lock2 = SingleInstanceLock(lock_dir=tmp_path, socket_port=50010)
    assert lock2.acquire() is False  # fasteners 側でブロック
    assert fallback_calls == []
    lock1.release()
    assert calls["release"] == 1


def test_single_instance_socket_fallback(monkeypatch, tmp_path):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "fasteners":
            raise ImportError()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    state = {"bound": False}

    class DummySocket:
        def __init__(self, *_a, **_k):
            self.closed = False

        def setsockopt(self, *_a, **_k):
            return None

        def bind(self, addr):
            if state["bound"]:
                raise OSError("in use")
            state["bound"] = True

        def listen(self, *_a, **_k):
            return None

        def close(self):
            self.closed = True

    monkeypatch.setattr(socket, "socket", lambda *a, **k: DummySocket())

    lock1 = SingleInstanceLock(lock_dir=tmp_path, socket_port=50011)
    assert lock1.acquire() is True
    lock2 = SingleInstanceLock(lock_dir=tmp_path, socket_port=50011)
    assert lock2.acquire() is False
    lock1.release()
