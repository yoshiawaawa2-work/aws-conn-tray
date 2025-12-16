"""
PySide6 + QtWebEngine を使った認可URL表示ヘルパー。
wx 側とは別プロセスで起動され、URL が /oauth/callback?code=... に到達したら終了する。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

try:
    from PySide6.QtCore import QUrl, Qt
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView

    QT_AVAILABLE = True
    QT_IMPORT_ERROR = ""
except Exception as e:
    QT_AVAILABLE = False
    QT_IMPORT_ERROR = str(e)

DEFAULT_STORAGE = os.path.expanduser("~/.cache/aws_conn_tray/qt-webview")


def _is_callback_url(url_str: str) -> bool:
    url_str = (url_str or "").lower()
    return (
        "127.0.0.1" in url_str
        and "/oauth/callback" in url_str
        and "code=" in url_str
    )


def run_webview(authorize_url: str, storage_path: Optional[str] = None) -> int:
    if not QT_AVAILABLE:
        sys.stderr.write(f"PySide6 / QtWebEngine が利用できません: {QT_IMPORT_ERROR}\n")
        return 1

    # GPU/VA-API/GL 周りでクラッシュする環境があるため、強めにソフトウェア描画を強制
    # - xcb を強制 (Wayland/VirtualBox でのクラッシュ回避)
    # - single-process で共有メモリを避ける
    # - GPU/GL/Sandbox をオフにする
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
        "--disable-gpu "
        "--disable-features=VaapiVideoDecoder "
        "--disable-dev-shm-usage"
    )
    os.environ["LIBVA_DRIVER_NAME"] = "mesa"
    os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
    os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    os.environ["QT_QUICK_BACKEND"] = "software"
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    os.environ["QT_OPENGL"] = "software"
    sys.stderr.write(
        "[qwebview] env "
        f"QTWEBENGINE_CHROMIUM_FLAGS={os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS')} "
        f"LIBVA_DRIVER_NAME={os.environ.get('LIBVA_DRIVER_NAME')} "
        f"LIBGL_ALWAYS_SOFTWARE={os.environ.get('LIBGL_ALWAYS_SOFTWARE')} "
        f"QTWEBENGINE_DISABLE_SANDBOX={os.environ.get('QTWEBENGINE_DISABLE_SANDBOX')} "
        f"QT_QUICK_BACKEND={os.environ.get('QT_QUICK_BACKEND')} "
        f"QT_QPA_PLATFORM={os.environ.get('QT_QPA_PLATFORM')} "
        f"QT_OPENGL={os.environ.get('QT_OPENGL')}\n"
    )

    sys.stderr.write(f"[qwebview] start url={authorize_url} storage={storage_path or DEFAULT_STORAGE}\n")
    app = QApplication(sys.argv)
    profile = QWebEngineProfile("aws-conn-tray")
    storage_dir = storage_path or DEFAULT_STORAGE
    try:
        os.makedirs(storage_dir, exist_ok=True)
    except Exception:
        pass
    profile.setPersistentStoragePath(storage_dir)
    profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)

    page = QWebEnginePage(profile)
    view = QWebEngineView()
    view.setPage(page)
    view.resize(900, 700)
    view.load(QUrl(authorize_url))
    try:
        # フォーカスを奪わず、最小化状態でタスクバーに格納する
        view.setAttribute(Qt.WA_ShowWithoutActivating, True)
    except Exception:
        pass

    def handle_url(qurl: QUrl):
        url_str = qurl.toString()
        sys.stderr.write(f"[qwebview] urlChanged {url_str}\n")
        if _is_callback_url(url_str):
            sys.stderr.write("[qwebview] callback detected -> quit\n")
            try:
                from aws_conn_tray.logger import notify, log_message
                notify(
                    "SSO ログイン完了",
                    "認証コードを検出したため WebView を閉じます。",
                )
                log_message(f"SSO callback detected, closing WebView: {url_str}")
            except Exception:
                pass
            view.close()
            app.quit()

    # URL変化でコールバック検知
    view.urlChanged.connect(handle_url)
    try:
        view.showMinimized()
    except Exception:
        view.show()
    rc = app.exec()
    sys.stderr.write(f"[qwebview] app.exec() finished rc={rc}\n")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("authorize_url")
    parser.add_argument("--storage-path", default=None)
    args = parser.parse_args()
    sys.exit(run_webview(args.authorize_url, args.storage_path))


if __name__ == "__main__":
    main()
