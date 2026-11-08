from pathlib import Path
import xml.etree.ElementTree as ET


def test_windows_task_template_has_placeholders():
    path = Path("doc/autostart/windows/task_aws_conn_tray.xml")
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    ET.parse(path)
    assert "C:\\PATH\\TO\\aws-conn-tray" in content
    assert "<LogonTrigger>" in content


def test_macos_plist_template_has_placeholders():
    path = Path("doc/autostart/macos/aws.conn.tray.plist")
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert "YOUR_USERNAME" in content
    assert "aws-conn-tray" in content
    assert "<key>RunAtLoad</key>" in content and "<true/>" in content
