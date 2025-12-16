#!/usr/bin/env python3
"""
build/ 配下に配布用ディレクトリを展開し、ZIP も生成するスクリプト。
実際のアプリ一式をコピーするため、リポジトリ直下で実行してください。
"""
import argparse
from pathlib import Path

from aws_conn_tray import installer_core


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-dir", default="build", help="出力先ディレクトリ")
    parser.add_argument("--dist-name", default="aws-conn-tray", help="展開ディレクトリ名")
    args = parser.parse_args()

    src_root = Path(__file__).resolve().parent.parent
    build_dir = src_root / args.build_dir

    dist_dir = installer_core.create_build_tree(src_root, build_dir, dist_name=args.dist_name)
    zip_path = installer_core.create_zip(dist_dir, build_dir, zip_name=f"{args.dist_name}.zip")
    print(f"build completed: dir={dist_dir} zip={zip_path}")


if __name__ == "__main__":
    main()
