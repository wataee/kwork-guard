"""guard/config.py - Загрузка и типизация настроек kwork-guard."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(os.getenv("GUARD_CONFIG", Path(__file__).resolve().parent.parent / "config.json"))

DEFAULT_CONFIG: dict[str, Any] = {
    "limits": {
        "max_calls_per_order": 25,
        "max_response_chars_per_order": 150000,
        "warn_at_ratio": 0.8,
    },
    "cache": {
        "enabled": True,
        "ttl_sec": 300,
    },
    "logging": {
        "max_bytes": 5242880,
        "backup_count": 3,
    },
    "upstream": {
        "edt_url": "http://127.0.0.1:8765/mcp",
        "timeout_sec": 600,
    },
}


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return DEFAULT_CONFIG.copy()
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
            merged = DEFAULT_CONFIG.copy()
            for k, v in data.items():
                if isinstance(v, dict) and k in merged:
                    merged[k] = {**merged[k], **v}
                else:
                    merged[k] = v
            return merged
    except Exception as exc:
        print(f"[!] Ошибка чтения config.json: {exc}. Применяются значения по умолчанию.")
        return DEFAULT_CONFIG.copy()
