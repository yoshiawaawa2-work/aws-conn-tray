from aws_rds_tray import prerequisites


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
