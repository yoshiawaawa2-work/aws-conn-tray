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
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView

    QT_AVAILABLE = True
    QT_IMPORT_ERROR = ""
except Exception as e:
    QT_AVAILABLE = False
    QT_IMPORT_ERROR = str(e)

DEFAULT_STORAGE = os.path.expanduser("~/.cache/aws-rds-tray/qt-webview")


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

    app = QApplication(sys.argv)
    profile = QWebEngineProfile("aws-rds-tray")
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

    def handle_url(qurl: QUrl):
        if _is_callback_url(qurl.toString()):
            view.close()
            app.quit()

    # URL変化でコールバック検知
    view.urlChanged.connect(handle_url)
    view.show()
    app.exec()
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("authorize_url")
    parser.add_argument("--storage-path", default=None)
    args = parser.parse_args()
    sys.exit(run_webview(args.authorize_url, args.storage_path))


if __name__ == "__main__":
    main()
