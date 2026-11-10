from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("kwork-guard.config")

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.getenv("GUARD_CONFIG", ROOT_DIR / "config.json"))

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
    "workspace": {
        "orders_dir": "C:\\Orders",
        "delivery_desktop_dir": "~/Desktop/1C_Delivery",
    },
    "onec": {
        "thin_client_path": "",
        "test_ib_path": "",
    },
    "openrouter": {
        "model_intake": "google/gemini-2.5-flash",
        "usd_to_rub": 95.0,
    },
    "upstream": {
        "edt_url": "http://127.0.0.1:8765/mcp",
        "timeout_sec": 600,
    },
}


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as file_handle:
            data = json.load(file_handle)
            merged = copy.deepcopy(DEFAULT_CONFIG)
            for key, value in data.items():
                if isinstance(value, dict) and key in merged and isinstance(merged[key], dict):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
            return merged
    except Exception as exc:
        logger.warning("Configuration read error from %s: %s", CONFIG_PATH, exc)
        return copy.deepcopy(DEFAULT_CONFIG)


def save_config(config_data: dict[str, Any]) -> bool:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = CONFIG_PATH.with_suffix(".tmp")
        with tmp_file.open("w", encoding="utf-8") as file_handle:
            json.dump(config_data, file_handle, ensure_ascii=False, indent=2)
        os.replace(tmp_file, CONFIG_PATH)
        return True
    except Exception as exc:
        logger.error("Configuration write error to %s: %s", CONFIG_PATH, exc)
        return False
