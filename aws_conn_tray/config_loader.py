import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List

from .paths import CONFIG_PATH


@dataclass
class ForwardTarget:
    """
    1つの SSM ポートフォワード設定を保持するデータクラス。
    name を付けておくことで、メニュー表示や通知で識別しやすくする。
    """

    name: str
    instance_id: str
    profile: str
    region: str
    local_port: int
    remote_host: str
    remote_port: int


@dataclass
class Config:
    """
    アプリ全体の設定。
    - forwards: 複数のポートフォワード定義を配列で保持
    - SSO 関連のタイムアウト・ヘルスチェック間隔は全体共通
    """

    forwards: List[ForwardTarget]
    sso_start_url: str
    sso_login_timeout_seconds: int = 900
    sso_auto_refresh_margin_seconds: int = 300
    tunnel_check_interval_seconds: int = 10


def _parse_forward(entry: Dict[str, Any]) -> ForwardTarget:
    """
    1エントリを ForwardTarget に変換する。必須キーが無ければ ValueError を投げる。
    """
    required = [
        "name",
        "instance_id",
        "profile",
        "region",
        "local_port",
        "remote_host",
        "remote_port",
    ]
    missing = [k for k in required if k not in entry]
    if missing:
        raise ValueError(f"forward に必須項目がありません: {', '.join(missing)}")
    return ForwardTarget(
        name=str(entry["name"]),
        instance_id=str(entry["instance_id"]),
        profile=str(entry["profile"]),
        region=str(entry["region"]),
        local_port=int(entry["local_port"]),
        remote_host=str(entry["remote_host"]),
        remote_port=int(entry["remote_port"]),
    )


def load_config() -> Config:
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            f"設定ファイルが見つかりません: {CONFIG_PATH}\n"
            f"インストーラまたは config.example.json を元に {CONFIG_PATH} を作成してください。"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data: Dict[str, Any] = json.load(f)

    if "forwards" not in data or not isinstance(data["forwards"], list) or not data["forwards"]:
        raise ValueError("設定ファイルに forwards 配列がありません。1件以上のフォワード設定を定義してください。")

    forwards = [_parse_forward(entry) for entry in data["forwards"]]
    return Config(
        forwards=forwards,
        sso_start_url=data["sso_start_url"],
        sso_login_timeout_seconds=int(data.get("sso_login_timeout_seconds", 900)),
        sso_auto_refresh_margin_seconds=int(data.get("sso_auto_refresh_margin_seconds", 300)),
        tunnel_check_interval_seconds=int(data.get("tunnel_check_interval_seconds", 10)),
    )
