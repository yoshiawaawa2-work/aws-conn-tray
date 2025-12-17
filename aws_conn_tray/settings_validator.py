"""
設定入力のバリデーション専用モジュール。
wx に依存せず、installer_ui など GUI の有無に関係なく利用できる。
"""

from typing import Any, Dict, List

# wx_tray から切り出した項目定義
FORWARD_REQUIRED_KEYS = ["name", "instance_id", "profile", "region", "remote_host"]
FORWARD_INT_KEYS = ["remote_port", "local_port"]
GLOBAL_INT_KEYS = ["sso_login_timeout_seconds", "sso_auto_refresh_margin_seconds", "tunnel_check_interval_seconds"]


def validate_settings_input(data: Dict[str, Any], defaults: Dict[str, Any]):
    """
    設定ダイアログから受け取った値を検証し、正常なら数値を int に変換した dict を返す。
    - forwards: 必須。各 forward に必須文字列と数値を要求。
    - グローバルな数値項目も 0 以上の整数のみ許容。
    """
    errors: List[str] = []
    parsed: Dict[str, Any] = {}

    forwards_raw = data.get("forwards") or []
    if not isinstance(forwards_raw, list) or not forwards_raw:
        errors.append("forwards: 1件以上のフォワード設定を追加してください。")
        return False, errors, {}

    forwards_parsed: List[Dict[str, Any]] = []
    for idx, fw in enumerate(forwards_raw):
        fw_parsed: Dict[str, Any] = {}
        for key in FORWARD_REQUIRED_KEYS:
            val = str(fw.get(key, "")).strip()
            if not val:
                errors.append(f"forward[{idx}].{key}: 必須項目です。")
            fw_parsed[key] = val
        for key in FORWARD_INT_KEYS:
            raw = str(fw.get(key, "")).strip()
            try:
                num = int(raw)
                if num < 0:
                    raise ValueError()
                fw_parsed[key] = num
            except Exception:
                default_val = defaults.get(key, "")
                errors.append(f"forward[{idx}].{key}: 0以上の整数を入力してください。（例: {default_val}）")
        forwards_parsed.append(fw_parsed)

    for key in GLOBAL_INT_KEYS:
        raw = str(data.get(key, "")).strip()
        try:
            num = int(raw)
            if num < 0:
                raise ValueError()
            parsed[key] = num
        except Exception:
            default_val = defaults.get(key, "")
            errors.append(f"{key}: 0以上の整数を入力してください。（デフォルト: {default_val}）")

    parsed["forwards"] = forwards_parsed
    parsed["sso_start_url"] = str(data.get("sso_start_url", "")).strip()
    if not parsed["sso_start_url"]:
        errors.append("sso_start_url: 必須項目です。")

    return len(errors) == 0, errors, parsed
