import os

from aws_rds_tray import sso


def test_parse_device_flow_picks_authorize_url():
    text = """
    Please visit https://example.com/device and also
    https://oidc.example.com/authorize?foo=bar
    Code: ABCD-EFGH
    """
    url, code = sso._parse_device_flow(text)
    assert "authorize" in url
    assert code == "ABCD-EFGH"


def test_token_cache_exists(tmp_path):
    pattern = os.path.join(tmp_path, "*.json")
    assert not sso.token_cache_exists(pattern)
    (tmp_path / "token.json").write_text("{}", encoding="utf-8")
    assert sso.token_cache_exists(pattern)


def test_is_sso_session_valid(monkeypatch):
    called = {}

    def fake_run(cmd, check, stdout, stderr, timeout):
        called["cmd"] = cmd
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr(sso.subprocess, "run", fake_run)
    assert sso.is_sso_session_valid("prof", "reg")
    assert "prof" in called["cmd"]
