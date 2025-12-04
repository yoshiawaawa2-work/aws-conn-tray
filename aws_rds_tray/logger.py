import datetime
import subprocess
from typing import Callable, Optional

import gi

from .paths import LOG_DIR, LOG_PATH

gi.require_version("Notify", "0.7")
from gi.repository import Notify  # noqa: E402

try:
    Notify.init("aws-rds-tray")
    NOTIFY_AVAILABLE = True
except Exception:
    NOTIFY_AVAILABLE = False


def log_message(message: str) -> None:
    try:
        import os

        os.makedirs(LOG_DIR, exist_ok=True)
        timestamp = datetime.datetime.now().isoformat()
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{timestamp} {message}\n")
    except Exception:
        pass


def notify(title: str, message: str, on_click: Optional[Callable[[], None]] = None) -> None:
    try:
        if NOTIFY_AVAILABLE:
            n = Notify.Notification.new(title, message, None)
            if on_click:
                n.add_action("default", "open", lambda _n, _a: on_click())
            n.show()
        else:
            raise RuntimeError("notify-unavailable")
    except Exception:
        # フォールバックで notify-send
        try:
            subprocess.run(
                ["notify-send", title, message, "--expire-time=8000"],
                check=False,
            )
        except FileNotFoundError:
            pass
    finally:
        log_message(f"{title}: {message}")
