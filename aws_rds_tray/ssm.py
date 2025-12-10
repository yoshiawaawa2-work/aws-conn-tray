import subprocess
import socket
import time
import os
import signal
from typing import Optional

from .logger import notify, log_message


class SsmPortForward:
    def __init__(self, instance_id: str, profile: str, region: str, local_port: int, remote_host: str, remote_port: int) -> None:
        self.instance_id = instance_id
        self.profile = profile
        self.region = region
        self.local_port = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.proc: Optional[subprocess.Popen] = None

    def start(self) -> bool:
        # 念のため既存プロセスを整理
        self.stop()
        # ローカルポート衝突を確認（他プロセスが掴んでいる場合は待機→諦める）
        for i in range(3):
            if not _is_port_in_use(self.local_port):
                break
            log_message(f"[ssm] local port {self.local_port} in use, waiting... ({i+1}/3)")
            time.sleep(0.5)
        else:
            log_message(f"[ssm] local port {self.local_port} still in use; abort start-session")
            notify("ポートフォワード", f"ローカルポート {self.local_port} が使用中です。")
            return False

        cmd = [
            "aws", "ssm", "start-session",
            "--target", self.instance_id,
            "--document-name", "AWS-StartPortForwardingSessionToRemoteHost",
            "--parameters", f"host={self.remote_host},portNumber={self.remote_port},localPortNumber={self.local_port}",
            "--profile", self.profile,
            "--region", self.region,
        ]
        try:
            self.proc = subprocess.Popen(cmd, start_new_session=True)
            log_message(f"[ssm] start-session launched pid={self.proc.pid} local={self.local_port} -> {self.remote_host}:{self.remote_port}")
            # 早期異常終了チェック
            time.sleep(0.3)
            rc = self.proc.poll()
            if rc not in (None, 0):
                log_message(f"[ssm] start-session exited early rc={rc}")
                return False
            return True
        except FileNotFoundError:
            notify("aws コマンドが見つかりません", "awscli v2 がインストールされているか確認してください。")
            log_message("[ssm] aws command not found")
            return False
        except Exception as e:
            notify("SSM セッション起動エラー", str(e))
            log_message(f"[ssm] start-session error: {e}")
            return False

    def is_alive(self) -> bool:
        if self.proc is None:
            return False
        return self.proc.poll() is None

    def stop(self):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            try:
                # プロセスグループごと終了（session-manager-plugin もまとめて落とす）
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except Exception:
                    pass
            # ポート解放を待つ
            time.sleep(0.5)
        log_message(f"[ssm] stop-session pid={self.proc.pid if self.proc else 'None'}")
        self.proc = None


def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True
