from pathlib import Path


def test_install_deps_script_exists():
    path = Path("scripts/install_deps.sh")
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "requirements.txt" in content
    assert "requirements.txt" in content
