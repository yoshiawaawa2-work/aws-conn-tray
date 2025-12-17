import os
import signal
import socket
import subprocess
import time
from typing import List, Optional

from aws_conn_tray.config_loader import ForwardTarget

from .logger import notify, log_message
from . import platforms

try:
    import psutil

    PSUTIL_AVAILABLE = True
except Exception:
    PSUTIL_AVAILABLE = False


def _cleanup_lingering_sessions(reason: str = "") -> None:
    """
    過去の session-manager-plugin / aws ssm start-session が残っていると
    ポートが開放されないままになるケースがあるため、起動前に掃除する。
    OS ごとに利用可能な仕組みで緩やかに終了を試みる。
    - psutil があればクロスプラットフォームに安全終了
    - Windows は taskkill、Linux/macOS は pgrep+kill でフォールバック
    """
    patterns: List[str] = ["session-manager-plugin", "aws ssm start-session"]
    if reason:
        log_message(f"[ssm] cleanup lingering sessions ({reason})")
    # psutil が使える場合はクロスプラットフォームに安全終了
    if PSUTIL_AVAILABLE:
        for pat in patterns:
            targets: List[psutil.Process] = []
            try:
                for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                    info = proc.info
                    name = info.get("name") or ""
                    cmdline = " ".join(info.get("cmdline") or [])
                    if pat in name or pat in cmdline:
                        targets.append(proc)
            except Exception as e:
                log_message(f"[ssm] psutil scan failed for '{pat}': {e}")
                continue

            for proc in targets:
                try:
                    log_message(f"[ssm] cleanup (psutil) term pid={proc.pid} pat={pat}")
                    proc.terminate()
                    proc.wait(timeout=1.5)
                except psutil.TimeoutExpired:
                    try:
                        log_message(f"[ssm] cleanup (psutil) kill pid={proc.pid} pat={pat}")
                        proc.kill()
                    except Exception:
                        pass
                except Exception as e:
                    log_message(f"[ssm] cleanup (psutil) error pid={getattr(proc,'pid',None)}: {e}")
        return

    if platforms.is_windows():
        for pat in patterns:
            try:
                # Windows では taskkill でイメージ名を指定して強制終了を試みる
                result = subprocess.run(
                    ["taskkill", "/IM", f"{pat}.exe", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                log_message(f"[ssm] cleanup (windows) pattern={pat} rc={result.returncode}")
            except FileNotFoundError:
                log_message("[ssm] taskkill not found; skip cleanup on Windows")
            except Exception as e:
                log_message(f"[ssm] cleanup error on Windows for '{pat}': {e}")
        return

    # Linux/macOS 系: pgrep で拾って SIGTERM→SIGKILL を送る
    for pat in patterns:
        try:
            result = subprocess.run(
                ["pgrep", "-f", pat],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode != 0 or not result.stdout.strip():
                continue
            pids = []
            for line in result.stdout.splitlines():
                try:
                    pids.append(int(line.strip()))
                except ValueError:
                    continue
            if not pids:
                continue
            log_message(f"[ssm] cleanup: killing lingering '{pat}' pids={pids}")
            for sig in (signal.SIGTERM, signal.SIGKILL):
                for pid in pids:
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
                time.sleep(0.3)
        except FileNotFoundError:
            log_message("[ssm] pgrep not found; skip cleanup on non-Windows")
        except Exception as e:
            log_message(f"[ssm] cleanup error for pattern '{pat}': {e}")


def _log_port_occupant(port: int) -> None:
    """
    ポートを誰が掴んでいるかをログに出力（利用可能なら ss/lsof を利用）。
    Linux 限定の補助。その他 OS ではスキップ。
    psutil があればそちらを優先し、プロセス名や PID を詳細に残す。
    """
    if PSUTIL_AVAILABLE:
        try:
            lines: List[str] = []
            for conn in psutil.net_connections(kind="inet"):
                laddr = conn.laddr
                if not laddr or getattr(laddr, "port", None) != port:
                    continue
                pid = conn.pid
                proc_name = ""
                if pid:
                    try:
                        proc = psutil.Process(pid)
                        proc_name = proc.name()
                    except Exception:
                        proc_name = ""
                lines.append(f"pid={pid} name={proc_name} status={conn.status}")
            if lines:
                log_message(f"[ssm] port {port} occupant via psutil:\n" + "\n".join(lines))
                return
        except Exception as e:
            log_message(f"[ssm] port occupant check (psutil) failed: {e}")

    if not platforms.is_linux():
        return
    cmds = [
        ["ss", "-ltnp", f"sport = :{port}"],
        ["lsof", "-i", f":{port}"],
    ]
    for cmd in cmds:
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if res.returncode == 0 and res.stdout.strip():
                log_message(f"[ssm] port {port} occupant via {' '.join(cmd)}:\n{res.stdout.strip()}")
                return
        except FileNotFoundError:
            continue
        except Exception as e:
            log_message(f"[ssm] port occupant check failed ({' '.join(cmd)}): {e}")


class SsmPortForward:
    """
    1 つのポートフォワードセッションを管理する。
    name を保持することでログや通知でどのフォワードか識別しやすくする。
    - start(): 衝突チェック→不要プロセス掃除→start-session 起動
    - stop(): セッション終了とポート解放待ち
    - is_alive(): プロセス存否のみ確認
    """

    def __init__(self, target: ForwardTarget) -> None:
        # ForwardTarget をそのまま保持しておく（UI やログで参照するため）
        self.target = target
        self.proc: Optional[subprocess.Popen] = None

    def start(self) -> bool:
        # 念のため既存プロセスを整理（残存 session-manager-plugin がポートを握っていないか確認）
        self.stop()
        _cleanup_lingering_sessions("before start")
        # ローカルポート衝突を確認（他プロセスが掴んでいる場合は待機→諦める）
        for i in range(10):
            if not _is_port_in_use(self.target.local_port):
                break
            log_message(f"[ssm] [{self.target.name}] local port {self.target.local_port} in use, waiting... ({i+1}/10)")
            _log_port_occupant(self.target.local_port)
            time.sleep(0.5)
        else:
            log_message(f"[ssm] [{self.target.name}] local port {self.target.local_port} still in use; abort start-session")
            _log_port_occupant(self.target.local_port)
            notify("ポートフォワード", f"{self.target.name}: ローカルポート {self.target.local_port} が使用中です。")
            return False

        cmd = [
            "aws", "ssm", "start-session",
            "--target", self.target.instance_id,
            "--document-name", "AWS-StartPortForwardingSessionToRemoteHost",
            "--parameters", f"host={self.target.remote_host},portNumber={self.target.remote_port},localPortNumber={self.target.local_port}",
            "--profile", self.target.profile,
            "--region", self.target.region,
        ]
        try:
            self.proc = subprocess.Popen(cmd, start_new_session=True)
            log_message(
                f"[ssm] [{self.target.name}] start-session launched pid={self.proc.pid} "
                f"local={self.target.local_port} -> {self.target.remote_host}:{self.target.remote_port}"
            )
            # 早期異常終了チェック
            time.sleep(0.3)
            rc = self.proc.poll()
            if rc not in (None, 0):
                log_message(f"[ssm] [{self.target.name}] start-session exited early rc={rc}")
                return False
            return True
        except FileNotFoundError:
            notify("aws コマンドが見つかりません", "awscli v2 がインストールされているか確認してください。")
            log_message("[ssm] aws command not found")
            return False
        except Exception as e:
            notify("SSM セッション起動エラー", f"{self.target.name}: {e}")
            log_message(f"[ssm] [{self.target.name}] start-session error: {e}")
            return False

    def is_alive(self) -> bool:
        if self.proc is None:
            return False
        return self.proc.poll() is None

    def stop(self):
        """
        start-session を終了させ、ローカルポートが解放されるまで待つ。
        - POSIX はプロセスグループごと kill して子の session-manager-plugin も巻き込む
        - Windows は terminate→kill で終了
        - ポートが解放されない場合はログに誰が掴んでいるかを出力
        """
        if self.proc is None:
            return
        if self.proc.poll() is None:
            try:
                # プロセスグループごと終了（POSIX）/ 直接 terminate（Windows）
                if platforms.is_windows():
                    self.proc.terminate()
                    self.proc.wait(timeout=2)
                else:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                    self.proc.wait(timeout=2)
            except Exception:
                try:
                    if platforms.is_windows():
                        self.proc.kill()
                    else:
                        os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except Exception:
                    pass
            # ポート解放を待つ（セッションマネージャが残る場合があるため少し粘る）
            for _ in range(20):
                if not _is_port_in_use(self.target.local_port):
                    break
                time.sleep(0.5)
            else:
                log_message(f"[ssm] [{self.target.name}] port {self.target.local_port} still in use after stop")
                _log_port_occupant(self.target.local_port)
        log_message(f"[ssm] [{self.target.name}] stop-session pid={self.proc.pid if self.proc else 'None'}")
        self.proc = None
        # 念のため追加で掃除
        _cleanup_lingering_sessions("after stop")


def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True
