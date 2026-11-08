"""
SSO ログインとトークン監視まわりのユーティリティ。
- ~/.aws/sso/cache から accessToken の有効性を確認
- aws sso login --no-browser を実行してデバイスフローの URL/コードを抽出
- Qt WebView/Browsers から再ログインを促す呼び出し元（wx_tray）に結果を返す
"""

import glob
import os
import re
import select
import subprocess
import time
from typing import Optional, Tuple

from .logger import notify, log_message
from .paths import SSO_CACHE_PATTERN, LOG_PATH

try:
    import pty

    PTY_AVAILABLE = True
except ImportError:
    PTY_AVAILABLE = False

LAST_DEVICE_URL: Optional[str] = None
LAST_DEVICE_CODE: Optional[str] = None


def token_cache_exists(cache_pattern: str = SSO_CACHE_PATTERN) -> bool:
    return bool(glob.glob(cache_pattern))


def _iter_sso_cache_entries(
    cache_pattern: str, expected_start_url: Optional[str]
) -> Tuple[str, float]:
    """
    Yield (path, remaining_seconds) for cache files that look like SSO access tokens.
    Filters out client registration files (e.g., botocore-client-default) by requiring
    accessToken/expiresAt and, if指定, startUrl が一致するものだけを返す。
    """
    import json
    import datetime

    now = datetime.datetime.utcnow()
    for path in glob.glob(cache_pattern):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        # 必須フィールドが無いもの（登録情報など）は除外
        access_token = data.get("accessToken")
        expires_at = data.get("expiresAt")
        start_url = data.get("startUrl")
        if not access_token or not expires_at:
            continue
        if expected_start_url and start_url != expected_start_url:
            continue

        try:
            expires_dt = datetime.datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            continue

        remaining = (expires_dt - now).total_seconds()
        yield path, remaining


def check_sso_token(
    cache_pattern: str = SSO_CACHE_PATTERN, expected_start_url: Optional[str] = None
) -> Tuple[bool, Optional[float]]:
    """
    Returns (token_is_valid, remaining_seconds).
    expected_start_url を渡すと、その startUrl を持つ accessToken/ expiresAt 付きファイルのみを採用する。
    """
    entries = list(_iter_sso_cache_entries(cache_pattern, expected_start_url))
    if not entries:
        return False, None

    # expiresAt が最新（remaining が最大）のものを採用
    _, remaining = max(entries, key=lambda x: x[1])
    return remaining > 0, remaining


def is_sso_session_valid(profile: str, region: str) -> bool:
    cmd = ["aws", "sts", "get-caller-identity", "--profile", profile]
    if region:
        cmd += ["--region", region]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _parse_device_flow(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    aws sso login --no-browser の出力から認可URLとデバイスコードらしきものを拾う。
    - authorize を含む URL を優先し、見つからなければ最初の URL を採用
    - 例: XXXX-XXXX 形式のコードを抽出
    """
    url = None
    code = None
    urls = [m.strip().rstrip(".") for m in re.findall(r"https?://\S+", text)]
    for u in urls:
        if "authorize" in u:
            url = u
            break
    if url is None and urls:
        url = urls[0]
    m = re.search(r"[A-Z0-9]{4}-[A-Z0-9]{4}", text)
    if m:
        code = m.group(0)
    return url, code


def run_sso_login(profile: str, region: str, sso_start_url: str, timeout: int) -> bool:
    """
    aws sso login --no-browser を実行し、認可URLを取得して開く。
    - pty があれば対話出力を拾いやすいよう pty で接続
    - authorize URL を検出したらデフォルトブラウザで開く（WebView 側は別経路）
    - 最後にトークン取得完了を待ち、成功/失敗を bool で返す
    """
    global LAST_DEVICE_URL, LAST_DEVICE_CODE
    cmd = ["aws", "sso", "login", "--no-browser", "--profile", profile]
    if region:
        cmd += ["--region", region]

    log_message(f"SSO login start (no-browser): {' '.join(cmd)} (timeout={timeout}s)")

    try:
        if PTY_AVAILABLE:
            import os

            master_fd, slave_fd = pty.openpty()
            proc = subprocess.Popen(
                cmd,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                text=False,
                close_fds=True,
            )
            read_fds = [master_fd]
        else:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            read_fds = [stream for stream in (proc.stdout, proc.stderr) if stream]
    except FileNotFoundError:
        notify("aws コマンドが見つかりません", "awscli v2 がインストールされているか確認してください。")
        return False
    except Exception as e:
        notify("SSO ログインエラー", str(e))
        return False

    deadline = time.time() + timeout
    stdout_acc = ""
    stderr_acc = ""
    authorize_url = None
    device_url = None
    code_hint = None
    url_opened = False
    fallback_opened = False
    start_time = time.time()

    while True:
        if proc.poll() is not None:
            if PTY_AVAILABLE:
                import os

                try:
                    remaining = os.read(read_fds[0], 4096)
                    if remaining:
                        stdout_acc += remaining.decode(errors="ignore")
                except Exception:
                    pass
            out, err = proc.communicate() if not PTY_AVAILABLE else ("", "")
            stdout_acc += (out or "")
            stderr_acc += (err or "")
            if out:
                log_message(f"SSO login stdout (final): {out.strip()}")
            if err:
                log_message(f"SSO login stderr (final): {err.strip()}")
            if not (authorize_url or device_url):
                url, code = _parse_device_flow(stdout_acc + "\n" + stderr_acc)
                if url:
                    if "authorize" in url:
                        authorize_url = url
                    else:
                        device_url = url
                if code and not code_hint:
                    code_hint = code
            break

        ready, _, _ = select.select(read_fds, [], [], 1)
        for stream in ready:
            if PTY_AVAILABLE:
                import os

                try:
                    line_bytes = os.read(stream, 4096)
                    if not line_bytes:
                        continue
                    line = line_bytes.decode(errors="ignore")
                except OSError:
                    continue
            else:
                line = stream.readline()
                if not line:
                    continue

            log_message(f"SSO login stream: {line.strip()}")
            if PTY_AVAILABLE:
                stdout_acc += line
            else:
                if stream is proc.stdout:
                    stdout_acc += line
                else:
                    stderr_acc += line

            url, code = _parse_device_flow(line)
            if code and not code_hint:
                code_hint = code
            if url:
                if "authorize" in url and not authorize_url:
                    authorize_url = url
                elif not device_url:
                    device_url = url
            if authorize_url and not url_opened:
                _open_auth_ui(authorize_url, code_hint)
                url_opened = True

        if not url_opened and not fallback_opened and (time.time() - start_time) > 3:
            _open_auth_ui(authorize_url or device_url or sso_start_url, code_hint)
            fallback_opened = True

        token_ok, _ = check_sso_token(expected_start_url=sso_start_url)
        if token_ok and is_sso_session_valid(profile, region):
            log_message("SSO token detected during login; assuming success")
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            return True

        if time.time() > deadline:
            proc.kill()
            _out, _err = proc.communicate()
            notify(
                "SSO ログインがタイムアウトしました",
                f"{timeout}秒以内にログインが完了しませんでした。ネットワークやブラウザを確認してください。"
            )
            return False

    if proc.returncode != 0:
        detail = stderr_acc.strip() or stdout_acc.strip() or "理由不明のエラー"
        notify("SSO ログインに失敗しました", f"ネットワークや認証状態を確認してください。\n{detail}")
        log_message(f"SSO login failed (code {proc.returncode}): {detail}")
        return False

    if not is_sso_session_valid(profile, region):
        detail = "STS が失敗しました (セッションが無効の可能性)"
        notify("SSO ログインに失敗しました", detail)
        log_message(f"SSO login failed after process success: {detail}")
        return False

    if authorize_url or device_url:
        LAST_DEVICE_URL = authorize_url or device_url
    if code_hint:
        LAST_DEVICE_CODE = code_hint

    log_message("SSO login succeeded")
    if stdout_acc.strip():
        log_message(f"SSO login stdout: {stdout_acc.strip()}")
    if stderr_acc.strip():
        log_message(f"SSO login stderr: {stderr_acc.strip()}")
    return True


def _open_auth_ui(url: str, code: Optional[str]) -> None:
    try:
        subprocess.run(["gio", "open", url], check=False)
    except Exception:
        pass
    notify(
        "SSO トークン更新",
        f"SSO トークンを更新するためログイン画面を開きました。コード: {code or '---'}"
    )
    log_message(f"Opened auth URL (browser): {url}")


def start_sso_login_browser(profile: str, region: str) -> bool:
    """
    デフォルトブラウザで SSO ログインを開始する（no-browser を使わない）。
    """
    cmd = ["aws", "sso", "login", "--profile", profile]
    if region:
        cmd += ["--region", region]
    try:
        subprocess.Popen(cmd)
        log_message(f"SSO login (browser) started: {' '.join(cmd)}")
        return True
    except FileNotFoundError:
        notify("aws コマンドが見つかりません", "AWS CLI v2 をインストールしてください。")
    except Exception as e:
        notify("SSO ログインに失敗しました", str(e))
        log_message(f"SSO login (browser) failed: {e}")
    return False
