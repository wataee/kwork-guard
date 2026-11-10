from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("kwork-guard.compactor")

GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def strip_noise_from_json(data: Any) -> Any:
    if isinstance(data, dict):
        if len(data) == 1 and "ru" in data:
            return strip_noise_from_json(data["ru"])

        has_name = "name" in data and bool(data["name"])
        cleaned_dict: dict[str, Any] = {}

        for key, value in data.items():
            if has_name and key in ("uuid", "id") and isinstance(value, str) and GUID_PATTERN.match(value):
                continue
            cleaned_val = strip_noise_from_json(value)
            if cleaned_val is None:
                continue
            if isinstance(cleaned_val, (str, list, dict)) and len(cleaned_val) == 0:
                continue
            cleaned_dict[key] = cleaned_val

        if len(cleaned_dict) == 1 and "ru" in cleaned_dict:
            return cleaned_dict["ru"]

        return cleaned_dict

    if isinstance(data, list):
        cleaned_list = []
        for item in data:
            cleaned_item = strip_noise_from_json(item)
            if cleaned_item is not None:
                if isinstance(cleaned_item, (str, list, dict)) and len(cleaned_item) == 0:
                    continue
                cleaned_list.append(cleaned_item)
        return cleaned_list

    return data


def compact_mcp_result(tool_name: str, raw_text: str, max_chars: int) -> str:
    try:
        parsed = json.loads(raw_text)
        cleaned = strip_noise_from_json(parsed)
        formatted = json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        formatted = raw_text

    if len(formatted) <= max_chars:
        return formatted

    cut_size = len(formatted) - max_chars
    trimmed = formatted[:max_chars]
    break_idx = max(trimmed.rfind("\n"), trimmed.rfind("},"), trimmed.rfind(","))
    if break_idx > int(max_chars * 0.75):
        trimmed = trimmed[:break_idx]
        cut_size = len(formatted) - len(trimmed)

    return f"{trimmed}…[обрезано {cut_size} симв.]"
