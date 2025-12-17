"""
シンプルなアンインストーラ CLI。
 - --preview で削除対象と実行予定コマンドのみ表示（実行しない）
 - install_dir/shortcuts_dir は省略時にデフォルトパスを利用
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from aws_conn_tray import installer_core


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="aws-conn-tray アンインストーラ")
    p.add_argument("--install-dir", help="インストール先のルートパス（省略時はデフォルト）")
    p.add_argument("--shortcuts-dir", help="自動起動/ショートカット用ディレクトリ（省略時はデフォルト）")
    p.add_argument("--preview", action="store_true", help="削除せず対象のみ表示")
    return p.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    meta = installer_core.load_install_metadata()
    defaults = installer_core.default_paths()
    install_dir = (
        Path(args.install_dir).expanduser()
        if args.install_dir
        else Path(meta["install_dir"]).expanduser() if meta and meta.get("install_dir") else defaults["install"]
    )
    shortcuts_dir = (
        Path(args.shortcuts_dir).expanduser()
        if args.shortcuts_dir
        else Path(meta["shortcuts_dir"]).expanduser()
        if meta and meta.get("shortcuts_dir")
        else defaults.get("autostart_dir")
    )

    opts = installer_core.UninstallOptions(install_dir=install_dir, shortcuts_dir=shortcuts_dir, preview=args.preview)
    res = installer_core.perform_uninstall(opts)

    if res.ok:
        if args.preview:
            print("プレビュー: 以下のパスを削除予定です。")
        else:
            print("アンインストール完了。削除したパス:")
        for tgt in res.targets:
            print(f"- {tgt}")
        if res.executed_commands:
            print("自動起動解除で実行した/予定のコマンド:")
            for cmd in res.executed_commands:
                print("  " + " ".join(cmd))
        return 0

    print("アンインストールに失敗しました:")
    for issue in res.issues:
        print(f"- {issue}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
