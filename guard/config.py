from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from guard.fs_utils import atomic_write_json

logger = logging.getLogger("kwork-guard.config")

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.getenv("GUARD_CONFIG", ROOT_DIR / "config.json"))

VALID_PROVIDERS = ("antigravity", "openrouter")

DEFAULT_CONFIG: dict[str, Any] = {
    "active_provider": "antigravity",
    "providers": {
        "openrouter": {
            "base_url": "https://openrouter.ai/api/v1",
            "api_key_env": "OPENROUTER_API_KEY",
        },
        "antigravity": {
            "base_url": "http://127.0.0.1:3000/v1",
            "api_key_env": "ANTIGRAVITY_API_KEY",
            "fallback_to_openrouter": False,
            "fallback_ports": [3000, 3001],
        },
    },
    "model_routing": {
        "fast_tasks": "google/gemini-2.5-flash",
        "coding_tasks": "anthropic/claude-3-7-sonnet",
        "economy_mode_model": "google/gemini-2.5-flash",
        "fast_task_types": ["compress", "intake", "summary"],
        "coding_task_types": ["bsl", "skd", "dcs", "edt_mcp"],
    },
    "limits": {
        "max_calls_per_order": 25,
        "max_response_chars_per_order": 150000,
        "warn_at_ratio": 0.8,
    },
    "finance": {
        "kwork_commission_ratio": 0.2,
        "api_budget_warn_ratio": 0.05,
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


def deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dicts(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as file_handle:
            data = json.load(file_handle)
            if not isinstance(data, dict):
                return copy.deepcopy(DEFAULT_CONFIG)
            return deep_merge_dicts(DEFAULT_CONFIG, data)
    except Exception as exc:
        logger.warning("Configuration read error from %s: %s", CONFIG_PATH, exc)
        return copy.deepcopy(DEFAULT_CONFIG)


def save_config(config_data: dict[str, Any]) -> bool:
    try:
        atomic_write_json(CONFIG_PATH, config_data)
        return True
    except Exception as exc:
        logger.error("Configuration write error to %s: %s", CONFIG_PATH, exc)
        return False


def get_active_provider(config_data: Optional[dict[str, Any]] = None) -> str:
    cfg = config_data if config_data is not None else load_config()
    provider = str(cfg.get("active_provider", "openrouter")).strip().lower()
    return provider if provider in VALID_PROVIDERS else "openrouter"


def set_active_provider(provider_name: str) -> tuple[bool, str]:
    normalized = provider_name.strip().lower()
    if normalized not in VALID_PROVIDERS:
        return False, f"Недопустимый провайдер '{provider_name}'. Допустимы: {', '.join(VALID_PROVIDERS)}"
    cfg = load_config()
    cfg["active_provider"] = normalized
    if save_config(cfg):
        return True, normalized
    return False, "Не удалось сохранить config.json"


def get_provider_settings(
    provider_name: Optional[str] = None,
    config_data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    cfg = config_data if config_data is not None else load_config()
    target = provider_name or get_active_provider(cfg)
    providers = cfg.get("providers", {})
    settings = providers.get(target)
    if isinstance(settings, dict):
        return copy.deepcopy(settings)
    return copy.deepcopy(DEFAULT_CONFIG["providers"].get(target, {}))


def select_model_for_task(
    task_type: str,
    config_data: Optional[dict[str, Any]] = None,
    economy_mode: bool = False,
) -> str:
    cfg = config_data if config_data is not None else load_config()
    routing = cfg.get("model_routing", {})
    if economy_mode:
        return str(routing.get("economy_mode_model") or routing.get("fast_tasks") or "google/gemini-2.5-flash")

    normalized_task = task_type.strip().lower()
    fast_types = {str(item).lower() for item in routing.get("fast_task_types", ["compress", "intake", "summary"])}
    coding_types = {str(item).lower() for item in routing.get("coding_task_types", ["bsl", "skd", "dcs", "edt_mcp"])}

    if normalized_task in fast_types:
        return str(routing.get("fast_tasks") or "google/gemini-2.5-flash")
    if normalized_task in coding_types:
        return str(routing.get("coding_tasks") or "anthropic/claude-3-7-sonnet")
    return str(routing.get("coding_tasks") or "anthropic/claude-3-7-sonnet")
