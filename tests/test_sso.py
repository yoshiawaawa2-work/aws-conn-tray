import os
import datetime
import json

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


def test_check_sso_token_filters_access_token_and_start_url(tmp_path):
    # registration (clientIdのみ) は無視される
    (tmp_path / "reg.json").write_text('{"clientId": "abc", "expiresAt": "2099-01-01T00:00:00Z"}', encoding="utf-8")

    now = datetime.datetime.utcnow()
    future = (now + datetime.timedelta(seconds=500)).strftime("%Y-%m-%dT%H:%M:%SZ")
    other = (now + datetime.timedelta(seconds=100)).strftime("%Y-%m-%dT%H:%M:%SZ")

    good = {
        "startUrl": "https://good.example.com/start",
        "accessToken": "token-good",
        "expiresAt": future,
    }
    bad = {
        "startUrl": "https://other.example.com/start",
        "accessToken": "token-other",
        "expiresAt": other,
    }
    (tmp_path / "good.json").write_text(json.dumps(good), encoding="utf-8")
    (tmp_path / "bad.json").write_text(json.dumps(bad), encoding="utf-8")

    ok, remaining = sso.check_sso_token(
        cache_pattern=os.path.join(tmp_path, "*.json"),
        expected_start_url="https://good.example.com/start",
    )
    assert ok is True
    assert remaining is not None and remaining > 450  # future の方が採用される

    # startUrl が一致しない場合は無効扱い
    ok2, rem2 = sso.check_sso_token(
        cache_pattern=os.path.join(tmp_path, "*.json"),
        expected_start_url="https://not-found.example.com/start",
    )
    assert ok2 is False
    assert rem2 is None


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


def test_start_sso_login_browser(monkeypatch):
    called = {}

    class DummyPopen:
        def __init__(self, cmd):
            called["cmd"] = cmd

    monkeypatch.setattr(sso.subprocess, "Popen", DummyPopen)
    monkeypatch.setattr(sso, "notify", lambda *a, **k: None)
    monkeypatch.setattr(sso, "log_message", lambda *a, **k: None)

    ok = sso.start_sso_login_browser("profA", "regionB")
    assert ok is True
    assert called["cmd"][0:4] == ["aws", "sso", "login", "--profile"]
    assert "profA" in called["cmd"]


def test_start_sso_login_browser_handles_missing_aws(monkeypatch):
    notified = {}

    def fake_notify(title, message, on_click=None):
        notified["title"] = title
        notified["message"] = message

    monkeypatch.setattr(sso, "notify", fake_notify)
    monkeypatch.setattr(sso, "log_message", lambda *a, **k: None)

    def fake_popen(cmd):
        raise FileNotFoundError()

    monkeypatch.setattr(sso.subprocess, "Popen", fake_popen)

    ok = sso.start_sso_login_browser("p", "r")

    assert ok is False
    assert notified.get("title") == "aws コマンドが見つかりません"
    assert "AWS CLI" in notified.get("message", "")
