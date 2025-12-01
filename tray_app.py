
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

# ------------------------------------------------------------
# 設定ファイルの読み込み
# ------------------------------------------------------------

CONFIG_PATH = os.path.expanduser("~/.config/aws-rds-tray/config.json")

def load_config():
    """
    設定ファイル (config.json) を読み込み、辞書として返す。
    基本的に ~/.config/aws-rds-tray/config.json を前提とする。
    """
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            f"設定ファイルが見つかりません: {CONFIG_PATH}\n"
            "config.example.json をコピーして編集してください。"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

config = load_config()

INSTANCE_ID = config["instance_id"]     # 踏み台 EC2 のインスタンス ID (i-xxxxxxxxxx)
PROFILE     = config["profile"]         # AWS CLI プロファイル名 (SSO 用)
REGION      = config["region"]          # リージョン (例: ap-northeast-1)
LOCAL_PORT  = int(config["local_port"]) # ローカル側で開くポート (例: 15432)
REMOTE_HOST = config.get("remote_host")  # RDS のホスト名 (例: xxx.rds.amazonaws.com)
REMOTE_PORT = int(config["remote_port"]) # RDS のポート (例: 5432)
SSO_URL     = config["sso_start_url"]   # IAM Identity Center (SSO) のユーザーポータル URL

# アイコンディレクトリ（このスクリプトと同一階層の icons/ を想定）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(BASE_DIR, "icons")

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
        self.warned_5min = False
        self.warned_1min = False
        self.warned_expired = False

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

            # --- SSO トークンの状態に応じて通知制御 ---
            if token_ok and remaining is not None:
                # トークン残り時間が大幅に伸びている（= 新しく login した）場合、警告フラグをリセット
                if remaining > 3600:
                    self.warned_5min = False
                    self.warned_1min = False
                    self.warned_expired = False

                if remaining <= 60 and not self.warned_1min:
                    notify("AWS SSO 有効期限が 1 分未満です", "aws sso login を実行してトークンを更新してください。")
                    self.warned_1min = True
                elif remaining <= 300 and not self.warned_5min:
                    notify("AWS SSO 有効期限が 5 分未満です", "近いうちに aws sso login を実行することをおすすめします。")
                    self.warned_5min = True
            else:
                # トークンが無効
                if not self.warned_expired:
                    notify(
                        "AWS SSO トークンが無効です",
                        "aws sso login を実行して再ログインしてください。\nメニューから SSO ログインページも開けます。"
                    )
                    self.warned_expired = True

            # --- アイコン色の決定 ---
            if not token_ok:
                # トークン無効 → 赤
                self.set_icon_color("red")
            else:
                if tunnel_ok:
                    # トークン有効かつトンネル生存 → 緑
                    self.set_icon_color("green")
                else:
                    # トークン有効だがトンネルが落ちている → 黄 + 再接続試行
                    self.set_icon_color("yellow")
                    start_port_forward()

            # 10 秒ごとにチェック（お好みで変更可能）
            for _ in range(10):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

# ------------------------------------------------------------
# エントリーポイント
# ------------------------------------------------------------

def main():
    app = TrayApp()
    Gtk.main()

if __name__ == "__main__":
    main()
