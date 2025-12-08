import sys
import types


def pytest_configure():
    """
    gi が無い環境でもテストが進むようにダミーの gi モジュールを登録する。
    """
    gi = types.ModuleType("gi")
    def require_version(*args, **kwargs):
        return None
    gi.require_version = require_version
    # ダミーの repository
    class DummyNotify:
        Notification = types.SimpleNamespace(
            new=lambda *args, **kwargs: types.SimpleNamespace(
                add_action=lambda *a, **k: None, show=lambda *a, **k: None
            )
        )

    class DummyIndicator:
        Indicator = types.SimpleNamespace(
            new=lambda *a, **k: types.SimpleNamespace(
                set_status=lambda *a, **k: None,
                set_menu=lambda *a, **k: None,
                set_icon_full=lambda *a, **k: None,
            ),
            IndicatorCategory=types.SimpleNamespace(APPLICATION_STATUS=0),
            IndicatorStatus=types.SimpleNamespace(ACTIVE=0),
        )

    class DummyGtk:
        class MenuItem:
            @staticmethod
            def new_with_label(label):
                return types.SimpleNamespace(connect=lambda *a, **k: None, set_sensitive=lambda *a, **k: None)
        class Menu:
            def __init__(self): pass
            def append(self, *a, **k): pass
            def show_all(self): pass
        class SeparatorMenuItem:
            def __init__(self): pass
        class Entry:
            def __init__(self): pass
            def get_text(self): return ""
            def set_text(self, val): pass
            def connect(self, *a, **k): pass

    gi.repository = types.SimpleNamespace(
        Notify=DummyNotify,
        AppIndicator3=DummyIndicator,
        AyatanaAppIndicator3=DummyIndicator,
        Gtk=DummyGtk,
        GObject=types.SimpleNamespace(idle_add=lambda *a, **k: None),
        GLib=types.SimpleNamespace(PRIORITY_DEFAULT=0, idle_add=lambda *a, **k: None),
        Gdk=types.SimpleNamespace(),
    )
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = gi.repository
