import os
import sys


def get_app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_ROOT = get_app_root()
CONFIG_DIR = os.path.join(APP_ROOT, "config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
LOG_DIR = os.path.join(os.path.expanduser("~"), ".cache", "aws-rds-tray")
LOG_PATH = os.path.join(LOG_DIR, "tray.log")
SSO_CACHE_PATTERN = os.path.expanduser("~/.aws/sso/cache/*.json")
ICON_DIR = os.path.join(APP_ROOT, "icons")
