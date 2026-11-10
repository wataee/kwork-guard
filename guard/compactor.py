from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any

from guard.compact_schema import compact_1c_xml

logger = logging.getLogger("kwork-guard.compactor")

GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

XML_HINT_TOOLS = {
    "get_metadata_details",
    "get_metadata_objects",
    "read_form",
    "dcs",
    "search_in_dcs",
    "read_file",
}


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


def _looks_like_1c_xml(tool_name: str, text: str) -> bool:
    stripped = text.lstrip("\ufeff").strip()
    if not stripped.startswith("<"):
        return False
    if tool_name in XML_HINT_TOOLS:
        return True
    head = stripped[:400].lower()
    return any(tok in head for tok in ("<mdclass:", "<form:form", "datacompositionschema", "<managedform"))


def compact_mcp_result(tool_name: str, raw_text: str, max_chars: int) -> str:
    formatted = raw_text
    if _looks_like_1c_xml(tool_name, raw_text):
        formatted = compact_1c_xml(raw_text, hint_path=tool_name)
    else:
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


def prune_verified_bsl_history(
    messages: list[dict[str, Any]],
    verified_modules: set[str],
) -> list[dict[str, Any]]:
    if not messages or not verified_modules:
        return copy.deepcopy(messages)

    last_index_by_mod: dict[str, int] = {}
    msg_mod_refs: dict[int, set[str]] = {}

    for idx, msg in enumerate(messages):
        if msg.get("role") == "system":
            continue
        serialized = json.dumps(msg, ensure_ascii=False)
        matched_mods: set[str] = set()
        for mod_path in verified_modules:
            if mod_path and mod_path in serialized:
                matched_mods.add(mod_path)
                last_index_by_mod[mod_path] = idx
        if matched_mods:
            msg_mod_refs[idx] = matched_mods

    pruned: list[dict[str, Any]] = []
    for idx, msg in enumerate(messages):
        cloned = copy.deepcopy(msg)
        role = cloned.get("role")
        refs = msg_mod_refs.get(idx, set())

        superseded_mods = [m for m in refs if last_index_by_mod.get(m, -1) > idx]
        if not superseded_mods or role == "system":
            pruned.append(cloned)
            continue

        if role == "tool":
            cloned["content"] = "[Collapsed: module updated & verified]"
        elif role == "assistant":
            if isinstance(cloned.get("tool_calls"), list):
                for call in cloned["tool_calls"]:
                    fn = call.get("function")
                    if isinstance(fn, dict) and fn.get("name") in ("write_module_source", "patch_method"):
                        args_str = str(fn.get("arguments") or "")
                        if any(m in args_str for m in superseded_mods):
                            try:
                                parsed_args = json.loads(args_str)
                                if "source" in parsed_args:
                                    parsed_args["source"] = "[Collapsed: module updated & verified]"
                                if "new_source" in parsed_args:
                                    parsed_args["new_source"] = "[Collapsed: module updated & verified]"
                                fn["arguments"] = json.dumps(parsed_args, ensure_ascii=False)
                            except json.JSONDecodeError:
                                fn["arguments"] = '{"collapsed": "[Collapsed: module updated & verified]"}'
            elif isinstance(cloned.get("content"), str) and ("Процедура" in cloned["content"] or "Функция" in cloned["content"]):
                cloned["content"] = "[Collapsed: module updated & verified]"

        pruned.append(cloned)

    return pruned
