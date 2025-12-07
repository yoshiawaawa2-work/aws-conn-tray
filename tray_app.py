#!/usr/bin/env python3
import os
import sys

import gi

from aws_rds_tray.config_loader import load_config
from aws_rds_tray.logger import notify, log_message
from aws_rds_tray.prerequisites import ensure_supported_os, check_prerequisites
from aws_rds_tray.sso import (
    bring_webview_to_front,
    check_sso_token,
    is_sso_session_valid,
    open_webview_minimized,
    run_sso_login,
)
from aws_rds_tray.tray import TrayApp

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


def ensure_initial_login(config) -> bool:
    token_ok, remaining = check_sso_token()
    session_ok = token_ok and is_sso_session_valid(config.profile, config.region)
    if token_ok and session_ok and remaining is not None and remaining > config.sso_auto_refresh_margin_seconds:
        log_message(f"Skip initial SSO login (remaining {remaining:.0f}s)")
        return True

    open_webview_minimized(config.sso_start_url)
    bring_webview_to_front()
    notify("SSO ログインが必要です", "ブラウザで SSO ログインを開始します。", on_click=bring_webview_to_front)
    return run_sso_login(
        profile=config.profile,
        region=config.region,
        sso_start_url=config.sso_start_url,
        timeout=config.sso_login_timeout_seconds,
    )


def main():
    """
    GUI バックエンド切り替え: 環境変数 AWS_RDS_TRAY_GUI=wx で wxPython 版、
    それ以外は従来の GTK 版。
    """
    backend = os.environ.get("AWS_RDS_TRAY_GUI", "gtk").lower()
    if backend == "wx":
        try:
            from aws_rds_tray.wx_tray import run_wx_app
        except Exception as e:
            notify("wx バックエンド起動失敗", f"wxPython を読み込めません: {e}")
            sys.exit(1)
        run_wx_app()
        return

    ensure_supported_os()
    config = load_config()

    if not ensure_initial_login(config):
        notify("SSO ログインに失敗しました", "トークンを取得できませんでした。ネットワークや認証情報を確認してください。")
        sys.exit(1)

    ok, issues = check_prerequisites(config.profile, config.region, config.instance_id, config.remote_host)
    if not ok:
        for issue in issues:
            notify("前提条件エラー", issue)
        sys.exit(1)

    app = TrayApp(config)
    Gtk.main()


if __name__ == "__main__":
    main()
