from typing import Callable, Optional, Dict, Any, Tuple, List
from dataclasses import dataclass
from pathlib import Path
import os
import threading
import json
import subprocess
import sys
import re
import multiprocessing
import contextlib
import select
import time
import webbrowser
import queue
import tempfile
from urllib.parse import urlparse, parse_qs

try:
    import wx
    import wx.adv
except ImportError:
    wx = None

try:
    import webview
except Exception:
    webview = None

from .paths import ICON_DIR, CONFIG_PATH, CONFIG_EXAMPLE_PATH, APP_ROOT
from .logger import log_message, notify
from .config_loader import Config, ForwardTarget, load_config
from . import installer_core
from .sso import check_sso_token, is_sso_session_valid, start_sso_login_browser
from .ssm import SsmPortForward, _cleanup_lingering_sessions
from . import platforms

WEBVIEW_STORAGE_DIR = os.path.expanduser("~/.cache/aws_conn_tray/webview")
QT_WEBVIEW_STORAGE_DIR = os.path.expanduser("~/.cache/aws_conn_tray/qt-webview")
# 全ミニダイアログ共通のサイズ（横幅20文字程度が収まる想定）。前回より少し高さを戻してコンパクトに。
MINI_PROMPT_SIZE = (360, 320)
# 行数が増えるメッセージにも対応できる高さ（MINI_PROMPT_SIZE と同値で統一）
MINI_PROMPT_HEIGHT = 320
# 設定ダイアログの最小サイズ（GTK の高さ0警告を防ぎ、コンテンツが収まる寸法）
SETTINGS_DIALOG_MIN_SIZE = (560, 560)
# 認可URL取得失敗時の統一メッセージ（手動で改行を入れて 20 文字前後に収める）
FAIL_AUTH_URL_MESSAGE = (
    "認可URLの取得に失敗しました。\n"
    "アプリアイコンのクリックメニューの\n"
    "「SSO ログイン」、又は、\n"
    "右下の「開く」ボタンから\n"
    "ログインしてください。\n"
    "（AWS CLI が未設定/\n"
    "ネットワーク不通などの\n"
    "可能性があります）"
)
DEVICE_CODE_MESSAGE = (
    "SSO ログイン用の\n"
    "認証コードです。\n"
    "ログイン画面で\n"
    "必要ならコードを\n"
    "入力してください。"
)
DEVICE_CODE_FALLBACK_MESSAGE = (
    "SSO ログイン用の\n"
    "認証コードです。\n"
    "右下の「開く」で\n"
    "ログイン画面を開き、\n"
    "必要ならコードを\n"
    "入力してください。"
)
WEBVIEW_FAILED_MESSAGE = (
    "認証画面を開けませんでした。\n"
    "右下の「開く」から\n"
    "ブラウザでログインしてください。"
)
WEBVIEW_FAILED_DEVICE_MESSAGE = (
    "認証画面を開けませんでした。\n"
    "右下の「開く」で\n"
    "ログイン画面を開き、\n"
    "必要ならコードを\n"
    "入力してください。"
)
WEBVIEW_CLOSED_MESSAGE = (
    "認証画面が閉じられました。\n"
    "右下の「開く」から\n"
    "再度ログインしてください。"
)
WEBVIEW_CLOSED_DEVICE_MESSAGE = (
    "認証画面が閉じられました。\n"
    "右下の「開く」で\n"
    "ログイン画面を開き、\n"
    "必要ならコードを\n"
    "入力してください。"
)
# Qt WebView サブプロセス管理用
_qt_webview_procs: List[subprocess.Popen] = []
SSO_LOGIN_CANCEL_GRACE_SECONDS = 5.0
_sso_login_proc: Optional[subprocess.Popen] = None
_sso_login_proc_profile: Optional[str] = None
_sso_login_proc_region: Optional[str] = None
_sso_login_proc_lock = threading.Lock()

try:
    import pty

    PTY_AVAILABLE = True
except Exception:
    PTY_AVAILABLE = False


def _has_display():
    if wx is None:
        return False
    try:
        # wx.App.IsDisplayAvailable は App 未生成でも利用可能
        if hasattr(wx.App, "IsDisplayAvailable"):
            return bool(wx.App.IsDisplayAvailable())
        # 古い環境向けフォールバック
        return wx.Display.GetCount() > 0
    except Exception:
        return False


@dataclass
class ForwardRuntime:
    """
    フォワード定義と実行状態をまとめて持つコンテナ。
    - forward: 設定値そのもの
    - ssm: 実際に起動する SsmPortForward インスタンス
    - retry_count / notified_port: ポート衝突時のリトライ管理
    - status: tray 表示用の状態 (green/yellow/red)
    """

    forward: ForwardTarget
    ssm: SsmPortForward
    retry_count: int = 0
    notified_port: bool = False
    status: str = "red"


class WxTrayIcon(wx.adv.TaskBarIcon):
    """
    トレイアイコンとメニュー。
    """

    def __init__(
        self,
        parent=None,
        on_settings: Optional[Callable[[], None]] = None,
        on_sso_login: Optional[Callable[[], None]] = None,
        on_reconnect_all: Optional[Callable[[], None]] = None,
        on_reconnect_single: Optional[Callable[[str], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
        on_quit_disable_autostart: Optional[Callable[[], None]] = None,
        forward_statuses: Optional[Dict[str, str]] = None,
    ):
        super().__init__()
        self.parent = parent
        self.cb_settings = on_settings
        self.cb_sso_login = on_sso_login
        self.cb_reconnect_all = on_reconnect_all
        self.cb_reconnect_single = on_reconnect_single
        self.cb_quit = on_quit
        self.cb_quit_disable_autostart = on_quit_disable_autostart
        self.forward_statuses: Dict[str, str] = forward_statuses or {}
        self._status_bitmap_cache: Dict[str, "wx.Bitmap"] = {}
        self.set_icon_color("red")
        # 左右クリックどちらでもメニューを開けるようにする
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DOWN, self._on_taskbar_click)
        self.Bind(wx.adv.EVT_TASKBAR_RIGHT_DOWN, self._on_taskbar_click)

    @staticmethod
    def menu_items():
        """
        メニュー構造の定義を返す（ラベル, ハンドラ名）。
        テストで GUI に依存せず確認できるようにデータ化。
        """
        return [
            ("⚙️ 設定", "on_settings"),
            ("SSO ログイン", "on_sso_login"),
            ("全フォワード再接続", "on_reconnect_all"),
            (None, None),  # separator
            ("自動起動解除して終了（最終手段）", "on_quit_disable_autostart"),
            ("終了", "on_quit"),
        ]

    def CreatePopupMenu(self):
        menu = wx.Menu()
        for label, handler_name in self.menu_items():
            if label is None:
                menu.AppendSeparator()
                continue
            item = menu.Append(-1, label)
            handler = getattr(self, handler_name, None)
            if handler:
                self.Bind(wx.EVT_MENU, handler, item)
        # 状態表示 + 個別再接続を後段に追加
        if self.forward_statuses:
            menu.AppendSeparator()
            # メインメニュー上に状態表示セクションを設け、ヘッダーで区切って見分けやすくする
            header = menu.Append(-1, "▼ フォワード状態（クリック不可）")
            header.Enable(False)
            for name, status in self.forward_statuses.items():
                item = menu.Append(-1, f"  {name}")
                item.Enable(False)  # 状態表示のみ（クリック不可）
                try:
                    bmp = self._get_status_bitmap(status)
                    if bmp and bmp.IsOk():
                        item.SetBitmap(bmp)
                        # 無効化でも色付きビットマップを維持
                        if hasattr(item, "SetDisabledBitmap"):
                            item.SetDisabledBitmap(bmp)
                except Exception:
                    pass
            menu.AppendSeparator()
            # 個別再接続
            for name in self.forward_statuses.keys():
                item = menu.Append(-1, f"{name} を再接続")
                self.Bind(wx.EVT_MENU, lambda evt, n=name: self.on_reconnect_single(n), item)
        return menu

    def _on_taskbar_click(self, event):
        menu = self.CreatePopupMenu()
        if menu:
            self.PopupMenu(menu)
            menu.Destroy()
        event.Skip()

    def set_icon_color(self, color: str):
        try:
            path = os.path.join(ICON_DIR, f"{color}.png")
            icon = wx.Icon(path, wx.BITMAP_TYPE_PNG)
            self.SetIcon(icon, "aws-conn-tray")
        except Exception as e:
            log_message(f"[wx] icon set failed: {e}")

    def _get_status_bitmap(self, status: str) -> Optional["wx.Bitmap"]:
        if wx is None:
            return None
        if status in self._status_bitmap_cache:
            return self._status_bitmap_cache[status]
        color_map = {
            "green": wx.Colour(34, 160, 0),
            "yellow": wx.Colour(226, 160, 0),
            "red": wx.Colour(200, 60, 50),
        }
        color = color_map.get(status, wx.Colour(136, 136, 136))
        size = 14
        try:
            bmp = wx.Bitmap(size, size, 32)
        except Exception:
            bmp = wx.Bitmap(size, size)
        dc = wx.MemoryDC(bmp)
        try:
            dc.SetBackground(wx.Brush(wx.Colour(0, 0, 0, 0)))
            dc.Clear()
            gc = wx.GraphicsContext.Create(dc)
            if gc:
                gc.SetBrush(wx.Brush(color))
                gc.SetPen(wx.Pen(wx.Colour(0, 0, 0, 0)))
                gc.DrawEllipse(1, 1, size - 2, size - 2)
            else:
                dc.SetBrush(wx.Brush(color))
                dc.SetPen(wx.Pen(color))
                dc.DrawCircle(size // 2, size // 2, (size - 2) // 2)
        finally:
            dc.SelectObject(wx.NullBitmap)
        self._status_bitmap_cache[status] = bmp
        return bmp

    def on_settings(self, _evt=None):
        if self.cb_settings:
            self.cb_settings()

    def on_sso_login(self, _evt=None):
        if self.cb_sso_login:
            self.cb_sso_login()

    def on_reconnect(self, _evt=None):
        # 互換用: 全体再接続にフォールバック
        if self.cb_reconnect_all:
            self.cb_reconnect_all()

    def on_reconnect_all(self, _evt=None):
        if self.cb_reconnect_all:
            self.cb_reconnect_all()

    def on_reconnect_single(self, name: str):
        if self.cb_reconnect_single:
            self.cb_reconnect_single(name)

    def on_quit(self, _evt=None):
        if self.cb_quit:
            self.cb_quit()
        try:
            wx.CallAfter(lambda: wx.GetApp().ExitMainLoop())
        except Exception:
            wx.CallAfter(lambda: wx.GetApp().ExitMainLoop())

    def on_quit_disable_autostart(self, _evt=None):
        if self.cb_quit_disable_autostart:
            self.cb_quit_disable_autostart()
        try:
            wx.CallAfter(lambda: wx.GetApp().ExitMainLoop())
        except Exception:
            wx.CallAfter(lambda: wx.GetApp().ExitMainLoop())


class WxTrayApp:
    """
    wxPython ベースのトレイアプリ。
    - 隠しフレーム + TaskBarIcon で常駐
    - SSO/SSM/設定ダイアログ/ミニダイアログ/Qt WebView を統合的に扱う
    - wx が無い、または DISPLAY 無しの場合は ImportError/RuntimeError を投げる。
    """

    def __init__(self):
        if wx is None:
            raise ImportError("wxPython is not installed.")
        if not _has_display():
            raise RuntimeError("DISPLAY が利用できません。GUI 環境で実行してください。")
        self._app = wx.App(clearSigInt=False)
        # tray_app への参照を app に保持しておき、フォールバック時に利用する
        try:
            self._app._tray_app = self
        except Exception:
            pass
        # Top-level が無いとすぐ終了する環境があるため、ダミーフレームで維持
        self._app.SetExitOnFrameDelete(False)
        self._frame = wx.Frame(None)
        self._frame.Hide()
        # 通知用にトップウィンドウを明示しておく
        self._app.SetTopWindow(self._frame)
        self.tray_icon: Optional[WxTrayIcon] = None
        self.config: Optional[Config] = None
        self.forward_states: Dict[str, ForwardRuntime] = {}
        self.timer: Optional[wx.Timer] = None
        self._refreshing = False
        self._notified_invalid = False
        self._last_webview_frame: Optional[wx.Frame] = None
        self._mini_prompt: Optional["MiniPrompt"] = None
        self._mini_prompt_mode: Optional[str] = None
        self._webview_running = False
        self._shutting_down = False
        # マルチフォワードの代表（認可URL取得などで使用）をキャッシュする
        self._primary_forward: Optional[ForwardTarget] = None

    def OnInit(self):
        return True

    def init_tray(self, callbacks: dict, config: Optional[Config] = None):
        """
        設定を読み込み、フォワード実行体を初期化し、トレイアイコンと監視タイマーをセット。
        callbacks にはメニュー操作のハンドラを渡せる（未指定はデフォルトにフォールバック）。
        - forward_states に ForwardRuntime を構築（ログ用 name を保持）
        - タスクトレイに状態インジケータ + 個別再接続メニューを用意
        - wx.Timer でトークン/トンネルの監視ループを開始
        """
        self.config = config or load_config()
        # 複数フォワードを ForwardRuntime として準備
        self.forward_states = {}
        for fw in self.config.forwards:
            self.forward_states[fw.name] = ForwardRuntime(forward=fw, ssm=SsmPortForward(fw))
        # 認可URL取得等で利用する代表フォワード（先頭）を保持
        self._primary_forward = self.config.forwards[0] if self.config.forwards else None
        # コールバックは dict で受け取る（未設定は None）
        callbacks = callbacks or {}
        callbacks.setdefault("settings", self.on_settings)
        callbacks.setdefault("sso_login", self.on_sso_login)
        callbacks.setdefault("reconnect_all", self.on_reconnect_all)
        callbacks.setdefault("reconnect_single", self.on_reconnect_single)
        callbacks.setdefault("quit", self.on_quit)
        callbacks.setdefault("quit_disable_autostart", self.on_quit_disable_autostart)
        self.tray_icon = WxTrayIcon(
            parent=self._frame,
            on_settings=callbacks.get("settings"),
            on_sso_login=callbacks.get("sso_login"),
            on_reconnect_all=callbacks.get("reconnect_all"),
            on_reconnect_single=callbacks.get("reconnect_single"),
            on_quit=callbacks.get("quit"),
            on_quit_disable_autostart=callbacks.get("quit_disable_autostart"),
            forward_statuses={name: "red" for name in self.forward_states.keys()},
        )
        # 監視タイマー
        self._start_monitor()

    def _start_monitor(self):
        if self.timer:
            try:
                self.timer.Stop()
            except Exception:
                pass
        self.timer = wx.Timer(self._app)
        self._app.Bind(wx.EVT_TIMER, lambda evt: self.monitor_once(), self.timer)
        self.timer.Start(self.config.tunnel_check_interval_seconds * 1000)
        self.monitor_once()

    def _get_primary_forward(self) -> Optional[ForwardTarget]:
        """
        認可URL取得などで使用する代表フォワードを返す。
        - 既にキャッシュがあればそれを返す。
        - forward_states があれば先頭を採用。
        - config.forwards があれば先頭を採用。
        - レガシーな config.profile/region 形式があればダミーの ForwardTarget を生成して利用する。
        """
        if getattr(self, "_primary_forward", None):
            return self._primary_forward
        # forward_states があれば最初のものを利用
        if getattr(self, "forward_states", None):
            try:
                first_state = next(iter(self.forward_states.values()))
                self._primary_forward = first_state.forward
                return self._primary_forward
            except Exception:
                pass
        # config.forwards から取得
        if self.config and getattr(self.config, "forwards", None):
            try:
                self._primary_forward = self.config.forwards[0]
                return self._primary_forward
            except Exception:
                pass
        # レガシー形式へのフォールバック（テスト用スタブ互換）
        if self.config and hasattr(self.config, "profile"):
            try:
                self._primary_forward = ForwardTarget(
                    name="default",
                    instance_id=getattr(self.config, "instance_id", ""),
                    profile=getattr(self.config, "profile", ""),
                    region=getattr(self.config, "region", ""),
                    local_port=int(getattr(self.config, "local_port", 0) or 0),
                    remote_host=getattr(self.config, "remote_host", ""),
                    remote_port=int(getattr(self.config, "remote_port", 0) or 0),
                )
                return self._primary_forward
            except Exception:
                pass
        return None

    def MainLoop(self):
        self._app.MainLoop()

    def IsMainLoopRunning(self):
        return self._app.IsMainLoopRunning()

    def shutdown(self):
        self._shutting_down = True
        if self.tray_icon:
            try:
                self.tray_icon.Destroy()
            except Exception:
                pass
        if hasattr(self, "_frame") and self._frame:
            try:
                self._frame.Destroy()
            except Exception:
                pass
        if self.timer:
            try:
                self.timer.Stop()
            except Exception:
                pass
        # 複数フォワードを停止
        for state in (getattr(self, "forward_states", {}) or {}).values():
            try:
                if state.ssm.is_alive():
                    state.ssm.stop()
                else:
                    # 念のため stop でプロセスを明示的に解放
                    state.ssm.stop()
            except Exception as e:
                log_message(f"[ssm] [{state.forward.name}] stop during shutdown failed: {e}")
        # 残存 session-manager-plugin / ssm start-session の掃除（安全のため）
        try:
            _cleanup_lingering_sessions("shutdown")
        except Exception as e:
            log_message(f"[ssm] cleanup during shutdown failed: {e}")
        # Qt WebView サブプロセスを強制終了しておく
        try:
            cleanup_qt_webview_procs(force=True)
        except Exception as e:
            log_message(f"[qt-webview] cleanup during shutdown failed: {e}")
        try:
            self._app.ExitMainLoop()
        except Exception:
            try:
                wx.CallAfter(self._app.ExitMainLoop)
            except Exception:
                pass

    # ===== 監視ロジック =====
    def monitor_once(self):
        if not self.config:
            return
        if getattr(self, "_shutting_down", False):
            return
        token_ok, remaining = check_sso_token(expected_start_url=self.config.sso_start_url)
        # 各フォワードはプロファイルが異なる可能性があるため、STS 成否をフォワード単位で確認する
        forward_statuses: Dict[str, str] = {}

        # 期限内かつ margin 以内なら自動更新を試みる
        if token_ok and remaining is not None:
            if remaining <= self.config.sso_auto_refresh_margin_seconds:
                if not self._refreshing:
                    self._refreshing = True
                    self._start_sso_refresh()
            else:
                # 余裕があればリセット
                self._refreshing = False

        if token_ok:
            self._notified_invalid = False
            self._close_mini_prompt()
            for name, state in self.forward_states.items():
                # プロファイルごとに STS が成功するかを確認
                session_ok = is_sso_session_valid(state.forward.profile, state.forward.region)
                if not session_ok:
                    # セッション無効なら再ログインを促す
                    forward_statuses[name] = "red"
                    state.status = "red"
                    continue

                if not state.ssm.is_alive():
                    # トークン有効だがトンネル未接続 → 再接続を試行
                    forward_statuses[name] = "yellow"
                    self.tray_icon.set_icon_color("yellow")
                    started = state.ssm.start()
                    if not started:
                        state.retry_count += 1
                        if not state.notified_port:
                            notify("ポートフォワード", f"{name}: ローカルポート {state.forward.local_port} が使用中です。")
                            state.notified_port = True
                        if state.retry_count >= 3:
                            msg = f"{name}: ローカルポート {state.forward.local_port} が使用中です。再接続を中止します。"
                            notify("ポートフォワード", msg)
                            log_message(f"[ssm] [{name}] port still in use after retries -> giving up")
                            forward_statuses[name] = "red"
                            state.status = "red"
                            continue
                        state.status = "yellow"
                    else:
                        state.notified_port = False
                        state.retry_count = 0
                        state.status = "yellow"
                else:
                    state.notified_port = False
                    state.retry_count = 0
                    forward_statuses[name] = "green"
                    state.status = "green"
        else:
            # トークン無効時はミニダイアログで WebView 誘導
            if not self._notified_invalid:
                msg = "SSO トークンが無効です。\n右下の「開く」またはメニューの「SSO ログイン」から再認証してください。"
                self._show_sso_prompt(self.config.sso_start_url, msg)
                log_message("[wx-monitor] token invalid, prompting login")
                self._notified_invalid = True
            # 全フォワードを赤扱い
            for name in self.forward_states.keys():
                forward_statuses[name] = "red"
                self.forward_states[name].status = "red"

        # 集計してトレイアイコン色を決定
        if forward_statuses:
            if any(status == "red" for status in forward_statuses.values()):
                icon_color = "red"
            elif any(status == "yellow" for status in forward_statuses.values()):
                icon_color = "yellow"
            else:
                icon_color = "green"
            # 最終的な集計結果でアイコンを 1 回だけ更新
            self.tray_icon.set_icon_color(icon_color)
            # メニュー側の状態表示にも反映
            self.tray_icon.forward_statuses = forward_statuses

    def _start_sso_refresh(self):
        """
        マージン内で自動リフレッシュを試みる。スレッドで実行し、URLが取れたら自動でWebViewを開く。
        """

        def worker():
            try:
                forward = self._get_primary_forward()
                if not forward:
                    log_message("[wx-refresh] no primary forward, skip refresh")
                    return

                def call_ui(fn: Callable[[], None]):
                    try:
                        if wx.GetApp() is not None and hasattr(wx, "CallAfter"):
                            wx.CallAfter(fn)
                        else:
                            fn()
                    except Exception as e:
                        log_message(f"[wx-refresh] call_ui error: {e}")

                url, code = _fetch_authorize_url(
                    forward.profile,
                    forward.region,
                    self.config.sso_start_url,
                    timeout=self.config.sso_login_timeout_seconds,
                )
                if url:
                    if "authorize" in url.lower():
                        log_message(f"[wx-refresh] authorize url fetched: {url}")
                    else:
                        log_message(f"[wx-refresh] device url fallback: {url}")
                        user_code = _extract_user_code(url, code)
                        call_ui(lambda: self._show_device_code_prompt(url, user_code))
                    # 自動で WebView を開き、失敗時は MiniPrompt/通知がフォールバック
                    call_ui(lambda: start_sso_login_webview(
                        url,
                        timeout_seconds=self.config.sso_login_timeout_seconds,
                        user_code=_extract_user_code(url, code),
                    ))
                else:
                    # 自動更新時も同じ考え方でブラウザ誘導に切り替える
                    call_ui(lambda: self._show_browser_prompt(self.config.sso_start_url, FAIL_AUTH_URL_MESSAGE))
            finally:
                self._refreshing = False

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    # ===== メニュー操作ハンドラ =====
    def on_settings(self):
        defaults = _load_example_config()
        current = _config_to_dict(self.config)
        new_data = self._show_settings_dialog(current, defaults)
        if not new_data:
            return
        ok, errors, parsed = validate_settings_input(new_data, defaults)
        if not ok:
            notify("入力エラー", "\n".join(errors))
            return
        if parsed != current:
            _save_config(parsed)
            notify("設定を保存しました", "アプリを再起動します。")
            try:
                subprocess.Popen([sys.executable, os.path.join(APP_ROOT, "tray_app.py")])
            except Exception as e:
                log_message(f"[wx-settings] restart failed: {e}")
            self.shutdown()

    def on_sso_login(self):
        # メニューからも認可URL取得→WebView起動を統一
        notify("SSO ログイン", "認証画面を開きます（フォーカスは奪いません）。")
        self._launch_webview(self.config.sso_start_url)
        self._refreshing = False
        self._notified_invalid = False

    def on_reconnect(self):
        # 後方互換: 全フォワード再接続に委譲
        self.on_reconnect_all()

    def on_reconnect_all(self):
        for name, state in self.forward_states.items():
            self._reconnect_forward(name, state)

    def on_reconnect_single(self, name: str):
        state = self.forward_states.get(name)
        if not state:
            notify("ポートフォワード", f"{name}: 設定が見つかりません。")
            return
        self._reconnect_forward(name, state)

    def _reconnect_forward(self, name: str, state: ForwardRuntime):
        """
        個別フォワードの再接続処理。状態と通知の更新もここで行う。
        """
        if state.ssm.is_alive():
            notify("ポートフォワード", f"{name}: すでにフォワードは有効です。")
            state.status = "green"
            return
        if self.tray_icon:
            self.tray_icon.set_icon_color("yellow")
        started = state.ssm.start()
        if not started:
            notify("ポートフォワード", f"{name}: ローカルポート {state.forward.local_port} が使用中です。")
            state.retry_count += 1
            state.status = "red"
            return
        state.retry_count = 0
        state.notified_port = False
        state.status = "yellow"

    def on_quit(self):
        self.shutdown()

    def on_quit_disable_autostart(self):
        try:
            installer_core.disable_autostart(dry_run=False)
        except Exception as e:
            notify("自動起動解除", f"自動起動の解除に失敗しました: {e}")
        self.shutdown()

    def _show_settings_dialog(self, current: Dict[str, Any], defaults: Dict[str, Any]):
        """
        設定ダイアログ。wx が無い場合や DISPLAY が無い場合は None を返す。
        - forwards をタブで編集できるようにし、「追加」ボタンでタブを増やす。
        - タブ名は name を入力するとリアルタイムに反映する。
        - instance_id / profile / region / remote_host / sso_start_url にはペーストボタンを付与。
        - グローバル数値設定にはデフォルト値復元ボタンを付与。
        - 必須/数値のバリデーションは validate_settings_input で実施。
        - ShowModal の戻り値が OK なら dict を返し、キャンセル/閉じるなら None。
        """
        if wx is None or not _has_display():
            return None
        # 親を隠しフレームに紐付けておくと GTK でのリサイズ警告が減る
        dlg = wx.Dialog(self._frame or None, title="設定", size=SETTINGS_DIALOG_MIN_SIZE)
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)

        # forwards をタブで管理
        notebook = wx.Notebook(panel)
        forward_entries: List[Dict[str, wx.TextCtrl]] = []

        def _paste_to_ctrl(ctrl: wx.TextCtrl):
            """
            クリップボードのテキストを取得して指定の TextCtrl に貼り付ける。
            失敗しても例外は出さずログだけ残す。
            """
            try:
                if not wx.TheClipboard.IsOpened():
                    wx.TheClipboard.Open()
                data = wx.TextDataObject()
                if wx.TheClipboard.GetData(data):
                    ctrl.SetValue(data.GetText())
                wx.TheClipboard.Close()
            except Exception as e:
                log_message(f"[settings] paste failed: {e}")

        def create_forward_page(initial: Dict[str, Any]):
            page = wx.Panel(notebook)
            grid = wx.FlexGridSizer(rows=0, cols=2, hgap=8, vgap=8)
            grid.AddGrowableCol(1, 1)
            controls: Dict[str, wx.TextCtrl] = {}

            paste_targets = {"instance_id", "profile", "region", "remote_host"}

            def add_field(label, key, value=""):
                grid.Add(wx.StaticText(page, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
                hs = wx.BoxSizer(wx.HORIZONTAL)
                txt = wx.TextCtrl(page, value=str(value))
                controls[key] = txt
                hs.Add(txt, 1, wx.EXPAND | wx.RIGHT, 4)
                if key in paste_targets:
                    btn = wx.Button(page, label="ペースト", size=(70, 24))
                    btn.Bind(wx.EVT_BUTTON, lambda _evt, t=txt: _paste_to_ctrl(t))
                    hs.Add(btn, 0)
                grid.Add(hs, 1, wx.EXPAND)

            add_field("名前", "name", initial.get("name", ""))
            add_field("インスタンスID", "instance_id", initial.get("instance_id", ""))
            add_field("プロファイル", "profile", initial.get("profile", ""))
            add_field("リージョン", "region", initial.get("region", ""))
            add_field("リモートホスト", "remote_host", initial.get("remote_host", ""))
            add_field("リモートポート", "remote_port", initial.get("remote_port", defaults.get("remote_port", 5432)))
            add_field("ローカルポート", "local_port", initial.get("local_port", defaults.get("local_port", 15432)))

            page.SetSizer(grid)
            notebook.AddPage(page, initial.get("name", "新規"))

            # 名前変更時にタブタイトルも更新する
            def on_name_change(evt):
                title = evt.GetString() or "新規"
                idx = notebook.GetPageIndex(page)
                if idx >= 0:
                    notebook.SetPageText(idx, title)
                evt.Skip()

            controls["name"].Bind(wx.EVT_TEXT, on_name_change)
            forward_entries.append(controls)

        forwards_current = current.get("forwards") or []
        if not forwards_current:
            forwards_current = [
                {
                    "name": "rds",
                    "instance_id": "",
                    "profile": "",
                    "region": "",
                    "remote_host": "",
                    "remote_port": defaults.get("remote_port", 5432),
                    "local_port": defaults.get("local_port", 15432),
                }
            ]
        for fw in forwards_current:
            create_forward_page(fw)

        sizer.Add(notebook, 1, wx.ALL | wx.EXPAND, 8)

        # タブ追加ボタン
        def on_add_tab(_evt=None):
            idx = len(forward_entries) + 1
            create_forward_page(
                {
                    "name": f"forward-{idx}",
                    "instance_id": "",
                    "profile": "",
                    "region": "",
                    "remote_host": "",
                    "remote_port": defaults.get("remote_port", 5432),
                    "local_port": defaults.get("local_port", 15432),
                }
            )
            notebook.ChangeSelection(notebook.GetPageCount() - 1)

        btn_add = wx.Button(panel, label="追加")
        btn_add.Bind(wx.EVT_BUTTON, on_add_tab)
        sizer.Add(btn_add, 0, wx.LEFT | wx.BOTTOM, 8)

        # グローバル設定
        def add_global_field(label, key, default_val):
            hs = wx.BoxSizer(wx.HORIZONTAL)
            hs.Add(wx.StaticText(panel, label=label), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
            txt = wx.TextCtrl(panel, value=str(current.get(key, default_val)))
            global_entries[key] = txt
            hs.Add(txt, 1, wx.EXPAND)
            if key == "sso_start_url":
                btn = wx.Button(panel, label="ペースト", size=(70, 24))
                btn.Bind(wx.EVT_BUTTON, lambda _evt, t=txt: _paste_to_ctrl(t))
                hs.Add(btn, 0, wx.LEFT, 4)
            if key in {"sso_login_timeout_seconds", "sso_auto_refresh_margin_seconds", "tunnel_check_interval_seconds"}:
                btn_def = wx.Button(panel, label="デフォルト", size=(80, 24))
                btn_def.Bind(wx.EVT_BUTTON, lambda _evt, t=txt, v=default_val: t.SetValue(str(v)))
                hs.Add(btn_def, 0, wx.LEFT, 4)
            return hs

        global_entries: Dict[str, wx.TextCtrl] = {}
        sizer.Add(add_global_field("SSO Start URL", "sso_start_url", current.get("sso_start_url", "")), 0, wx.EXPAND | wx.ALL, 6)
        sizer.Add(add_global_field("SSO ログインタイムアウト(秒)", "sso_login_timeout_seconds", defaults.get("sso_login_timeout_seconds", 900)), 0, wx.EXPAND | wx.ALL, 6)
        sizer.Add(add_global_field("自動更新マージン(秒)", "sso_auto_refresh_margin_seconds", defaults.get("sso_auto_refresh_margin_seconds", 300)), 0, wx.EXPAND | wx.ALL, 6)
        sizer.Add(add_global_field("監視間隔(秒)", "tunnel_check_interval_seconds", defaults.get("tunnel_check_interval_seconds", 10)), 0, wx.EXPAND | wx.ALL, 6)

        # ボタン
        btns = wx.StdDialogButtonSizer()
        btn_ok = wx.Button(panel, wx.ID_OK)
        btn_cancel = wx.Button(panel, wx.ID_CANCEL)
        btns.AddButton(btn_ok)
        btns.AddButton(btn_cancel)
        btns.Realize()
        sizer.Add(btns, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        # レイアウトを確定させ、GTK が高さ0/幅負を計算しないよう最小サイズを明示する
        panel.SetSizer(sizer)
        sizer.Fit(panel)
        panel.Layout()
        dlg.Fit()
        dlg.SetMinSize(SETTINGS_DIALOG_MIN_SIZE)
        dlg.Layout()
        result = dlg.ShowModal()
        if result != wx.ID_OK:
            dlg.Destroy()
            return None
        # 入力値を dict に詰めて返す
        forwards_data: List[Dict[str, Any]] = []
        for controls in forward_entries:
            forwards_data.append({k: ctrl.GetValue() for k, ctrl in controls.items()})
        global_data = {k: ctrl.GetValue() for k, ctrl in global_entries.items()}
        dlg.Destroy()
        return {**global_data, "forwards": forwards_data}

    def _show_sso_prompt(self, auth_url: str, message: str, button_label: str = "開く", user_code: Optional[str] = None):
        """
        右下に出すミニダイアログを表示し、クリックで _launch_webview を呼ぶ。
        - 既存表示があれば閉じて新しい内容に差し替える
        - webview 起動中は二重起動を避ける
        """
        # 既存があればいったん閉じてメッセージを更新
        self._close_mini_prompt()
        self._mini_prompt_mode = "normal"

        # MiniPrompt に渡すコールバックは、このインスタンスのメソッドをバインドする
        def on_click():
            if self._webview_running:
                log_message("[webview-launch] skipped: webview already running")
                return
            try:
                launcher = getattr(self, "_launch_webview", None)
                if launcher is None:
                    log_message("[webview-launch] failed: _launch_webview missing")
                    return
                launcher(auth_url)
            except Exception as e:
                log_message(f"[webview-launch] failed: {e}")

        wrapped = _wrap_text_for_prompt(message, width=20)
        self._mini_prompt = MiniPrompt(message=wrapped, on_click=on_click, button_label=button_label, user_code=user_code)
        self._mini_prompt.show_bottom_right()

    def _close_mini_prompt(self):
        if self._mini_prompt:
            try:
                self._mini_prompt.Destroy()
            except Exception:
                pass
            self._mini_prompt = None
        self._mini_prompt_mode = None

    def _show_browser_prompt(self, url: str, message: str, user_code: Optional[str] = None):
        """
        右下ダイアログでブラウザ起動を促す（自動では開かない）。
        """
        self._close_mini_prompt()
        self._mini_prompt_mode = "normal"

        def on_click():
            try:
                webbrowser.open(url)
            except Exception as e:
                log_message(f"[webview-fallback] browser open failed: {e}")

        wrapped = _wrap_text_for_prompt(message, width=20)
        self._mini_prompt = MiniPrompt(message=wrapped, on_click=on_click, button_label="開く", user_code=user_code)
        self._mini_prompt.show_bottom_right()

    def _show_device_code_prompt(self, auth_url: str, user_code: Optional[str]):
        """
        device URL の場合は認証コード付きでミニダイアログを表示する。
        """
        if not user_code:
            return
        self._close_mini_prompt()
        self._mini_prompt_mode = "device_code"

        def on_click():
            if self._webview_running:
                log_message("[webview-launch] skipped: webview already running")
                return
            try:
                launcher = getattr(self, "_launch_webview", None)
                if launcher is None:
                    log_message("[webview-launch] failed: _launch_webview missing")
                    return
                launcher(auth_url)
            except Exception as e:
                log_message(f"[webview-launch] failed: {e}")

        wrapped = _wrap_text_for_prompt(DEVICE_CODE_MESSAGE, width=20)
        self._mini_prompt = MiniPrompt(message=wrapped, on_click=on_click, button_label=None, user_code=user_code)
        self._mini_prompt.show_bottom_right()

    def _launch_webview(self, fallback_url: str):
        """
        UI をブロックしないように認可URL取得→WebView起動をスレッドで実行。
        - authorize URL が取れなければ start_url をブラウザで開く誘導に切り替える
        - Qt WebView 起動中フラグ (_webview_running) を立てて二重起動を防止
        """
        def worker():
            self._webview_running = True
            try:
                forward = self._get_primary_forward()
                if not forward:
                    log_message("[webview-launch] no primary forward, abort")
                    return
                url_to_open = fallback_url
                is_device_url = False
                user_code = None
                log_message(f"[webview-launch] requested (fallback={fallback_url})")
                if not self.config:
                    log_message("[webview-launch] no config, abort")
                    return
                try:
                    log_message("[webview-launch] fetching authorize url...")
                    url, code = _fetch_authorize_url(
                        forward.profile,
                        forward.region,
                        self.config.sso_start_url,
                        timeout=self.config.sso_login_timeout_seconds,
                    )
                    if url:
                        url_to_open = url
                        if "authorize" in url.lower():
                            log_message(f"[webview-launch] authorize url fetched: {url}")
                            is_device_url = False
                        else:
                            log_message(f"[webview-launch] device url fallback: {url}")
                            is_device_url = True
                            user_code = _extract_user_code(url, code)
                            wx.CallAfter(self._show_device_code_prompt, url, user_code)
                    else:
                        # ここで繰り返し _show_sso_prompt を出すと「開く」→再取得失敗→再度「開く」で無限ループになる。
                        # 認可URLが取れない場合は AWS CLI やネットワーク前提が満たされていないと推測し、
                        # ユーザにブラウザで start_url を開いてもらう誘導に切り替える。
                        log_message("[webview-launch] authorize url not found, request retry")
                        wx.CallAfter(self._show_browser_prompt, fallback_url, FAIL_AUTH_URL_MESSAGE)
                        return
                except Exception as e:
                    # 例外発生時も同様にブラウザ誘導へ切り替える（繰り返しリトライは避ける）
                    log_message(f"[webview-launch] authorize url fetch failed: {e}")
                    wx.CallAfter(self._show_browser_prompt, fallback_url, FAIL_AUTH_URL_MESSAGE)
                    return

                ok = start_sso_login_webview(
                    url_to_open,
                    timeout_seconds=self.config.sso_login_timeout_seconds,
                    user_code=user_code if is_device_url else None,
                )
                log_message(f"[webview-launch] start_sso_login_webview result={ok}")
                if ok:
                    # 成功したらミニダイアログは閉じる
                    if getattr(self, "_mini_prompt_mode", None) != "device_code":
                        wx.CallAfter(self._close_mini_prompt)
                else:
                    if is_device_url:
                        wx.CallAfter(self._show_browser_prompt, url_to_open, DEVICE_CODE_FALLBACK_MESSAGE, user_code)
                    else:
                        wx.CallAfter(self._show_browser_prompt, fallback_url, FAIL_AUTH_URL_MESSAGE)
            finally:
                self._webview_running = False

        threading.Thread(target=worker, daemon=True).start()


# ===== WebView ヘルパー (pywebview) =====

def open_pywebview_authorize(url: str, storage_dir: Optional[str] = None) -> bool:
    """
    pywebview を使って認可URLを開く。storage_dir を指定してクッキー永続化。
    """
    if webview is None:
        return False
    storage_dir = storage_dir or WEBVIEW_STORAGE_DIR
    try:
        os.makedirs(storage_dir, exist_ok=True)

        # テスト用のモック環境では直接 create_window を呼ぶ
        if not hasattr(webview, "__file__"):
            # create_window のシグネチャを調べて永続化オプションを選択
            kw_storage: Dict[str, Any] = {}
            try:
                import inspect

                sig = inspect.signature(webview.create_window)
                if "storage_path" in sig.parameters:
                    kw_storage["storage_path"] = storage_dir
                elif "user_data_dir" in sig.parameters:
                    kw_storage["user_data_dir"] = storage_dir
            except Exception:
                pass

            window = webview.create_window("AWS SSO ログイン", url, width=800, height=600, **kw_storage)

            def check_and_close(_=None):
                try:
                    cur = (window.get_current_url() or "").lower()
                    if "127.0.0.1" in cur and "oauth/callback" in cur and "code=" in cur:
                        window.destroy()
                except Exception:
                    pass

            try:
                window.events.loaded += check_and_close
            except Exception:
                pass
            try:
                if hasattr(window.events, "navigation"):
                    window.events.navigation += check_and_close
            except Exception:
                pass
            webview.start()
            return True

        # 本番: サブプロセスで pywebview を起動（メインスレッド制約回避）
        helper_code = f"""
import webview, inspect
url = {url!r}
storage_dir = {storage_dir!r}
kw = {{}}
try:
    sig = inspect.signature(webview.create_window)
    if "storage_path" in sig.parameters:
        kw["storage_path"] = storage_dir
    elif "user_data_dir" in sig.parameters:
        kw["user_data_dir"] = storage_dir
except Exception:
    pass
win = webview.create_window("AWS SSO ログイン", url, width=800, height=600, **kw)
def check_and_close(_=None):
    try:
        cur = (win.get_current_url() or "").lower()
        if "127.0.0.1" in cur and "oauth/callback" in cur and "code=" in cur:
            win.destroy()
    except Exception:
        pass
try:
    win.events.loaded += check_and_close
    win.events.navigation += check_and_close
except Exception:
    pass
webview.start()
"""
        proc = subprocess.Popen([sys.executable, "-c", helper_code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_message(f"[pywebview] subprocess started pid={proc.pid} url={url}")
        return True
    except Exception as e:
        log_message(f"[pywebview] failed: {e}")
        return False


def cleanup_qt_webview_procs(force: bool = False, timeout: float = 3.0):
    """
    Qt WebView サブプロセスを掃除する。
    force=True なら terminate→kill まで行う。force=False は終了済みのものだけを除去。
    """
    global _qt_webview_procs
    next_procs: List[subprocess.Popen] = []
    for p in _qt_webview_procs:
        rc = p.poll()
        if rc is None:
            if not force:
                next_procs.append(p)
                continue
            try:
                log_message(f"[qt-webview] cleanup terminate pid={p.pid}")
                p.terminate()
                p.wait(timeout=timeout)
            except Exception:
                with contextlib.suppress(Exception):
                    log_message(f"[qt-webview] cleanup kill pid={p.pid}")
                    p.kill()
            rc = p.poll()
            log_message(f"[qt-webview] cleanup done pid={p.pid} rc={rc}")
        else:
            log_message(f"[qt-webview] already exited pid={getattr(p,'pid',None)} rc={rc}")
    _qt_webview_procs = next_procs


def open_qt_webview_authorize(
    url: str,
    storage_dir: Optional[str] = None,
    on_fail: Optional[Callable[[], None]] = None,
    on_close: Optional[Callable[[Optional[int], str, str], None]] = None,
) -> bool:
    """
    PySide6 + QtWebEngine をサブプロセスで起動して認可URLを開く。
    """
    cleanup_qt_webview_procs(force=False)
    storage_dir = storage_dir or QT_WEBVIEW_STORAGE_DIR
    try:
        os.makedirs(storage_dir, exist_ok=True)
    except Exception:
        pass
    cmd = [
        sys.executable,
        "-m",
        "aws_conn_tray.qwebview_runner",
        url,
        "--storage-path",
        storage_dir,
    ]
    env = os.environ.copy()
    if platforms.is_linux():
        env["QTWEBENGINE_CHROMIUM_FLAGS"] = (
            "--disable-gpu "
            "--disable-features=VaapiVideoDecoder "
            "--disable-dev-shm-usage"
        )
        env["LIBVA_DRIVER_NAME"] = "mesa"
        env["LIBGL_ALWAYS_SOFTWARE"] = "1"
        env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
        env["QT_QUICK_BACKEND"] = "software"
        env["QT_QPA_PLATFORM"] = "xcb"
        env["QT_OPENGL"] = "software"
    try:
        # Python が aws_conn_tray パッケージを確実に解決できるよう、cwd をアプリのルートに固定
        cwd = Path(APP_ROOT)
        popen_kwargs = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        if cwd:
            popen_kwargs["cwd"] = cwd
        try:
            proc = subprocess.Popen(cmd, **popen_kwargs)
        except TypeError:
            # テストのモックが cwd を受け取らない場合のフォールバック
            popen_kwargs.pop("cwd", None)
            proc = subprocess.Popen(cmd, **popen_kwargs)
        log_message(
            "[qt-webview] subprocess start "
            f"url={url} storage={storage_dir} "
            f"env_flags={{QTWEBENGINE_CHROMIUM_FLAGS={env.get('QTWEBENGINE_CHROMIUM_FLAGS')}, "
            f"LIBVA_DRIVER_NAME={env.get('LIBVA_DRIVER_NAME')}, "
            f"LIBGL_ALWAYS_SOFTWARE={env.get('LIBGL_ALWAYS_SOFTWARE')}}}"
        )
        _qt_webview_procs.append(proc)

        def _wait_and_log(p: subprocess.Popen, auth_url: str):
            try:
                out, err = p.communicate()
                rc = p.returncode
                log_message(f"[qt-webview] exit rc={rc} stdout={out!r} stderr={err!r}")
                if on_close:
                    try:
                        on_close(rc, out or "", err or "")
                    except Exception as e:
                        log_message(f"[qt-webview] on_close error: {e}")
                if rc not in (0, None) and on_fail:
                    try:
                        on_fail()
                    except Exception as e:
                        log_message(f"[qt-webview] on_fail error: {e}")
            except Exception as e:
                log_message(f"[qt-webview] wait error: {e}")
                if on_close:
                    try:
                        on_close(None, "", "")
                    except Exception as e2:
                        log_message(f"[qt-webview] on_close error: {e2}")

        threading.Thread(target=_wait_and_log, args=(proc, url), daemon=True).start()

        # すぐに落ちていないか簡易チェック（依存不足など）
        time.sleep(0.5)
        rc = proc.poll()
        if rc not in (None, 0):
            with contextlib.suppress(ValueError):
                _qt_webview_procs.remove(proc)
            if on_fail:
                try:
                    on_fail()
                except Exception as e:
                    log_message(f"[qt-webview] on_fail error: {e}")
            # すぐに落ちても on_fail を実行した上で True を返し、呼び出し側でフォールバック動作を継続させる
            return True
        # 終了したプロセスがあれば掃除
        cleanup_qt_webview_procs(force=False)
        return True
    except Exception as e:
        log_message(f"[qt-webview] failed to start: {e}")
        if on_fail:
            try:
                on_fail()
            except Exception as e2:
                log_message(f"[qt-webview] on_fail error: {e2}")
        # 失敗時も呼び出し側がブラウザフォールバックを続行できるよう True を返す
        return True


def start_sso_login_webview(url: str, timeout_seconds: Optional[int] = None, user_code: Optional[str] = None) -> bool:
    """
    認可URLを Qt WebEngine (PySide6) で開く。失敗時はミニダイアログ経由でブラウザフォールバック。
    """
    if not url:
        return False
    is_device_url = "authorize" not in url.lower()
    fail_message = WEBVIEW_FAILED_DEVICE_MESSAGE if is_device_url else WEBVIEW_FAILED_MESSAGE
    closed_message = WEBVIEW_CLOSED_DEVICE_MESSAGE if is_device_url else WEBVIEW_CLOSED_MESSAGE
    fallback_triggered = {"done": False}

    def _is_session_valid_now() -> bool:
        try:
            app = wx.GetApp()
            tray_app = getattr(app, "_tray_app", None) if app else None
            if not tray_app or not getattr(tray_app, "config", None):
                return False
            token_ok, _ = check_sso_token(expected_start_url=tray_app.config.sso_start_url)
            if not token_ok:
                return False
            forward = None
            try:
                forward = tray_app._get_primary_forward()
            except Exception:
                forward = None
            if forward:
                return is_sso_session_valid(forward.profile, forward.region)
            return True
        except Exception:
            return False

    def _fallback():
        # 右下ダイアログでブラウザ誘導。wx コンテキストが無い場合は notify にフォールバック。
        fallback_triggered["done"] = True
        log_message("[webview-fallback] showing browser prompt")
        if timeout_seconds:
            _schedule_sso_login_cancel("browser_fallback", grace_seconds=float(timeout_seconds))
        try:
            app = wx.GetApp()
            if app and hasattr(app, "_tray_app"):
                tray_app = getattr(app, "_tray_app")
            else:
                tray_app = None
        except Exception:
            tray_app = None

        if tray_app and hasattr(tray_app, "_show_browser_prompt"):
            try:
                tray_app._show_browser_prompt(
                    url,
                    fail_message,
                    user_code=user_code if is_device_url else None,
                )
                notify(
                    "SSO ログイン",
                    "WebView を開けませんでした。右下の「開く」からブラウザを開いてください。",
                    on_click=lambda: webbrowser.open(url),
                )
                return
            except Exception:
                pass
        notify(
            "SSO ログイン",
            "WebView を開けませんでした。通知をクリックしてブラウザで開いてください。",
            on_click=lambda: webbrowser.open(url),
        )

    def _on_close(rc: Optional[int], out: str, err: str):
        if rc != 0:
            return
        text = f"{out}\n{err}".lower()
        if "callback detected" in text or "/oauth/callback" in text:
            _schedule_sso_login_cancel("webview_closed")
            return
        if _is_session_valid_now():
            log_message("[webview-close] session valid; skip prompt")
            return
        if timeout_seconds:
            _schedule_sso_login_cancel("webview_closed", grace_seconds=float(timeout_seconds))
        try:
            app = wx.GetApp()
            tray_app = getattr(app, "_tray_app", None) if app else None
        except Exception:
            tray_app = None
        if tray_app and hasattr(tray_app, "_show_browser_prompt"):
            try:
                tray_app._show_browser_prompt(
                    url,
                    closed_message,
                    user_code=user_code if is_device_url else None,
                )
                return
            except Exception:
                pass
        notify(
            "SSO ログイン",
            "認証画面が閉じられました。通知をクリックしてブラウザで開いてください。",
            on_click=lambda: webbrowser.open(url),
        )

    if open_qt_webview_authorize(url, storage_dir=QT_WEBVIEW_STORAGE_DIR, on_fail=_fallback, on_close=_on_close):
        try:
            notify("SSO ログイン", "認証画面を最小化で開きました。タスクバーから表示できます。（ウィンドウ名: qwebview_runner.py）")
        except Exception:
            pass
        return True
    _fallback()
    return False


# ===== 設定バリデーション =====

FORWARD_REQUIRED_KEYS = ["name", "instance_id", "profile", "region", "remote_host"]
FORWARD_INT_KEYS = ["local_port", "remote_port"]
GLOBAL_INT_KEYS = [
    "sso_login_timeout_seconds",
    "sso_auto_refresh_margin_seconds",
    "tunnel_check_interval_seconds",
]


def validate_settings_input(data: Dict[str, Any], defaults: Dict[str, Any]):
    """
    設定ダイアログから受け取った値を検証し、正常なら数値を int に変換した dict を返す。
    - forwards: 必須。各 forward に必須文字列と数値を要求。
    - グローバルな数値項目も 0 以上の整数のみ許容。
    """
    errors = []
    parsed: Dict[str, Any] = {}

    forwards_raw = data.get("forwards") or []
    if not isinstance(forwards_raw, list) or not forwards_raw:
        errors.append("forwards: 1件以上のフォワード設定を追加してください。")
        return False, errors, {}

    forwards_parsed: List[Dict[str, Any]] = []
    for idx, fw in enumerate(forwards_raw):
        fw_parsed: Dict[str, Any] = {}
        for key in FORWARD_REQUIRED_KEYS:
            val = str(fw.get(key, "")).strip()
            if not val:
                errors.append(f"forward[{idx}].{key}: 必須項目です。")
            fw_parsed[key] = val
        for key in FORWARD_INT_KEYS:
            raw = str(fw.get(key, "")).strip()
            try:
                num = int(raw)
                if num < 0:
                    raise ValueError()
                fw_parsed[key] = num
            except Exception:
                default_val = defaults.get(key, "")
                errors.append(f"forward[{idx}].{key}: 0以上の整数を入力してください。（例: {default_val}）")
        forwards_parsed.append(fw_parsed)

    for key in GLOBAL_INT_KEYS:
        raw = str(data.get(key, "")).strip()
        try:
            num = int(raw)
            if num < 0:
                raise ValueError()
            parsed[key] = num
        except Exception:
            default_val = defaults.get(key, "")
            errors.append(f"{key}: 0以上の整数を入力してください。（デフォルト: {default_val}）")

    parsed["forwards"] = forwards_parsed
    parsed["sso_start_url"] = str(data.get("sso_start_url", "")).strip()
    if not parsed["sso_start_url"]:
        errors.append("sso_start_url: 必須項目です。")

    return len(errors) == 0, errors, parsed


# ===== エントリポイント =====

def run_wx_app(config: Optional[Config] = None):
    """
    エントリポイント。
    config が指定されていなければ内部で load_config する。
    """
    config = config or load_config()
    app = WxTrayApp()
    app.init_tray(callbacks={}, config=config)
    app.MainLoop()


# ===== 共通ヘルパー =====


def _load_example_config() -> Dict[str, Any]:
    with open(CONFIG_EXAMPLE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _config_to_dict(cfg: Optional[Config]) -> Dict[str, Any]:
    if cfg is None:
        return {}
    return {
        "forwards": [
            {
                "name": f.name,
                "instance_id": f.instance_id,
                "profile": f.profile,
                "region": f.region,
                "remote_host": f.remote_host,
                "remote_port": f.remote_port,
                "local_port": f.local_port,
            }
            for f in cfg.forwards
        ],
        "sso_start_url": cfg.sso_start_url,
        "sso_login_timeout_seconds": cfg.sso_login_timeout_seconds,
        "sso_auto_refresh_margin_seconds": cfg.sso_auto_refresh_margin_seconds,
        "tunnel_check_interval_seconds": cfg.tunnel_check_interval_seconds,
    }


def _save_config(data: Dict[str, Any]):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ===== ミニダイアログ =====


class MiniPrompt:
    """
    tkinter のミニダイアログをサブプロセスで起動するラッパー。
    """

    def __init__(
        self,
        message: str,
        on_click: Callable[[], None],
        button_label: Optional[str] = "開く",
        user_code: Optional[str] = None,
    ):
        self.message = message
        self.on_click = on_click
        self.button_label = button_label
        self.user_code = user_code
        self._proc: Optional[subprocess.Popen] = None
        self._signal_path: Optional[str] = None
        self._stop = threading.Event()

    def show_bottom_right(self):
        if self._proc and self._proc.poll() is None:
            return
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        self._signal_path = tmp.name
        cmd = [
            sys.executable,
            "-m",
            "aws_conn_tray.tk_mini_prompt",
            "--message",
            self.message,
            "--signal-file",
            self._signal_path,
        ]
        if self.button_label:
            cmd += ["--button-label", self.button_label]
        else:
            cmd += ["--no-button"]
        if self.user_code:
            cmd += ["--user-code", self.user_code]
        try:
            popen_kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
            if platforms.is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW"):
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            self._proc = subprocess.Popen(cmd, **popen_kwargs)
        except Exception as e:
            log_message(f"[tk-mini] failed to start: {e}")
            return

        def _watch():
            while not self._stop.is_set():
                if self._signal_path and os.path.exists(self._signal_path):
                    try:
                        with open(self._signal_path, "r", encoding="utf-8") as f:
                            data = f.read().strip()
                        if data == "open":
                            try:
                                if self.on_click:
                                    self.on_click()
                            except Exception as e:
                                log_message(f"[tk-mini] click handler error: {e}")
                            break
                    except Exception:
                        pass
                if self._proc and self._proc.poll() is not None:
                    break
                time.sleep(0.2)
            self._cleanup_signal()

        threading.Thread(target=_watch, daemon=True).start()

    def Destroy(self):
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._cleanup_signal()

    def _cleanup_signal(self):
        if self._signal_path:
            try:
                os.unlink(self._signal_path)
            except Exception:
                pass
            self._signal_path = None


def _wrap_text_for_prompt(text: str, width: int = 20) -> str:
    """
    日本語全角20文字相当で改行する。空白区切りの単語は途中で切らない。
    空白の無い長文は幅で強制的に切る。
    """
    lines: list[str] = []
    for raw_line in text.splitlines():
        tokens = raw_line.split()
        if not tokens:
            lines.append("")
            continue
        current = ""
        for tok in tokens:
            # 単語が幅を超える場合は強制分割
            while len(tok) > width:
                lines.append(tok[:width])
                tok = tok[width:]
            if len(current) + (1 if current else 0) + len(tok) <= width:
                current = f"{current} {tok}".strip()
            else:
                if current:
                    lines.append(current)
                current = tok
        if current:
            lines.append(current)
    # 念のため幅を超える行を強制カット
    final_lines: list[str] = []
    for ln in lines:
        if len(ln) <= width:
            final_lines.append(ln)
        else:
            for i in range(0, len(ln), width):
                final_lines.append(ln[i : i + width])
    return "\n".join(final_lines)


# ===== SSO 認可URL取得（ブラウザ開き専用の軽量版） =====


def _parse_authorize_url(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    aws sso login の標準出力/エラーから URL とデバイスコードを抽出する。
    - AWS のメッセージが地域や言語で変わっても拾えるよう、最初に登場する http/https を採用する。
    - 従来は "authorize" を含む URL のみを採用していたが、実環境では device.sso.xxx… など
      authorize を含まない URL が多いため、最初の URL を素直に採用することで取りこぼしを防ぐ。
    """
    url = None
    code = None
    non_authorize_url = None
    urls = [m.strip().rstrip(".") for m in re.findall(r"https?://\S+", text)]
    if urls:
        # 先頭の URL を採用。authorize が含まれている場合はその方がより確実なので優先。
        for u in urls:
            if "authorize" in u.lower():
                url = u
                break
        if url is None:
            url = urls[0]
    m = re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}", text)
    if m:
        code = m.group(0)
    return url, code


def _extract_user_code(url: Optional[str], code: Optional[str]) -> Optional[str]:
    """
    device URL または出力テキストから user_code を抽出する。
    """
    if code:
        return code
    if not url:
        return None
    try:
        parsed = urlparse(url)
        # user_code は query もしくは fragment に入る
        for blob in (parsed.query or "", parsed.fragment or ""):
            if not blob:
                continue
            if "?" in blob:
                blob = blob.split("?", 1)[1]
            qs = parse_qs(blob)
            val = (qs.get("user_code") or [None])[0]
            if val:
                return val
    except Exception:
        return None
    return None


def _clear_sso_login_proc(proc: Optional[subprocess.Popen] = None) -> None:
    global _sso_login_proc, _sso_login_proc_profile, _sso_login_proc_region
    with _sso_login_proc_lock:
        if proc is None or proc is _sso_login_proc:
            _sso_login_proc = None
            _sso_login_proc_profile = None
            _sso_login_proc_region = None


def _terminate_sso_login_proc(proc: subprocess.Popen, reason: str) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception:
        pass
    try:
        log_message(f"[wx-auth-url] terminate aws sso login ({reason})")
        proc.terminate()
        if hasattr(proc, "wait"):
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
    except Exception:
        pass
    try:
        if proc.poll() is None:
            log_message(f"[wx-auth-url] kill aws sso login ({reason})")
            proc.kill()
            if hasattr(proc, "wait"):
                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass
    except Exception:
        pass


def _set_sso_login_proc(proc: subprocess.Popen, profile: str, region: str) -> None:
    global _sso_login_proc, _sso_login_proc_profile, _sso_login_proc_region
    prev = None
    with _sso_login_proc_lock:
        prev = _sso_login_proc
    if prev and prev is not proc:
        _terminate_sso_login_proc(prev, "superseded")
    with _sso_login_proc_lock:
        _sso_login_proc = proc
        _sso_login_proc_profile = profile
        _sso_login_proc_region = region

    def _wait():
        try:
            if hasattr(proc, "wait"):
                proc.wait()
            else:
                # DummyProc 対応: poll を軽く回す
                for _ in range(20):
                    if proc.poll() is not None:
                        break
                    time.sleep(0.2)
        except Exception:
            pass
        _clear_sso_login_proc(proc)

    threading.Thread(target=_wait, daemon=True).start()


def _schedule_sso_login_cancel(reason: str, grace_seconds: float = SSO_LOGIN_CANCEL_GRACE_SECONDS) -> None:
    with _sso_login_proc_lock:
        proc = _sso_login_proc
        profile = _sso_login_proc_profile
        region = _sso_login_proc_region

    if proc is None:
        return
    try:
        if proc.poll() is not None:
            _clear_sso_login_proc(proc)
            return
    except Exception:
        pass

    def _worker():
        time.sleep(max(0.0, grace_seconds))
        try:
            if proc.poll() is not None:
                _clear_sso_login_proc(proc)
                return
        except Exception:
            pass
        try:
            if profile and region and is_sso_session_valid(profile, region):
                log_message("[wx-auth-url] session valid; skip cancel")
                return
        except Exception:
            pass
        _terminate_sso_login_proc(proc, reason)
        _clear_sso_login_proc(proc)

    threading.Thread(target=_worker, daemon=True).start()


def _fetch_authorize_url(profile: str, region: str, sso_start_url: str, timeout: int) -> Tuple[Optional[str], Optional[str]]:
    """
    aws sso login --no-browser を実行し、認可URLとコードを抽出する（GUI非依存）。
    """
    cmd = ["aws", "sso", "login", "--no-browser", "--profile", profile]
    if region:
        cmd += ["--region", region]
    log_message(f"[wx-auth-url] exec: {' '.join(cmd)} timeout={timeout}")
    max_wait = max(10, min(timeout, 120))
    deadline = time.time() + max_wait
    url = None
    code = None
    non_authorize_url = None
    stdout_acc = ""
    stderr_acc = ""

    # PTY経由で擬似TTYを見せると URL が出やすい (posix 限定)
    use_pty = PTY_AVAILABLE and os.name == "posix"
    master_fd = None
    slave_fd = None
    proc: Optional[subprocess.Popen] = None
    try:
        if use_pty:
            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, text=True)
            read_fds = [master_fd]
        else:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            read_fds = []
        _set_sso_login_proc(proc, profile, region)
    except Exception as e:
        log_message(f"[wx-auth-url] failed to start aws sso login: {e}")
        if master_fd:
            os.close(master_fd)
        if slave_fd:
            os.close(slave_fd)
        _clear_sso_login_proc()
        return None, None

    try:
        try:
            if use_pty:
                while time.time() < deadline:
                    if proc.poll() is not None:
                        break
                    ready, _, _ = select.select(read_fds, [], [], 1)
                    for stream in ready:
                        try:
                            line_bytes = os.read(stream, 4096)
                        except Exception:
                            line_bytes = b""
                        if not line_bytes:
                            continue
                        line = line_bytes.decode(errors="ignore")
                        stdout_acc += line
                        # 一部が分割されて届く可能性があるため、累積バッファ全体を毎回パースする
                        u, c = _parse_authorize_url(stdout_acc)
                        # 改行がまだ来ていない場合は URL が途中の可能性があるため、確定は遅らせる
                        has_line_break = "\n" in stdout_acc or "\r" in stdout_acc
                        if u and (has_line_break or (proc and proc.poll() is not None)):
                            if c and not code:
                                code = c
                            if "authorize" in u.lower():
                                url, code = u, c or code
                                log_message(f"[wx-auth-url] authorize url fetched (stream): {u}")
                                break
                            if non_authorize_url is None:
                                non_authorize_url = u
                                log_message(f"[wx-auth-url] non-authorize url seen (stream): {u}")
                    if url:
                        break
            else:
                # Windows など PTY/Select が使えない環境では、別スレッドで stdout/stderr を逐次読む
                line_q: queue.Queue[Tuple[str, str]] = queue.Queue()

                def _reader(name: str, stream):
                    try:
                        for line in iter(stream.readline, ""):
                            line_q.put((name, line))
                    except Exception:
                        pass
                    finally:
                        try:
                            stream.close()
                        except Exception:
                            pass

                if proc.stdout:
                    threading.Thread(target=_reader, args=("stdout", proc.stdout), daemon=True).start()
                if proc.stderr:
                    threading.Thread(target=_reader, args=("stderr", proc.stderr), daemon=True).start()

                while time.time() < deadline:
                    try:
                        src, line = line_q.get(timeout=0.5)
                        if src == "stdout":
                            stdout_acc += line
                        else:
                            stderr_acc += line
                        u, c = _parse_authorize_url(stdout_acc + "\n" + stderr_acc)
                        if u:
                            if c and not code:
                                code = c
                            if "authorize" in u.lower():
                                url, code = u, c or code
                                log_message(f"[wx-auth-url] authorize url fetched (pipe): {u}")
                                break
                            if non_authorize_url is None:
                                non_authorize_url = u
                                log_message(f"[wx-auth-url] non-authorize url seen (pipe): {u}")
                    except queue.Empty:
                        pass
                    if proc.poll() is not None and line_q.empty():
                        break

                # 残りを軽く吸い上げる
                while not line_q.empty():
                    try:
                        src, line = line_q.get_nowait()
                    except queue.Empty:
                        break
                    if src == "stdout":
                        stdout_acc += line
                    else:
                        stderr_acc += line
                if not url:
                    u, c = _parse_authorize_url(stdout_acc + "\n" + stderr_acc)
                    if c and not code:
                        code = c
                    if u and "authorize" in u.lower():
                        url, code = u, c or code
                        log_message(f"[wx-auth-url] authorize url fetched (pipe): {u}")
                    elif u and non_authorize_url is None:
                        non_authorize_url = u
        except Exception as e:
            log_message(f"[wx-auth-url] error while reading: {e}")
            try:
                proc.kill()
            except Exception:
                pass

        rc = proc.poll()
        log_message(f"[wx-auth-url] rc={rc} stdout_len={len(stdout_acc)} stderr_len={len(stderr_acc)}")

        if not url:
            # 最終的に見つからなければ結合して再度パース（authorize 優先）
            u2, c2 = _parse_authorize_url(stdout_acc + "\n" + stderr_acc)
            if c2 and not code:
                code = c2
            if u2 and "authorize" in u2.lower():
                url, code = u2, c2 or code
            elif u2 and non_authorize_url is None:
                non_authorize_url = u2

        if not url and non_authorize_url:
            url = non_authorize_url
            log_message(f"[wx-auth-url] authorize url not found; using device url fallback: {url}")

        if not url:
            log_message(f"[wx-auth-url] authorize url not found. stdout={stdout_acc!r} stderr={stderr_acc!r}")
            return None, code

    finally:
        # URL を取得できた場合は aws sso login プロセスを生かしておく。
        # （ここで kill/terminate すると認可完了前に落ちてトークンが保存されない）
        if proc and not url:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        pass
            except Exception:
                try:
                    proc.kill()
                    try:
                        proc.wait(timeout=1)
                    except Exception:
                        pass
                except Exception:
                    pass
            _clear_sso_login_proc(proc)
        if use_pty:
            for fd in (master_fd, slave_fd):
                if fd is not None:
                    with contextlib.suppress(Exception):
                        os.close(fd)
    # URL 取得に成功した場合はここまで来るので返す
    return url, code


def _is_request_approved(title: str, url: str, body: str) -> bool:
    url = (url or "").lower()
    # デバイスフロー承認後のローカルコールバックを検出
    if "127.0.0.1" in url and "oauth/callback" in url and "code=" in url:
        return True
    return False
