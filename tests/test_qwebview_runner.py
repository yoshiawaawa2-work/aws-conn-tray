from aws_rds_tray.qwebview_runner import _is_callback_url


def test_is_callback_url():
    assert _is_callback_url("http://127.0.0.1:1234/oauth/callback?code=abc") is True
    assert _is_callback_url("https://127.0.0.1/oauth/callback?code=xyz") is True
    assert _is_callback_url("https://example.com") is False
