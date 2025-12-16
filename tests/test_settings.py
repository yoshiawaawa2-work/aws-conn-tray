from aws_conn_tray.wx_tray import validate_settings_input


def test_validate_config_success():
    defaults = {
        "local_port": 15432,
        "remote_port": 5432,
        "sso_login_timeout_seconds": 900,
        "sso_auto_refresh_margin_seconds": 300,
        "tunnel_check_interval_seconds": 10,
    }
    data = {
        "forwards": [
            {
                "name": "rds",
                "instance_id": "i-123",
                "profile": "prof",
                "region": "ap-northeast-1",
                "remote_host": "db.example",
                "local_port": "15432",
                "remote_port": "5432",
            }
        ],
        "sso_start_url": "https://example.com/start",
        "sso_login_timeout_seconds": "900",
        "sso_auto_refresh_margin_seconds": "300",
        "tunnel_check_interval_seconds": "10",
    }
    ok, errors, parsed = validate_settings_input(data, defaults)
    assert ok is True
    assert errors == []
    assert parsed["forwards"][0]["local_port"] == 15432
    assert parsed["forwards"][0]["instance_id"] == "i-123"


def test_validate_config_errors():
    defaults = {"local_port": 1}
    data = {
        "forwards": [
            {
                "name": "",
                "instance_id": "",
                "profile": "",
                "region": "",
                "remote_host": "",
                "local_port": "-1",
                "remote_port": "abc",
            }
        ],
        "sso_start_url": "",
        "sso_login_timeout_seconds": "-5",
        "sso_auto_refresh_margin_seconds": "-1",
        "tunnel_check_interval_seconds": "-2",
    }
    ok, errors, _ = validate_settings_input(data, defaults)
    assert ok is False
    assert len(errors) >= 1
