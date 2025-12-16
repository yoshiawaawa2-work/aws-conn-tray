from pathlib import Path

from scripts.build_package import main as build_main


def test_build_package_creates_artifacts(tmp_path, monkeypatch):
    # 実際のコピーを避けるため、installer_core のメソッドをモックし、呼び出し確認のみ行う
    calls = {}

    def fake_create_tree(src, build_dir, dist_name="aws-conn-tray"):
        calls["tree"] = (src, build_dir, dist_name)
        dst = build_dir / dist_name
        dst.mkdir(parents=True, exist_ok=True)
        return dst

    def fake_create_zip(src, build_dir, zip_name="aws-conn-tray.zip"):
        calls["zip"] = (src, build_dir, zip_name)
        path = build_dir / zip_name
        path.write_text("dummy", encoding="utf-8")
        return path

    import scripts.build_package as bp
    monkeypatch.setattr(bp.installer_core, "create_build_tree", fake_create_tree)
    monkeypatch.setattr(bp.installer_core, "create_zip", fake_create_zip)
    monkeypatch.setattr(bp, "__file__", str(tmp_path / "build_package.py"))
    monkeypatch.setattr(bp, "Path", Path)
    # pytest の引数を避けるため argv を書き換える
    monkeypatch.setattr(bp, "argparse", bp.argparse)
    monkeypatch.setattr(bp.argparse, "ArgumentParser", bp.argparse.ArgumentParser)
    monkeypatch.setattr(bp, "__name__", "__main__")
    import sys
    monkeypatch.setattr(sys, "argv", ["build_package.py"])

    # 実行
    bp.main()

    assert "tree" in calls and "zip" in calls
