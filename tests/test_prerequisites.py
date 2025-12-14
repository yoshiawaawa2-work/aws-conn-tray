from aws_conn_tray import prerequisites


def test_check_prerequisites_ok(monkeypatch):
    monkeypatch.setattr(prerequisites, "shutil", type("S", (), {"which": lambda _: "/usr/bin/aws"}))
    monkeypatch.setattr(
        prerequisites,
        "_run_command",
        lambda cmd, timeout=5: (0, "aws-cli/2.0.0", ""),
    )
    monkeypatch.setattr(prerequisites, "check_profile_config", lambda profile: [])

    ok, issues = prerequisites.check_prerequisites("prof", "reg", "i-123", "db.example.com")
    assert ok
    assert issues == []


def test_check_prerequisites_missing_aws(monkeypatch):
    monkeypatch.setattr(prerequisites, "shutil", type("S", (), {"which": lambda _: None}))
    monkeypatch.setattr(prerequisites, "_run_command", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(prerequisites, "check_profile_config", lambda profile: [])

    ok, issues = prerequisites.check_prerequisites("prof", "reg", "i-123", "db.example.com")
    assert not ok
    assert any("aws CLI" in i for i in issues)


def test_check_profile_config_with_sso_session(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text(
        "[profile prof]\n"
        "sso_session = default\n"
        "[sso-session default]\n"
        "sso_start_url = https://example.com/start\n"
        "sso_region = ap-northeast-1\n",
        encoding="utf-8",
    )

    def fake_expand(path):
        if path == "~/.aws/config":
            return str(cfg)
        return path

    monkeypatch.setattr(prerequisites.os.path, "exists", lambda p: p == str(cfg))
    monkeypatch.setattr(prerequisites.os.path, "expanduser", fake_expand)

    issues = prerequisites.check_profile_config("prof")
    assert issues == []


def test_check_profile_config_missing_session(monkeypatch, tmp_path):
    cfg = tmp_path / "config"
    cfg.write_text(
        "[profile prof]\n"
        "sso_session = default\n",
        encoding="utf-8",
    )

    def fake_expand(path):
        if path == "~/.aws/config":
            return str(cfg)
        return path

    monkeypatch.setattr(prerequisites.os.path, "exists", lambda p: p == str(cfg))
    monkeypatch.setattr(prerequisites.os.path, "expanduser", fake_expand)

    issues = prerequisites.check_profile_config("prof")
    assert any("存在しません" in i for i in issues)


def test_check_prerequisites_flags_missing_remote_host(monkeypatch):
    # aws/session-manager-plugin はある前提
    monkeypatch.setattr(prerequisites, "shutil", type("S", (), {"which": lambda _: "/usr/bin/aws"}))
    monkeypatch.setattr(prerequisites, "_run_command", lambda *a, **k: (0, "aws-cli/2.0.0", ""))
    monkeypatch.setattr(prerequisites, "check_profile_config", lambda profile: [])

    ok, issues = prerequisites.check_prerequisites("prof", "reg", "i-123", "")

    assert ok is False
    assert any("remote_host" in i for i in issues)


def test_check_prerequisites_warns_when_awscli_not_v2(monkeypatch):
    monkeypatch.setattr(prerequisites, "shutil", type("S", (), {"which": lambda _: "/usr/bin/aws"}))
    monkeypatch.setattr(prerequisites, "_run_command", lambda *a, **k: (0, "aws-cli/1.27.0", ""))
    monkeypatch.setattr(prerequisites, "check_profile_config", lambda profile: [])

    ok, issues = prerequisites.check_prerequisites("prof", "reg", "i-123", "db.example.com")

    assert ok is False
    assert any("AWS CLI v2 ではない可能性があります" in i for i in issues)


def test_check_prerequisites_skip_ssm(monkeypatch):
    monkeypatch.setattr(prerequisites, "shutil", type("S", (), {"which": lambda _: "/usr/bin/aws"}))
    # SSM コマンドは実行されない前提で必ず失敗を返す
    def fake_run(cmd, timeout=5):
        if "describe-instance-information" in cmd:
            return (1, "", "token expired")
        return (0, "aws-cli/2.0.0", "")
    monkeypatch.setattr(prerequisites, "_run_command", fake_run)
    monkeypatch.setattr(prerequisites, "check_profile_config", lambda profile: [])

    ok, issues = prerequisites.check_prerequisites("prof", "reg", "i-123", "db.example.com", require_ssm=False)

    assert ok is True
    assert issues == []
