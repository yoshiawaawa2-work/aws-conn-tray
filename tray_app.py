#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys

import psutil
from aws_conn_tray.config_loader import load_config
from aws_conn_tray.logger import notify
from aws_conn_tray.prerequisites import ensure_supported_os, check_prerequisites
from aws_conn_tray.single_instance import SingleInstanceLock


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="AWS Conn Tray")
    parser.add_argument("--settings", action="store_true", help="設定編集のみを開く（本体は起動しません）")
    return parser.parse_args(argv)


def _is_main_running() -> bool:
    """
    メインアプリのロックを試し取得して起動有無を判定。取得できれば未起動とみなす。
    """
    lock = SingleInstanceLock(name="aws-conn-tray")
    acquired = lock.acquire()
    if acquired:
        lock.release()
    return not acquired


def _restart_running_app():
    """
    既存の tray_app プロセスを終了させて再起動する。psutil が無い場合は何もしない。
    - 自分自身は対象外
    - terminate→wait でできるだけ穏当な終了を試みる
    - ロック解放待ちを挟んでから再起動を投げる
    """
    try:
        me = os.getpid()
        targets = []
        for proc in psutil.process_iter(["pid", "cmdline"]):
            if proc.info.get("pid") == me:
                continue
            cmdline = proc.info.get("cmdline") or []
            if any("tray_app.py" in (c or "") for c in cmdline):
                targets.append(proc)
        for proc in targets:
            try:
                proc.terminate()
            except Exception:
                pass
        for proc in targets:
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        # lock が解放されるまで少し待つ
        for _ in range(10):
            if not _is_main_running():
                break
            try:
                import time

                time.sleep(0.2)
            except Exception:
                break
        # 再起動
        subprocess.Popen([sys.executable, os.path.abspath(__file__)])
    except Exception:
        notify("設定編集", "アプリの再起動に失敗しました。手動で再起動してください。")


def _run_settings_only():
    ensure_supported_os()
    main_running = _is_main_running()
    # 設定編集専用ロック（本体とは別名）で多重起動を防止
    lock = SingleInstanceLock(name="aws-conn-tray-settings")
    if not lock.acquire():
        notify("設定編集", "既に設定編集ウィンドウが開いています。")
        return
    try:
        try:
            wx_tray = getattr(sys.modules.get(__name__), "wx_tray", None)
            if wx_tray is None:
                from aws_conn_tray import wx_tray  # type: ignore
        except Exception as e:
            notify("設定編集", f"wxPython を読み込めません: {e}")
            return

        config = load_config()
        defaults = wx_tray._load_example_config()
        app = wx_tray.WxTrayApp()
        current = wx_tray._config_to_dict(config)
        new_data = app._show_settings_dialog(current, defaults)
        if not new_data:
            return
        ok, errors, parsed = wx_tray.validate_settings_input(new_data, defaults)
        if not ok:
            notify("入力エラー", "\n".join(errors))
            return
        if parsed != current:
            wx_tray._save_config(parsed)
            if main_running:
                _restart_running_app()
                notify("設定を保存しました", "起動中のアプリを再起動しました。")
            else:
                notify("設定を保存しました", "アプリ本体は起動していません。")
    finally:
        lock.release()


def main(argv=None):
    """
    wxPython 版のみを起動するエントリポイント。
    --settings が指定された場合は設定編集のみ行い、本体は起動しない。
    それ以外は単一インスタンスロックを取り、本体を起動する。
    """
    args = _parse_args(argv)
    if getattr(args, "settings", False):
        _run_settings_only()
        return
    lock = SingleInstanceLock()
    if not lock.acquire():
        notify("AWS Conn Tray", "既に AWS Conn Tray が起動しています。")
        return
    ensure_supported_os()
    config = load_config()

    ok, issues = check_prerequisites(
        config.forwards,
        require_ssm=False,  # トークンが無い状態でも起動し、アプリ側で再ログインを促す
    )
    if not ok:
        for issue in issues:
            notify("前提条件エラー", issue)
        # SSM 到達性などはアプリ起動後に再ログインすれば解消するため、致命ではない
        # ここではログだけ残して継続する

    try:
        from aws_conn_tray.wx_tray import run_wx_app
    except Exception as e:
        notify("wx バックエンド起動失敗", f"wxPython を読み込めません: {e}")
        sys.exit(1)

    # config は事前チェック済みのものを渡して起動
    try:
        run_wx_app(config)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
