import os
import sys


def get_app_root() -> str:
    """
    実行パスからアプリのルートディレクトリを返す。
    - PyInstaller で固めた場合: 実行ファイルのディレクトリ
    - 通常実行時: パッケージディレクトリの親（リポジトリルート想定）
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))


APP_ROOT = get_app_root()
CONFIG_DIR = os.path.join(APP_ROOT, "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
CONFIG_EXAMPLE_PATH = os.path.join(CONFIG_DIR, "config.example.json")
LOG_DIR = os.path.join(os.path.expanduser("~"), ".cache", "aws-rds-tray")
LOG_PATH = os.path.join(LOG_DIR, "tray.log")
SSO_CACHE_PATTERN = os.path.expanduser("~/.aws/sso/cache/*.json")
ICON_DIR = os.path.join(APP_ROOT, "icons")
