"""
単一インスタンス起動を保証するための簡易ロック。
- fasteners があればファイルロックを利用（クロスプラットフォーム）
- 無い場合はローカルポートの bind でフォールバック
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Optional


class SingleInstanceLock:
    def __init__(self, name: str = "aws-conn-tray", lock_dir: Optional[Path] = None, socket_port: Optional[int] = None):
        self.name = name
        self.lock_dir = Path(lock_dir) if lock_dir else Path.home() / ".aws-conn-tray"
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.lock_dir / f"{name}.lock"
        self.socket_port = socket_port or 53125
        self._file_lock = None
        self._sock: Optional[socket.socket] = None

    def acquire(self) -> bool:
        # まず fasteners があれば使う
        try:
            import fasteners

            self._file_lock = fasteners.InterProcessLock(str(self.lock_path))
            acquired = self._file_lock.acquire(blocking=False)
            if acquired:
                return True
            self._file_lock = None
            return False
        except Exception:
            # fasteners が無い/ロック失敗時はフォールバック
            self._file_lock = None

        # ソケットバインドによるフォールバック
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", int(self.socket_port)))
            sock.listen(1)
            self._sock = sock
            return True
        except Exception:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None
            return False

    def release(self) -> None:
        if self._file_lock:
            try:
                self._file_lock.release()
            except Exception:
                pass
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
