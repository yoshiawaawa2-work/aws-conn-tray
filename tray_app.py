#!/usr/bin/env python3
import sys

from aws_rds_tray.config_loader import load_config
from aws_rds_tray.logger import notify
from aws_rds_tray.prerequisites import ensure_supported_os, check_prerequisites


def main():
    """
    wxPython 版のみを起動するエントリポイント。
    """
    ensure_supported_os()
    config = load_config()

    ok, issues = check_prerequisites(
        config.profile,
        config.region,
        config.instance_id,
        config.remote_host,
        require_ssm=False,  # トークンが無い状態でも起動し、アプリ側で再ログインを促す
    )
    if not ok:
        for issue in issues:
            notify("前提条件エラー", issue)
        # SSM 到達性などはアプリ起動後に再ログインすれば解消するため、致命ではない
        # ここではログだけ残して継続する

    try:
        from aws_rds_tray.wx_tray import run_wx_app
    except Exception as e:
        notify("wx バックエンド起動失敗", f"wxPython を読み込めません: {e}")
        sys.exit(1)

    # config は事前チェック済みのものを渡して起動
    run_wx_app(config)


if __name__ == "__main__":
    main()
