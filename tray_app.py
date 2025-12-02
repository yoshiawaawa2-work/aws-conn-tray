
#!/usr/bin/env python3
# ============================================================
# tray_app.py
# ------------------------------------------------------------
# AWS SSO + SSM Port Forward 管理用のトレイ常駐アプリ
#
# 機能概要：
#   - Ubuntu のパネル（タスクトレイ）に常駐アイコンを表示
#   - ~/.aws/sso/cache/*.json から SSO トークンの期限 (expiresAt) を監視
#   - トークンの残り時間が少ない時・切れた時にデスクトップ通知
#   - SSO ログイン画面（ブラウザ）を開くメニュー項目を提供
#   - AWS SSM のポートフォワード (AWS-StartPortForwardingSession) を起動・監視
#   - トンネルが切れていれば自動で張り直しを試行
#   - アイコンの色で状態を表示（緑 = 正常, 黄 = トンネル切れ, 赤 = トークン切れ）
#
# 注意点：
#   - 「通知をクリックしたらブラウザを開く」機能は、環境依存が大きく
#     安定しないため、このサンプルでは「トレイアイコンのメニューから
#     SSO ログインを開く」方式にしています。
#   - SSO トークンの取得・更新自体は aws sso login コマンドに依存します。
#     このスクリプトは「期限監視」と「ポートフォワードの自動復旧」を
#     主目的としています。
# ============================================================

import gi
import os
import glob
import json
import datetime
import subprocess
import threading
import time
import sys
from typing import Optional

# Gtk / AppIndicator のバージョン指定
gi.require_version("Gtk", "3.0")
# Ubuntu 環境によっては AyatanaAppIndicator3 か AppIndicator3 か異なるため両方を試す
try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator
except (ValueError, ImportError):
    gi.require_version("AppIndicator3", "0.1")
    from gi.repository import AppIndicator3 as AppIndicator

from gi.repository import Gtk, GObject, GLib

from app_utils import (
    APP_ROOT,
    CONFIG_DIR,
    CONFIG_PATH,
    LOG_PATH,
    check_prerequisites,
    log_message,
)

# ------------------------------------------------------------
# 設定ファイルの読み込み
# ------------------------------------------------------------

def load_config():
    """
    設定ファイル (config/config.json) を読み込み、辞書として返す。
    """
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            f"設定ファイルが見つかりません: {CONFIG_PATH}\n"
            f"インストーラまたは config.example.json を元に {CONFIG_PATH} を作成してください。"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

config = load_config()

INSTANCE_ID = config["instance_id"]      # 踏み台 EC2 のインスタンス ID (i-xxxxxxxxxx)
PROFILE     = config["profile"]          # AWS CLI プロファイル名 (SSO 用)
REGION      = config["region"]           # リージョン (例: ap-northeast-1)
LOCAL_PORT  = int(config["local_port"])  # ローカル側で開くポート (例: 15432)
REMOTE_HOST = config.get("remote_host")  # RDS のホスト名 (例: xxx.rds.amazonaws.com)
REMOTE_PORT = int(config["remote_port"]) # RDS のポート (例: 5432)
SSO_URL     = config["sso_start_url"]    # IAM Identity Center (SSO) のユーザーポータル URL
SSO_LOGIN_TIMEOUT = int(config.get("sso_login_timeout_seconds", 900))  # SSO ログイン待ちタイムアウト（秒）
SSO_AUTO_REFRESH_MARGIN = int(config.get("sso_auto_refresh_margin_seconds", 300))  # 自動更新を試す残り秒数
TUNNEL_CHECK_INTERVAL = int(config.get("tunnel_check_interval_seconds", 10))  # トンネル確認間隔（秒）

# アイコンディレクトリ（インストール先直下の icons/ を想定）
ICON_DIR = os.path.join(APP_ROOT, "icons")

# ------------------------------------------------------------
# ユーティリティ関数
# ------------------------------------------------------------

def notify(title: str, message: str) -> None:
    """
    デスクトップ通知を表示する。
    notify-send コマンドをラップしているだけなので、
    必要に応じて libnotify / Gio.Notification などに差し替え可能。
    """
    try:
        subprocess.run(
            ["notify-send", title, message, "--expire-time=8000"],
            check=False
        )
    except FileNotFoundError:
        # notify-send が無い場合は何もしない（ログに出してもよい）
        pass
    finally:
        log_message(f"{title}: {message}")

def check_sso_token():
    """
    ~/.aws/sso/cache/*.json のうち、最も新しいファイルを 1 つ選び、
    expiresAt を読み取って残り秒数を返す。
    戻り値:
        (token_is_valid: bool, remaining_seconds: float | None)
        ※ トークンがそもそも見つからない場合は (False, None)
    """
    cache_pattern = os.path.expanduser("~/.aws/sso/cache/*.json")
    cache_files = glob.glob(cache_pattern)

    if not cache_files:
        return False, None

    # 一番新しいファイル（aws sso login 実行の度に更新される想定）
    latest_file = max(cache_files, key=os.path.getmtime)

    try:
        with open(latest_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False, None

    expires_at = data.get("expiresAt")
    if not expires_at:
        return False, None

    try:
        # 例: "2025-11-24T01:23:45Z"
        expires_dt = datetime.datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False, None

    now = datetime.datetime.utcnow()
    remaining = (expires_dt - now).total_seconds()
    return remaining > 0, remaining

# ------------------------------------------------------------
# SSM ポートフォワードの管理
# ------------------------------------------------------------

ssm_process = None  # subprocess.Popen オブジェクトを保持

def start_port_forward():
    """
    AWS SSM start-session を使ってポートフォワードを開始する。
    既にプロセスが生きている場合は何もしない。
    """
    global ssm_process

    if ssm_process is not None and ssm_process.poll() is None:
        # すでに生きている
        return

    if not REMOTE_HOST:
        notify("設定エラー", "config.json の remote_host が設定されていません。")
        return

    # RDS などの別ホストに転送するため、RemoteHost 用のドキュメントを使う
    cmd = [
        "aws", "ssm", "start-session",
        "--target", INSTANCE_ID,
        "--document-name", "AWS-StartPortForwardingSessionToRemoteHost",
        "--parameters", f"host={REMOTE_HOST},portNumber={REMOTE_PORT},localPortNumber={LOCAL_PORT}",
        "--profile", PROFILE,
        "--region", REGION,
    ]

    # 非同期で起動（終了状況は is_port_forward_alive() で確認）
    try:
        ssm_process = subprocess.Popen(cmd)
    except FileNotFoundError:
        notify("aws コマンドが見つかりません", "awscli v2 がインストールされているか確認してください。")
    except Exception as e:
        notify("SSM セッション起動エラー", str(e))

def is_port_forward_alive() -> bool:
    """
    SSM ポートフォワード (aws ssm start-session) のプロセスが生きているかを確認する。
    """
    global ssm_process
    if ssm_process is None:
        return False
    return ssm_process.poll() is None

# ------------------------------------------------------------
# 前提条件チェック
# ------------------------------------------------------------

SUPPORTED_PLATFORMS = ("linux", "linux2", "win32")


def ensure_supported_os():
    if sys.platform not in SUPPORTED_PLATFORMS:
        notify("非対応OS", "このアプリは Linux/Windows のみ対応しています。")
        raise SystemExit(1)


def ensure_prerequisites():
    ok, issues = check_prerequisites(PROFILE, REGION, INSTANCE_ID, REMOTE_HOST)
    if not ok:
        for issue in issues:
            notify("前提条件エラー", issue)
        raise SystemExit(1)
    log_message("Prerequisite check passed")

# ------------------------------------------------------------
# SSO ログイン
# ------------------------------------------------------------

def run_sso_login(timeout: Optional[int] = None) -> bool:
    """
    aws sso login を実行し、トークンが有効化されたら True。
    ブラウザはシステム既定を起動する（最小化などは環境依存のため保証できない）。
    """
    cmd = ["aws", "sso", "login", "--profile", PROFILE]
    if REGION:
        cmd += ["--region", REGION]

    effective_timeout = timeout if timeout is not None else SSO_LOGIN_TIMEOUT
    log_message(f"SSO login start: {' '.join(cmd)} (timeout={effective_timeout}s)")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + effective_timeout
        stdout_acc = ""
        stderr_acc = ""
        while True:
            token_ok, _ = check_sso_token()
            if token_ok:
                log_message("SSO token detected during login; assuming success")
                if proc.poll() is None:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                return True

            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                stdout_acc = (stdout or "").strip()
                stderr_acc = (stderr or "").strip()
                break

            if time.time() > deadline:
                proc.kill()
                stdout, stderr = proc.communicate()
                stdout_acc = (stdout or "").strip()
                stderr_acc = (stderr or "").strip()
                notify(
                    "SSO ログインがタイムアウトしました",
                    f"{effective_timeout}秒以内にログインが完了しませんでした。ネットワークやブラウザを確認してください。"
                )
                return False

            time.sleep(2)

    except FileNotFoundError:
        notify("aws コマンドが見つかりません", "awscli v2 がインストールされているか確認してください。")
        return False
    except Exception as e:
        notify("SSO ログインエラー", str(e))
        return False

    if proc.returncode != 0:
        detail = stderr_acc or stdout_acc or "理由不明のエラー"
        notify("SSO ログインに失敗しました", f"ネットワークや認証状態を確認してください。\n{detail}")
        log_message(f"SSO login failed (code {proc.returncode}): {detail}")
        return False

    log_message("SSO login succeeded")
    if stdout_acc:
        log_message(f"SSO login stdout: {stdout_acc}")
    if stderr_acc:
        log_message(f"SSO login stderr: {stderr_acc}")
    return True


def initial_sso_login() -> None:
    """
    起動時に SSO トークンを確認し、必要ならブラウザログインを実行。
    """
    token_ok, remaining = check_sso_token()
    if token_ok and remaining is not None and remaining > SSO_AUTO_REFRESH_MARGIN:
        log_message(f"Skip initial SSO login (remaining {remaining:.0f}s)")
        return

    notify("SSO ログインが必要です", "ブラウザで SSO ログインを開始します。")
    if not run_sso_login(timeout=SSO_LOGIN_TIMEOUT):
        raise SystemExit(1)

# ------------------------------------------------------------
# トレイアプリ本体
# ------------------------------------------------------------

class TrayApp:
    """
    Gtk + AppIndicator によるトレイ常駐アプリ。
    バックグラウンドスレッドで定期的に状態を監視し、
    アイコン色・通知・ポートフォワードの再接続などを行う。
    """
    def __init__(self):
        # インジケータ（トレイアイコン）を作成
        self.indicator = AppIndicator.Indicator.new(
            "aws-rds-tray",
            os.path.join(ICON_DIR, "red.png"),  # 初期状態は赤（不明 / 切断）
            AppIndicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        # メニューを作成
        menu = Gtk.Menu()

        # 手動で SSO ログインページを開くメニュー項目
        open_sso_item = Gtk.MenuItem.new_with_label("SSO ログインページを開く")
        open_sso_item.connect("activate", self.on_open_sso_clicked)
        menu.append(open_sso_item)

        # 手動でポートフォワード再接続を試みるメニュー項目
        reconnect_item = Gtk.MenuItem.new_with_label("ポートフォワード再接続")
        reconnect_item.connect("activate", self.on_reconnect_clicked)
        menu.append(reconnect_item)

        # 区切り
        separator = Gtk.SeparatorMenuItem()
        menu.append(separator)

        # 終了メニュー項目
        quit_item = Gtk.MenuItem.new_with_label("終了")
        quit_item.connect("activate", self.on_quit_clicked)
        menu.append(quit_item)

        menu.show_all()
        self.indicator.set_menu(menu)

        # 状態監視用のフラグ
        self.auto_refresh_in_progress = False
        self.notified_token_invalid = False
        self.notified_tunnel_down = False

        # 監視ループを別スレッドで開始
        self._stop_event = threading.Event()
        self.thread = threading.Thread(target=self.monitor_loop, daemon=True)
        self.thread.start()

    # -------------------- メニューアクション --------------------

    def on_open_sso_clicked(self, _menu_item):
        """
        メニュー「SSO ログインページを開く」がクリックされた時に呼ばれる。
        デフォルトブラウザで SSO_URL を開く。
        """
        try:
            subprocess.run(["gio", "open", SSO_URL], check=False)
        except FileNotFoundError:
            notify("ブラウザ起動エラー", "gio コマンドが見つかりませんでした。")

    def on_reconnect_clicked(self, _menu_item):
        """
        メニュー「ポートフォワード再接続」がクリックされた時に呼ばれる。
        すでに生きている場合は何もしない。
        """
        if is_port_forward_alive():
            notify("ポートフォワード", "すでにポートフォワードは有効です。")
        else:
            notify("ポートフォワード", "ポートフォワード再接続を試行します。")
            start_port_forward()

    def on_quit_clicked(self, _menu_item):
        """
        メニュー「終了」がクリックされた時に呼ばれる。
        バックグラウンドスレッドを止め、Gtk メインループを終了する。
        """
        self._stop_event.set()
        # スレッドが Gtk オブジェクトを触っている最中に終了すると警告が出ることがあるため、
        # 少し待ってからメインループを終了する。
        if self.thread.is_alive():
            self.thread.join(timeout=2)
        Gtk.main_quit()

    # -------------------- アイコン制御 --------------------

    def set_icon_color(self, color: str):
        """
        color に "red" / "yellow" / "green" を指定してアイコン画像を切り替える。
        """
        if self._stop_event.is_set():
            # 終了処理中は何もしない
            return
        icon_path = os.path.join(ICON_DIR, f"{color}.png")
        if not os.path.exists(icon_path):
            # 万一アイコンが無ければ何もしない（またはデフォルトに戻す）
            return
        # Gtk の UI 更新はメインスレッドで行う必要があるため、
        # GLib.idle_add を使ってメインスレッドに投げる。
        def _update_icon():
            if self._stop_event.is_set():
                return False
            try:
                self.indicator.set_icon(icon_path)
            except Exception:
                return False
            return False  # 一度だけ実行

        GLib.idle_add(_update_icon, priority=GLib.PRIORITY_DEFAULT)

    # -------------------- メイン監視ループ --------------------

    def monitor_loop(self):
        """
        バックグラウンドスレッドで実行される監視ループ。
        おおよそ 10 秒ごとに：
          - SSO トークンの有効期限を確認
          - ポートフォワードの生死を確認
          - 状態に応じてアイコン色を変更
          - 必要に応じて通知を表示
          - トンネルの自動再接続を試行
        """
        while not self._stop_event.is_set():
            token_ok, remaining = check_sso_token()
            tunnel_ok = is_port_forward_alive()

            # --- SSO トークンの状態に応じて自動リフレッシュ ---
            if token_ok and remaining is not None:
                self.notified_token_invalid = False
                if remaining <= SSO_AUTO_REFRESH_MARGIN and not self.auto_refresh_in_progress:
                    self.auto_refresh_in_progress = True
                    threading.Thread(target=self._try_auto_refresh, daemon=True).start()
            else:
                if not self.notified_token_invalid:
                    notify(
                        "AWS SSO トークンが無効です",
                        "ブラウザで SSO ログインを行ってください。"
                    )
                    self.notified_token_invalid = True

            # --- ポートフォワードの状態確認 ---
            if token_ok and not tunnel_ok:
                self.set_icon_color("yellow")
                start_port_forward()
                self.notified_tunnel_down = False
            elif token_ok and tunnel_ok:
                self.set_icon_color("green")
                self.notified_tunnel_down = False
            else:
                self.set_icon_color("red")

            # 設定された間隔でチェック
            for _ in range(TUNNEL_CHECK_INTERVAL):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    # -------------------- SSO 自動更新処理 --------------------

    def _try_auto_refresh(self):
        """
        残り時間が少ないときに SSO ログインを実行してトークンを更新する。
        成功したらトンネルが落ちている場合は再接続する。
        """
        success = run_sso_login(timeout=SSO_LOGIN_TIMEOUT)
        self.auto_refresh_in_progress = False
        if success:
            log_message("SSO auto-refresh succeeded")
            if not is_port_forward_alive():
                start_port_forward()
        else:
            notify(
                "SSO 再ログインが必要です",
                "SSO トークンの自動更新に失敗しました。ブラウザで再ログインしてください。"
            )

# ------------------------------------------------------------
# エントリーポイント
# ------------------------------------------------------------

def main():
    # OS と前提条件を確認し、SSO ログインを自動実行してからトレイアプリを起動する
    ensure_supported_os()
    ensure_prerequisites()
    initial_sso_login()
    app = TrayApp()
    Gtk.main()

if __name__ == "__main__":
    main()
