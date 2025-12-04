import json
import os
from dataclasses import dataclass
from typing import Any, Dict

from .paths import CONFIG_PATH


@dataclass
class Config:
    instance_id: str
    profile: str
    region: str
    local_port: int
    remote_host: str
    remote_port: int
    sso_start_url: str
    sso_login_timeout_seconds: int = 900
    sso_auto_refresh_margin_seconds: int = 300
    tunnel_check_interval_seconds: int = 10


def load_config() -> Config:
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            f"設定ファイルが見つかりません: {CONFIG_PATH}\n"
            f"インストーラまたは config.example.json を元に {CONFIG_PATH} を作成してください。"
        )
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data: Dict[str, Any] = json.load(f)
    return Config(
        instance_id=data["instance_id"],
        profile=data["profile"],
        region=data["region"],
        local_port=int(data["local_port"]),
        remote_host=data.get("remote_host", ""),
        remote_port=int(data["remote_port"]),
        sso_start_url=data["sso_start_url"],
        sso_login_timeout_seconds=int(data.get("sso_login_timeout_seconds", 900)),
        sso_auto_refresh_margin_seconds=int(data.get("sso_auto_refresh_margin_seconds", 300)),
        tunnel_check_interval_seconds=int(data.get("tunnel_check_interval_seconds", 10)),
    )
