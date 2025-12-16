import argparse
from types import SimpleNamespace

import installer_ui


def test_run_headless_preview(monkeypatch, tmp_path):
    monkeypatch.setattr(installer_ui, "perform", lambda opts, preview: SimpleNamespace(ok=True, issues=[]))
    args = argparse.Namespace(
        preview=True,
        headless=True,
        install_dir=str(tmp_path),
        config=None,
        no_autostart=False,
        auto_install_missing=False,
    )
    rc = installer_ui.run_headless(args)
    assert rc == 0
