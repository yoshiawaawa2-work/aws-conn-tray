
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
import re
import select
import os
from typing import Optional
try:
    import pty
    PTY_AVAILABLE = True
except ImportError:
    PTY_AVAILABLE = False

LAST_DEVICE_URL: Optional[str] = None
LAST_DEVICE_CODE: Optional[str] = None
LAST_WEBVIEW_WINDOW = None

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

# WebView (オプション)
try:
    gi.require_version("WebKit2", "4.0")
    from gi.repository import WebKit2
    WEBKIT_AVAILABLE = True
except (ValueError, ImportError):
    WEBKIT_AVAILABLE = False

from app_utils import (
    APP_ROOT,
    CONFIG_DIR,
    CONFIG_PATH,
    LOG_PATH,
    check_prerequisites,
    log_message,
)
SSO_CACHE_PATTERN = os.path.expanduser("~/.aws/sso/cache/*.json")

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


def open_webview_minimized(url: str) -> bool:
    """
    WebKit2 が使える場合に、最小化状態の小さな WebView を開く。
    Gtk メインループ起動後に呼び出すこと。
    """
    if not WEBKIT_AVAILABLE:
        return False

    def _create():
        window = Gtk.Window()
        window.set_title("AWS SSO ログイン")
        window.set_default_size(400, 300)
        view = WebKit2.WebView()
        view.load_uri(url)
        window.add(view)
        window.show_all()
        try:
            window.set_keep_above(True)
        except Exception:
            pass
        try:
            window.iconify()
        except Exception:
            pass
        global LAST_WEBVIEW_WINDOW
        LAST_WEBVIEW_WINDOW = window
        try:
            window.present_with_time(Gtk.get_current_event_time())
        except Exception:
            pass
        return False

    GLib.idle_add(_create, priority=GLib.PRIORITY_DEFAULT)
    log_message(f"Open WebView minimized: {url}")
    return True


def bring_webview_to_front():
    """最後に開いた WebView を前面に出す。無ければ何もしない。"""
    global LAST_WEBVIEW_WINDOW
    try:
        if LAST_WEBVIEW_WINDOW:
            LAST_WEBVIEW_WINDOW.present_with_time(Gtk.get_current_event_time())
            LAST_WEBVIEW_WINDOW.set_keep_above(True)
    except Exception:
        pass


def is_sso_session_valid() -> bool:
    """
    aws sts get-caller-identity を呼び、SSO セッションが有効か簡易判定する。
    """
    cmd = ["aws", "sts", "get-caller-identity", "--profile", PROFILE]
    if REGION:
        cmd += ["--region", REGION]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False
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


def token_cache_exists() -> bool:
    return bool(glob.glob(SSO_CACHE_PATTERN))

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

def _parse_device_flow(text: str) -> (Optional[str], Optional[str]):
    url = None
    code = None
    urls = [m.strip().rstrip(".") for m in re.findall(r"https?://\S+", text)]
    # authorize を優先、それ以外は最初の URL
    for u in urls:
        if "authorize" in u:
            url = u
            break
    if url is None and urls:
        url = urls[0]

    m = re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}", text)
    if m:
        code = m.group(0)
    return url, code


def run_sso_login(timeout: Optional[int] = None) -> bool:
    """
    aws sso login --no-browser を実行し、デバイスフローの URL を取得して埋め込みブラウザ（あれば）で開く。
    トークンが有効化されたら True。タイムアウトで False。
    """
    cmd = ["aws", "sso", "login", "--no-browser", "--profile", PROFILE]
    if REGION:
        cmd += ["--region", REGION]

    effective_timeout = timeout if timeout is not None else SSO_LOGIN_TIMEOUT
    log_message(f"SSO login start (no-browser): {' '.join(cmd)} (timeout={effective_timeout}s)")

    try:
        if PTY_AVAILABLE:
            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                text=False,
                close_fds=True,
            )
            read_fds = [master_fd]
        else:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            read_fds = [stream for stream in (proc.stdout, proc.stderr) if stream]
    except FileNotFoundError:
        notify("aws コマンドが見つかりません", "awscli v2 がインストールされているか確認してください。")
        return False
    except Exception as e:
        notify("SSO ログインエラー", str(e))
        return False

    deadline = time.time() + effective_timeout
    stdout_acc = ""
    stderr_acc = ""
    device_url = None
    code_hint = None
    url_opened = False
    fallback_opened = False
    start_time = time.time()

    while True:
        if proc.poll() is not None:
            out, err = proc.communicate()
            stdout_acc += (out or "")
            stderr_acc += (err or "")
            if out:
                log_message(f"SSO login stdout (final): {out.strip()}")
            if err:
                log_message(f"SSO login stderr (final): {err.strip()}")
            # プロセス終了後の出力からも URL/コードを拾う
            if not device_url:
                url, code = _parse_device_flow(stdout_acc + "\n" + stderr_acc)
                if url:
                    device_url = url
                if code and not code_hint:
                    code_hint = code
            break

        # 非ブロッキング読み取り
        ready, _, _ = select.select(read_fds, [], [], 1)
        for stream in ready:
            if PTY_AVAILABLE:
                try:
                    line_bytes = os.read(stream, 4096)
                    if not line_bytes:
                        continue
                    line = line_bytes.decode(errors="ignore")
                except OSError:
                    continue
            else:
                line = stream.readline()
                if not line:
                    continue

            log_message(f"SSO login stream: {line.strip()}")
            if PTY_AVAILABLE:
                stdout_acc += line
            else:
                if stream is proc.stdout:
                    stdout_acc += line
                else:
                    stderr_acc += line

            url, code = _parse_device_flow(line)
            if code and not code_hint:
                code_hint = code
            if url and not device_url:
                device_url = url
            if device_url and not url_opened:
                opened = open_webview_minimized(device_url)
                if not opened:
                    try:
                        subprocess.run(["gio", "open", device_url], check=False)
                    except Exception:
                        pass
                notify(
                    "SSO トークン更新",
                    f"SSO トークンを更新するためログイン画面を開きました。コード: {code or code_hint or '---'}"
                )
                log_message(f"Opened device URL: {device_url}")
                url_opened = True

        # フォールバック: 一定時間経過しても device URL が取れない場合は SSO_URL を開く
        if not url_opened and not fallback_opened and (time.time() - start_time) > 3:
            opened = open_webview_minimized(device_url or SSO_URL)
            if not opened:
                try:
                    subprocess.run(["gio", "open", device_url or SSO_URL], check=False)
                except Exception:
                    pass
            log_message(f"SSO login fallback: opening {device_url or SSO_URL}")
            notify(
                "SSO トークン更新",
                f"SSO トークンを更新するためログイン画面を開きました。コード: {code_hint or '---'}"
            )
            fallback_opened = True

        token_ok, _ = check_sso_token()
        if token_ok and is_sso_session_valid():
            log_message("SSO token detected during login; assuming success")
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            return True

        if time.time() > deadline:
            proc.kill()
            out, err = proc.communicate()
            stdout_acc += (out or "")
            stderr_acc += (err or "")
            notify(
                "SSO ログインがタイムアウトしました",
                f"{effective_timeout}秒以内にログインが完了しませんでした。ネットワークやブラウザを確認してください。"
            )
            return False

        time.sleep(1)

    if proc.returncode != 0:
        detail = stderr_acc.strip() or stdout_acc.strip() or "理由不明のエラー"
        notify("SSO ログインに失敗しました", f"ネットワークや認証状態を確認してください。\n{detail}")
        log_message(f"SSO login failed (code {proc.returncode}): {detail}")
        return False

    if not is_sso_session_valid():
        detail = "STS が失敗しました (セッションが無効の可能性)"
        notify("SSO ログインに失敗しました", detail)
        log_message(f"SSO login failed after process success: {detail}")
        return False

    log_message("SSO login succeeded")
    global LAST_DEVICE_URL, LAST_DEVICE_CODE
    if device_url:
        LAST_DEVICE_URL = device_url
    if code_hint:
        LAST_DEVICE_CODE = code_hint
    if stdout_acc.strip():
        log_message(f"SSO login stdout: {stdout_acc.strip()}")
    if stderr_acc.strip():
        log_message(f"SSO login stderr: {stderr_acc.strip()}")
    return True


def initial_sso_login() -> None:
    """
    起動時に SSO トークンを確認し、必要ならブラウザログインを実行。
    トークンが無ければ必ず実行し、成功しなければ終了。
    """
    token_ok, remaining = check_sso_token()
    session_ok = token_ok and is_sso_session_valid()

    if token_ok and session_ok and remaining is not None and remaining > SSO_AUTO_REFRESH_MARGIN:
        log_message(f"Skip initial SSO login (remaining {remaining:.0f}s)")
        return

    # トークンが無い、またはセッション無効なら認可画面を前面で開く
    if not session_ok:
        global LAST_DEVICE_URL, LAST_DEVICE_CODE
        LAST_DEVICE_URL = SSO_URL
        open_webview_minimized(SSO_URL)
        bring_webview_to_front()

    notify("SSO ログインが必要です", "ブラウザで SSO ログインを開始します。")
    if not run_sso_login(timeout=SSO_LOGIN_TIMEOUT):
        notify("SSO ログインに失敗しました", "トークンを取得できませんでした。ネットワークや認証情報を確認してください。")
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
        self.reauth_in_progress = False
        self.last_device_url = None
        self.last_device_code = None
        self.initial_cache_missing = not token_cache_exists()

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
            if token_ok:
                # トークンファイルは残っていてもセッションが無効化されている場合があるので STS で確認
                if not is_sso_session_valid():
                    token_ok = False

            # --- SSO トークンの状態に応じて自動リフレッシュ ---
            if token_ok and remaining is not None:
                self.notified_token_invalid = False
                self.reauth_in_progress = False
                self.initial_cache_missing = False
                if remaining <= SSO_AUTO_REFRESH_MARGIN and not self.auto_refresh_in_progress:
                    self.auto_refresh_in_progress = True
                    threading.Thread(target=self._try_auto_refresh, daemon=True).start()
            else:
                if not self.notified_token_invalid:
                    msg = "ブラウザで SSO ログインを行ってください。"
                    if self.initial_cache_missing:
                        msg = "ここをクリックして SSO ログインを行ってください。"
                    opened = open_webview_minimized(LAST_DEVICE_URL or SSO_URL)
                    if not opened:
                        try:
                            subprocess.run(["gio", "open", LAST_DEVICE_URL or SSO_URL], check=False)
                        except Exception:
                            pass
                    bring_webview_to_front()
                    notify("AWS SSO トークンが無効です", msg)
                    bring_webview_to_front()
                    self.notified_token_invalid = True
                if not self.reauth_in_progress:
                    self.reauth_in_progress = True
                    threading.Thread(target=self._force_reauth, daemon=True).start()

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

    def _force_reauth(self):
        """
        トークン無効時に強制的に再認証を走らせる。
        成功したらトンネルが落ちている場合は再接続する。
        """
        success = run_sso_login(timeout=SSO_LOGIN_TIMEOUT)
        self.reauth_in_progress = False
        if success:
            log_message("SSO re-auth succeeded after token invalid")
            self.notified_token_invalid = False
            if not is_port_forward_alive():
                start_port_forward()
        else:
            notify(
                "SSO 再ログインが必要です",
                "トークンが無効です。ブラウザで再ログインしてください。"
            )
# ------------------------------------------------------------
# エントリーポイント
# ------------------------------------------------------------

def main():
    # OS を確認し、SSO ログインを実行してから前提条件チェック・トレイ起動
    ensure_supported_os()
    initial_sso_login()
    ensure_prerequisites()
    app = TrayApp()
    Gtk.main()

if __name__ == "__main__":
    main()
