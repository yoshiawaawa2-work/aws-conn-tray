import subprocess
from typing import Optional

from .logger import notify


class SsmPortForward:
    def __init__(self, instance_id: str, profile: str, region: str, local_port: int, remote_host: str, remote_port: int) -> None:
        self.instance_id = instance_id
        self.profile = profile
        self.region = region
        self.local_port = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.proc: Optional[subprocess.Popen] = None

    def start(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        cmd = [
            "aws", "ssm", "start-session",
            "--target", self.instance_id,
            "--document-name", "AWS-StartPortForwardingSessionToRemoteHost",
            "--parameters", f"host={self.remote_host},portNumber={self.remote_port},localPortNumber={self.local_port}",
            "--profile", self.profile,
            "--region", self.region,
        ]
        try:
            self.proc = subprocess.Popen(cmd)
        except FileNotFoundError:
            notify("aws コマンドが見つかりません", "awscli v2 がインストールされているか確認してください。")
        except Exception as e:
            notify("SSM セッション起動エラー", str(e))

    def is_alive(self) -> bool:
        if self.proc is None:
            return False
        return self.proc.poll() is None
