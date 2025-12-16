import subprocess
from pathlib import Path

import app_utils


def test_get_app_root_uses_executable_when_frozen(monkeypatch, tmp_path: Path):
    exe_dir = tmp_path / "dist"
    exe_dir.mkdir()
    monkeypatch.setattr(app_utils.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_utils.sys, "executable", str(exe_dir / "app"))

    assert app_utils.get_app_root() == str(exe_dir)


def test_log_message_writes_to_custom_path(monkeypatch, tmp_path: Path):
    log_dir = tmp_path / "logs"
    log_path = log_dir / "tray.log"
    monkeypatch.setattr(app_utils, "LOG_DIR", str(log_dir))
    monkeypatch.setattr(app_utils, "LOG_PATH", str(log_path))

    app_utils.log_message("hello")

    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "hello" in content


def test_run_command_handles_timeout(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="sleep", timeout=1)

    monkeypatch.setattr(app_utils.subprocess, "run", fake_run)

    rc, out, err = app_utils._run_command(["sleep", "2"])

    assert rc == 124
    assert err == "timeout"
    assert out == ""


def test_run_command_handles_not_found(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError()

    monkeypatch.setattr(app_utils.subprocess, "run", fake_run)

    rc, out, err = app_utils._run_command(["/not/exist"])

    assert rc == 127
    assert err == "not found"
    assert out == ""


def test_check_profile_config_accepts_sso_session(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.write_text(
        "[profile prof]\n"
        "sso_session = default\n"
        "[sso-session default]\n"
        "sso_start_url = https://example.com/start\n"
        "sso_region = ap-northeast-1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(app_utils.os.path, "exists", lambda p: p == str(cfg))
    monkeypatch.setattr(
        app_utils.os.path,
        "expanduser",
        lambda p: str(cfg) if p == "~/.aws/config" else p,
    )

    issues = app_utils._check_profile_config("prof")

    assert issues == []


def test_check_profile_config_flags_missing_session(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "config"
    cfg.write_text(
        "[profile prof]\n"
        "sso_session = default\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(app_utils.os.path, "exists", lambda p: p == str(cfg))
    monkeypatch.setattr(
        app_utils.os.path,
        "expanduser",
        lambda p: str(cfg) if p == "~/.aws/config" else p,
    )

    issues = app_utils._check_profile_config("prof")

    assert any("存在しません" in msg or "設定されていません" in msg for msg in issues)
