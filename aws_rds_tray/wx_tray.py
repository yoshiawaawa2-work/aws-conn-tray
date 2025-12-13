from typing import Callable, Optional, Dict, Any, Tuple, List
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
from .config_loader import Config, load_config
from .sso import check_sso_token, is_sso_session_valid, start_sso_login_browser
from .ssm import SsmPortForward, _cleanup_lingering_sessions

WEBVIEW_STORAGE_DIR = os.path.expanduser("~/.cache/aws-rds-tray/webview")
QT_WEBVIEW_STORAGE_DIR = os.path.expanduser("~/.cache/aws-rds-tray/qt-webview")
MINI_PROMPT_SIZE = (260, 90)
# Qt WebView サブプロセス管理用
_qt_webview_procs: List[subprocess.Popen] = []

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


class WxTrayIcon(wx.adv.TaskBarIcon):
    """
    トレイアイコンとメニュー。
    """

    def __init__(
        self,
        parent=None,
        on_settings: Optional[Callable[[], None]] = None,
        on_sso_login: Optional[Callable[[], None]] = None,
        on_reconnect: Optional[Callable[[], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.parent = parent
        self.cb_settings = on_settings
        self.cb_sso_login = on_sso_login
        self.cb_reconnect = on_reconnect
        self.cb_quit = on_quit
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
            ("ポートフォワード再接続", "on_reconnect"),
            (None, None),  # separator
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
            self.SetIcon(icon, "aws-rds-tray")
        except Exception as e:
            log_message(f"[wx] icon set failed: {e}")

    def on_settings(self, _evt=None):
        if self.cb_settings:
            self.cb_settings()

    def on_sso_login(self, _evt=None):
        if self.cb_sso_login:
            self.cb_sso_login()

    def on_reconnect(self, _evt=None):
        if self.cb_reconnect:
            self.cb_reconnect()

    def on_quit(self, _evt=None):
        if self.cb_quit:
            self.cb_quit()
        try:
            wx.CallAfter(lambda: wx.GetApp().ExitMainLoop())
        except Exception:
            wx.CallAfter(lambda: wx.GetApp().ExitMainLoop())


class WxTrayApp:
    """
    wxPython ベースのトレイアプリ。
    wx が無い、または DISPLAY 無しの場合は ImportError/RuntimeError を投げる。
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
        self.ssm: Optional[SsmPortForward] = None
        self.timer: Optional[wx.Timer] = None
        self._refreshing = False
        self._notified_invalid = False
        self._last_webview_frame: Optional[wx.Frame] = None
        self._mini_prompt: Optional["MiniPrompt"] = None
        self._webview_running = False
        self._port_notified = False
        self._port_retry_count = 0
        self._shutting_down = False

    def OnInit(self):
        return True

    def init_tray(self, callbacks: dict, config: Optional[Config] = None):
        self.config = config or load_config()
        self.ssm = SsmPortForward(
            instance_id=self.config.instance_id,
            profile=self.config.profile,
            region=self.config.region,
            local_port=self.config.local_port,
            remote_host=self.config.remote_host,
            remote_port=self.config.remote_port,
        )
        # コールバックは dict で受け取る（未設定は None）
        callbacks = callbacks or {}
        callbacks.setdefault("settings", self.on_settings)
        callbacks.setdefault("sso_login", self.on_sso_login)
        callbacks.setdefault("reconnect", self.on_reconnect)
        callbacks.setdefault("quit", self.on_quit)
        self.tray_icon = WxTrayIcon(
            parent=self._frame,
            on_settings=callbacks.get("settings"),
            on_sso_login=callbacks.get("sso_login"),
            on_reconnect=callbacks.get("reconnect"),
            on_quit=callbacks.get("quit"),
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
        if self.ssm:
            try:
                if self.ssm.is_alive():
                    self.ssm.stop()
                else:
                    # 念のため stop でプロセスを明示的に解放
                    self.ssm.stop()
            except Exception as e:
                log_message(f"[ssm] stop during shutdown failed: {e}")
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
        # ensure counters exist even in test-made instances
        if not hasattr(self, "_port_retry_count"):
            self._port_retry_count = 0
        if not hasattr(self, "_port_notified"):
            self._port_notified = False

        token_ok, remaining = check_sso_token(expected_start_url=self.config.sso_start_url)
        if token_ok and not is_sso_session_valid(self.config.profile, self.config.region):
            token_ok = False

        tunnel_ok = self.ssm.is_alive() if self.ssm else False
        log_message(f"[wx-monitor] token_ok={token_ok} remaining={remaining} tunnel_ok={tunnel_ok}")

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
            if not tunnel_ok and self.ssm:
                self.tray_icon.set_icon_color("yellow")
                started = self.ssm.start()
                if not started:
                    self._port_retry_count += 1
                    if not self._port_notified:
                        notify("ポートフォワード", f"ローカルポート {self.config.local_port} が使用中です。")
                        self._port_notified = True
                    if self._port_retry_count >= 3:
                        msg = f"ローカルポート {self.config.local_port} が使用中です。アプリを終了します。"
                        notify("ポートフォワード", msg)
                        log_message("[ssm] port still in use after retries -> shutdown")
                        self.tray_icon.set_icon_color("red")
                        self._shutting_down = True
                        wx.CallAfter(self.shutdown)
                        return
                    self.tray_icon.set_icon_color("red")
                else:
                    self._port_notified = False
                    self._port_retry_count = 0
            elif tunnel_ok:
                self._port_notified = False
                self._port_retry_count = 0
                self.tray_icon.set_icon_color("green")
        else:
            # トークン無効時はミニダイアログで WebView 誘導
            if not self._notified_invalid:
                msg = "SSO トークンが無効です。\n右下の「開く」またはメニューの「SSO ログイン」から再認証してください。"
                self._show_sso_prompt(self.config.sso_start_url, msg)
                log_message("[wx-monitor] token invalid, prompting login")
                self._notified_invalid = True
            self.tray_icon.set_icon_color("red")

    def _start_sso_refresh(self):
        """
        マージン内で自動リフレッシュを試みる。スレッドで実行し、URLが取れたら自動でWebViewを開く。
        """

        def worker():
            try:
                url, code = _fetch_authorize_url(
                    self.config.profile,
                    self.config.region,
                    self.config.sso_start_url,
                    timeout=self.config.sso_login_timeout_seconds,
                )
                if url and "authorize" in url.lower():
                    log_message(f"[wx-refresh] authorize url fetched: {url}")
                    # 自動で WebView を開き、失敗時は MiniPrompt/通知がフォールバック
                    start_sso_login_webview(url)
                else:
                    msg = "認可URLの取得に失敗しました。以下のボタンで再試行してください。"
                    self._show_sso_prompt(self.config.sso_start_url, msg, "再試行")
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
        if self.ssm and not self.ssm.is_alive():
            self.tray_icon.set_icon_color("yellow")
            started = self.ssm.start()
            if not started:
                notify("ポートフォワード", f"ローカルポート {self.config.local_port} が使用中です。")
                self.tray_icon.set_icon_color("red")
                self._port_retry_count += 1
                if self._port_retry_count >= 3:
                    msg = f"ローカルポート {self.config.local_port} が使用中です。アプリを終了します。"
                    notify("ポートフォワード", msg)
                    log_message(f"[ssm] port still in use after retries -> shutdown")
                    wx.CallAfter(self.shutdown)
        else:
            notify("ポートフォワード", "すでにポートフォワードは有効です。")

    def on_quit(self):
        self.shutdown()

    def _show_settings_dialog(self, current: Dict[str, Any], defaults: Dict[str, Any]):
        """
        設定ダイアログ。wx が無い場合や DISPLAY が無い場合は None を返す。
        """
        if wx is None or not _has_display():
            return None
        dlg = wx.Dialog(None, title="設定", size=(400, 400))
        panel = wx.Panel(dlg)
        sizer = wx.BoxSizer(wx.VERTICAL)
        entries = {}

        def add_text(label, key):
            hs = wx.BoxSizer(wx.HORIZONTAL)
            hs.Add(wx.StaticText(panel, label=label), 0, wx.ALL, 5)
            txt = wx.TextCtrl(panel, value=str(current.get(key, "")))
            entries[key] = txt
            hs.Add(txt, 1, wx.ALL | wx.EXPAND, 5)
            sizer.Add(hs, 0, wx.EXPAND)

        def add_int(label, key):
            hs = wx.BoxSizer(wx.HORIZONTAL)
            hs.Add(wx.StaticText(panel, label=label), 0, wx.ALL, 5)
            txt = wx.TextCtrl(panel, value=str(current.get(key, defaults.get(key, 0))))
            entries[key] = txt
            hs.Add(txt, 1, wx.ALL | wx.EXPAND, 5)
            sizer.Add(hs, 0, wx.EXPAND)

        for key in REQUIRED_KEYS:
            add_text(key, key)
        for key in INT_KEYS:
            add_int(key, key)

        btns = wx.StdDialogButtonSizer()
        btn_ok = wx.Button(panel, wx.ID_OK)
        btn_cancel = wx.Button(panel, wx.ID_CANCEL)
        btns.AddButton(btn_ok)
        btns.AddButton(btn_cancel)
        btns.Realize()
        sizer.Add(btns, 0, wx.ALIGN_CENTER | wx.ALL, 5)

        panel.SetSizer(sizer)
        dlg.Fit()
        result = dlg.ShowModal()
        if result != wx.ID_OK:
            dlg.Destroy()
            return None
        data = {k: entries[k].GetValue() for k in entries}
        dlg.Destroy()
        return data

    def _show_sso_prompt(self, auth_url: str, message: str, button_label: str = "開く"):
        # 既存があればいったん閉じてメッセージを更新
        self._close_mini_prompt()

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

        self._mini_prompt = MiniPrompt(message=message, on_click=on_click, button_label=button_label)
        self._mini_prompt.show_bottom_right()

    def _close_mini_prompt(self):
        if self._mini_prompt:
            try:
                self._mini_prompt.Destroy()
            except Exception:
                pass
            self._mini_prompt = None

    def _show_browser_prompt(self, url: str, message: str):
        """
        右下ダイアログでブラウザ起動を促す（自動では開かない）。
        """
        self._close_mini_prompt()

        def on_click():
            try:
                webbrowser.open(url)
            except Exception as e:
                log_message(f"[webview-fallback] browser open failed: {e}")

        self._mini_prompt = MiniPrompt(message=message, on_click=on_click, button_label="開く")
        self._mini_prompt.show_bottom_right()

    def _launch_webview(self, fallback_url: str):
        """
        UI をブロックしないように認可URL取得→WebView起動をスレッドで実行。
        """
        def worker():
            self._webview_running = True
            try:
                url_to_open = fallback_url
                log_message(f"[webview-launch] requested (fallback={fallback_url})")
                if not self.config:
                    log_message("[webview-launch] no config, abort")
                    return
                try:
                    log_message("[webview-launch] fetching authorize url...")
                    url, _ = _fetch_authorize_url(
                        self.config.profile,
                        self.config.region,
                        self.config.sso_start_url,
                        timeout=self.config.sso_login_timeout_seconds,
                    )
                    if url and "authorize" in url.lower():
                        url_to_open = url
                        log_message(f"[webview-launch] authorize url fetched: {url}")
                    else:
                        log_message("[webview-launch] authorize url not found, request retry")
                        msg = "認可URLの取得に失敗しました。以下のボタンを押して再試行してください。"
                        wx.CallAfter(self._show_sso_prompt, fallback_url, msg, "再試行")
                        return
                except Exception as e:
                    log_message(f"[webview-launch] authorize url fetch failed: {e}")
                    msg = "認可URLの取得に失敗しました。以下のボタンを押して再試行してください。"
                    wx.CallAfter(self._show_sso_prompt, fallback_url, msg, "再試行")
                    return

                ok = start_sso_login_webview(url_to_open)
                log_message(f"[webview-launch] start_sso_login_webview result={ok}")
                if ok:
                    # 成功したらミニダイアログは閉じる
                    wx.CallAfter(self._close_mini_prompt)
                else:
                    msg = "WebView を開けませんでした。以下のボタンから再試行してください。"
                    wx.CallAfter(self._show_sso_prompt, fallback_url, msg, "再試行")
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
        "aws_rds_tray.qwebview_runner",
        url,
        "--storage-path",
        storage_dir,
    ]
    env = os.environ.copy()
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        "--disable-gpu --disable-software-rasterizer "
        "--disable-features=VaapiVideoDecoder --disable-dev-shm-usage --no-sandbox "
        "--disable-gpu-compositing --disable-webgl"
    )
    env["LIBVA_DRIVER_NAME"] = "mesa"
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    env["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    env["QT_QUICK_BACKEND"] = "software"
    env["QT_QPA_PLATFORM"] = "xcb"
    env["QT_OPENGL"] = "software"
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
        )
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
                if rc not in (0, None) and on_fail:
                    try:
                        on_fail()
                    except Exception as e:
                        log_message(f"[qt-webview] on_fail error: {e}")
            except Exception as e:
                log_message(f"[qt-webview] wait error: {e}")

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
            return False
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
        return False


def start_sso_login_webview(url: str) -> bool:
    """
    認可URLを Qt WebEngine (PySide6) で開く。失敗時はミニダイアログ経由でブラウザフォールバック。
    """
    if not url:
        return False
    fallback_triggered = {"done": False}

    def _fallback():
        # 右下ダイアログでブラウザ誘導。wx コンテキストが無い場合は notify にフォールバック。
        fallback_triggered["done"] = True
        log_message("[webview-fallback] showing browser prompt")
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
                    "WebView を開けませんでした。右下の「開く」でブラウザを開いてログインしてください。",
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

    if open_qt_webview_authorize(url, storage_dir=QT_WEBVIEW_STORAGE_DIR, on_fail=_fallback):
        try:
            notify("SSO ログイン", "認証画面を最小化で開きました。タスクバーから表示できます。（ウィンドウ名: qwebview_runner.py）")
        except Exception:
            pass
        return True
    _fallback()
    return False


# ===== 設定バリデーション =====

REQUIRED_KEYS = ["instance_id", "profile", "region", "remote_host", "sso_start_url"]
INT_KEYS = [
    "local_port",
    "remote_port",
    "sso_login_timeout_seconds",
    "sso_auto_refresh_margin_seconds",
    "tunnel_check_interval_seconds",
]


def validate_settings_input(data: Dict[str, Any], defaults: Dict[str, Any]):
    errors = []
    parsed: Dict[str, Any] = {}

    for key in REQUIRED_KEYS:
        val = str(data.get(key, "")).strip()
        if not val:
            errors.append(f"{key}: 必須項目です。")
        parsed[key] = val

    for key in INT_KEYS:
        raw = str(data.get(key, "")).strip()
        try:
            num = int(raw)
            if num < 0:
                raise ValueError()
            parsed[key] = num
        except Exception:
            default_val = defaults.get(key, "")
            errors.append(f"{key}: 0以上の整数を入力してください。（デフォルト: {default_val}）")

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
        "instance_id": cfg.instance_id,
        "profile": cfg.profile,
        "region": cfg.region,
        "remote_host": cfg.remote_host,
        "remote_port": cfg.remote_port,
        "local_port": cfg.local_port,
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


class MiniPrompt(wx.Frame):
    """
    右下に出す小さな半透明ダイアログ。
    クリックでコールバックを呼ぶだけでフォーカス奪取を最小化。
    """

    def __init__(self, message: str, on_click: Callable[[], None], button_label: str = "開く"):
        style = wx.STAY_ON_TOP | wx.FRAME_NO_TASKBAR | wx.BORDER_SIMPLE
        super().__init__(None, title="", size=MINI_PROMPT_SIZE, style=style)
        # 念のため常に最前面フラグを維持
        self.SetWindowStyleFlag(self.GetWindowStyle() | wx.STAY_ON_TOP)
        self.on_click = on_click
        # さらに広い最小サイズでテキスト切れを防止
        self.SetSize((520, 200))
        self.SetMinSize((520, 200))
        panel = wx.Panel(self)
        panel.SetBackgroundColour(wx.Colour(245, 245, 245))
        sizer = wx.BoxSizer(wx.VERTICAL)
        label = wx.StaticText(panel, label=message, style=wx.ALIGN_LEFT)
        # 読みやすいフォントに調整
        font = label.GetFont()
        font.SetPointSize(max(12, font.GetPointSize() + 3))
        font.SetWeight(wx.FONTWEIGHT_BOLD)
        label.SetFont(font)
        label.SetForegroundColour(wx.Colour(0, 0, 0))
        # 長い文言でも折り返す（ボーダーと余白を考慮し広めに）
        label.Wrap(460)
        btn = wx.Button(panel, label=button_label)
        btn_font = btn.GetFont()
        btn_font.SetPointSize(max(12, btn_font.GetPointSize() + 3))
        btn_font.SetWeight(wx.FONTWEIGHT_BOLD)
        btn.SetFont(btn_font)
        btn.SetForegroundColour(wx.Colour(0, 0, 0))
        sizer.Add(label, 0, wx.ALL | wx.EXPAND, 12)
        sizer.AddStretchSpacer()
        sizer.Add(btn, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        panel.SetSizer(sizer)

        # 半透明（可能な環境のみ）
        try:
            self.SetTransparent(190)
        except Exception:
            pass

        # クリックでコールバック
        btn.Bind(wx.EVT_BUTTON, self._on_click)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_click)
        panel.Bind(wx.EVT_LEFT_DOWN, self._on_click)
        label.Bind(wx.EVT_LEFT_DOWN, self._on_click)

    def show_bottom_right(self):
        try:
            scr_w, scr_h = wx.GetDisplaySize()
            w, h = self.GetSize()
            x = max(0, scr_w - w - 20)
            y = max(0, scr_h - h - 40)
            self.SetPosition((x, y))
            # フォーカスを奪わずに表示
            if hasattr(self, "ShowWithoutActivating"):
                self.ShowWithoutActivating()
            else:
                self.Show()
            self.Raise()
        except Exception:
            self.Show()

    def _on_click(self, _evt=None):
        try:
            if self.on_click:
                self.on_click()
        except Exception as e:
            log_message(f"[wx-mini] click handler error: {e}")
        finally:
            try:
                self.Destroy()
            except Exception:
                pass


# ===== SSO 認可URL取得（ブラウザ開き専用の軽量版） =====


def _parse_authorize_url(text: str) -> Tuple[Optional[str], Optional[str]]:
    url = None
    code = None
    urls = [m.strip().rstrip(".") for m in re.findall(r"https?://\S+", text)]
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
    stdout_acc = ""
    stderr_acc = ""

    # PTY経由で擬似TTYを見せると URL が出やすい (posix 限定)
    use_pty = PTY_AVAILABLE and os.name == "posix"
    master_fd = None
    slave_fd = None
    try:
        if use_pty:
            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(cmd, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, text=True)
            read_fds = [master_fd]
        else:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            read_fds = []
    except Exception as e:
        log_message(f"[wx-auth-url] failed to start aws sso login: {e}")
        if master_fd:
            os.close(master_fd)
        if slave_fd:
            os.close(slave_fd)
        return None, None

    try:
        if use_pty:
            while time.time() < deadline:
                if proc.poll() is not None:
                    break
                ready, _, _ = select.select(read_fds, [], [], 1)
                for stream in ready:
                    line_bytes = os.read(stream, 4096)
                    if not line_bytes:
                        continue
                    line = line_bytes.decode(errors="ignore")
                    stdout_acc += line
                    u, c = _parse_authorize_url(line)
                    if u and "authorize" in u.lower():
                        url, code = u, c
                        log_message(f"[wx-auth-url] authorize url fetched (stream): {u}")
                        return url, code
        else:
            # Windows など select/pty が使えない環境ではまとめて読み取る
            try:
                out, err = proc.communicate(timeout=max_wait)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                out, err = proc.communicate()
            stdout_acc += out or ""
            stderr_acc += err or ""
            u, c = _parse_authorize_url(stdout_acc)
            if u and "authorize" in (u or "").lower():
                url, code = u, c
                log_message(f"[wx-auth-url] authorize url fetched (pipe): {u}")
                return url, code
    except Exception as e:
        log_message(f"[wx-auth-url] error while reading: {e}")
        try:
            proc.kill()
        except Exception:
            pass

    rc = proc.poll()
    log_message(f"[wx-auth-url] rc={rc} stdout_len={len(stdout_acc)} stderr_len={len(stderr_acc)}")

    if not url:
        # 最終的に見つからなければ結合して再度パース
        u2, c2 = _parse_authorize_url(stdout_acc + "\n" + stderr_acc)
        if u2 and "authorize" in (u2 or "").lower():
            url, code = u2, c2

    if not url:
        log_message(f"[wx-auth-url] authorize url not found. stdout={stdout_acc!r} stderr={stderr_acc!r}")
        return None, code

    # 後片付け: PTYを開いていたら閉じる
    if use_pty:
        try:
            os.close(master_fd)
        except Exception:
            pass
        try:
            os.close(slave_fd)
        except Exception:
            pass
    return url, code


def _is_request_approved(title: str, url: str, body: str) -> bool:
    url = (url or "").lower()
    # デバイスフロー承認後のローカルコールバックを検出
    if "127.0.0.1" in url and "oauth/callback" in url and "code=" in url:
        return True
    return False
