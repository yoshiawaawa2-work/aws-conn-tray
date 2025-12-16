"""
PySimpleGUI ベースの簡易インストーラ UI。
- --preview / --dry-run でファイルコピーや自動起動登録を行わずに画面確認のみ可能。
- --headless で GUI を出さずに指定オプションでインストールを試行。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import PySimpleGUI as sg

from aws_conn_tray import installer_core


def build_layout(default_install: Path) -> list:
    return [
        [sg.Text("インストール先"), sg.Input(str(default_install), key="install_dir", size=(50, 1)), sg.FolderBrowse("参照")],
        [sg.Checkbox("ログイン時に自動起動を登録する", key="autostart", default=True)],
        [sg.Frame("設定ファイル", [
            [sg.Radio("サンプルから生成", "cfgsrc", key="cfg_sample", default=True)],
            [sg.Radio("既存 config.json を指定", "cfgsrc", key="cfg_import", default=False), sg.Input(key="cfg_path", size=(45, 1)), sg.FileBrowse("参照")],
        ])],
        [sg.Checkbox("前提が足りない場合に推奨コマンドを自動実行（自己責任）", key="auto_install_missing", default=False)],
        [sg.HorizontalSeparator()],
        [sg.Button("インストール", bind_return_key=True), sg.Button("プレビュー", key="preview"), sg.Button("キャンセル")],
        [sg.Multiline("", key="log", size=(70, 10), disabled=True)],
    ]


def _append_log(window: sg.Window, message: str):
    try:
        prev = window["log"].get()
        window["log"].update(prev + message + "\n")
    except Exception:
        pass


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--preview", action="store_true", help="処理を実行せず画面だけ確認")
    p.add_argument("--headless", action="store_true", help="GUI を出さずに実行")
    p.add_argument("--install-dir", default=None)
    p.add_argument("--config", default=None, help="既存 config.json のパス")
    p.add_argument("--no-autostart", action="store_true")
    p.add_argument("--auto-install-missing", action="store_true")
    return p.parse_args()


def perform(opts, preview: bool) -> installer_core.InstallResult:
    cfg_path: Optional[Path] = Path(opts.config) if opts.config else None
    install_dir = Path(opts.install_dir) if opts.install_dir else installer_core.default_paths()["install"]
    install_opts = installer_core.InstallOptions(
        install_dir=install_dir,
        config_source=cfg_path,
        use_example_config=cfg_path is None,
        autostart=not opts.no_autostart,
        auto_install_missing=opts.auto_install_missing,
        preview=preview,
    )
    return installer_core.perform_install(install_opts)


def run_headless(args):
    res = perform(args, preview=args.preview)
    if res.ok:
        print("インストール完了（プレビュー）" if args.preview else "インストール完了")
    else:
        print("インストール失敗:")
        for issue in res.issues:
            print(" -", issue)
    return 0 if res.ok else 1


def run_gui(args):
    defaults = installer_core.default_paths()
    layout = build_layout(defaults["install"])
    window = sg.Window("aws-conn-tray インストーラ", layout)

    while True:
        event, values = window.read()
        if event in (sg.WINDOW_CLOSED, "キャンセル"):
            break
        preview = event == "プレビュー" or args.preview
        if event in ("インストール", "プレビュー"):
            install_dir = values.get("install_dir") or str(defaults["install"])
            cfg_import = values.get("cfg_import")
            cfg_path = Path(values.get("cfg_path")) if cfg_import and values.get("cfg_path") else None
            opts = argparse.Namespace(
                install_dir=install_dir,
                config=str(cfg_path) if cfg_path else None,
                no_autostart=not values.get("autostart", True),
                auto_install_missing=values.get("auto_install_missing", False),
            )
            res = perform(opts, preview=preview)
            if res.ok:
                msg = "プレビュー完了（実際のコピーや自動起動登録は行っていません）" if preview else "インストールが完了しました。"
                sg.popup_ok(msg, keep_on_top=True)
                _append_log(window, msg)
            else:
                sg.popup_error("前提条件エラー:\n" + "\n".join(res.issues), keep_on_top=True)
                _append_log(window, "\n".join(res.issues))

    window.close()


def main():
    args = _parse_args()
    if args.headless:
        sys.exit(run_headless(args))
    run_gui(args)


if __name__ == "__main__":
    main()
