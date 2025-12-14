import datetime
import subprocess
from typing import Callable, Optional

from .paths import LOG_DIR, LOG_PATH

# wx が使える場合は wx 側の通知を優先（GLib ループ不要でクロスプラットフォーム）
try:
    import wx
    import wx.adv

    WX_AVAILABLE = True
except Exception:
    WX_AVAILABLE = False


def log_message(message: str) -> None:
    try:
        import os

        os.makedirs(LOG_DIR, exist_ok=True)
        timestamp = datetime.datetime.now().isoformat()
        lines = str(message).splitlines() or [""]
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(f"{timestamp} {line}\n")
    except Exception:
        pass


def notify(title: str, message: str, on_click: Optional[Callable[[], None]] = None) -> None:
    try:
        # wx が利用でき、かつ wx.App が存在するなら wx の NotificationMessage を優先
        if WX_AVAILABLE and wx.GetApp() is not None:
            def _show():
                parent = wx.GetApp().GetTopWindow()
                msg = wx.adv.NotificationMessage(title, message, parent=parent)
                if on_click:
                    def handler(_evt):
                        try:
                            on_click()
                        except Exception:
                            pass
                        try:
                            msg.Unbind(wx.adv.EVT_NOTIFICATION_MESSAGE_CLICKED, handler)
                        except Exception:
                            pass
                    msg.Bind(wx.adv.EVT_NOTIFICATION_MESSAGE_CLICKED, handler)
                msg.Show()

            try:
                if hasattr(wx, "IsMainThread") and not wx.IsMainThread():
                    wx.CallAfter(_show)
                else:
                    _show()
                return
            except Exception:
                pass

        # フォールバックで notify-send（GUIが無い場合は無視）
        try:
            subprocess.run(
                ["notify-send", title, message, "--expire-time=8000"],
                check=False,
            )
        except FileNotFoundError:
            pass
    except FileNotFoundError:
        pass
    except Exception:
        pass
    finally:
        log_message(f"{title}: {message}")
