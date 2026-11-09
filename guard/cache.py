"""guard/cache.py - Кеширование идемпотентных вызовов на чтение с инвалидацией при записи (WP7)."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Optional

READ_PREFIXES = ("read_", "list_", "get_metadata_")
READ_EXACT = {"get_module_structure", "get_symbol_info", "get_configuration_properties"}

WRITE_TOOLS = {
    "write_module_source",
    "modify_metadata",
    "create_metadata",
    "delete_metadata",
    "rename_metadata_object",
    "adopt_metadata_object",
    "dcs",
    "scaffold_vpf",
    "scaffold_report",
    "scaffold_processor",
    "patch_method",
}


def is_cacheable_read(tool_name: str) -> bool:
    if tool_name in READ_EXACT:
        return True
    return any(tool_name.startswith(p) for p in READ_PREFIXES)


def is_write_tool(tool_name: str) -> bool:
    return tool_name in WRITE_TOOLS


class ReadCache:
    def __init__(self, ttl_sec: int = 300, enabled: bool = True) -> None:
        self.ttl_sec = ttl_sec
        self.enabled = enabled
        # {project_name: {cache_key: (response_result, timestamp)}}
        self._store: dict[str, dict[str, tuple[dict[str, Any], float]]] = {}

    @staticmethod
    def _make_key(tool_name: str, args: dict[str, Any]) -> str:
        serialized = json.dumps(args, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(f"{tool_name}:{serialized}".encode("utf-8")).hexdigest()

    def get(self, project_name: str, tool_name: str, args: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not self.enabled or not is_cacheable_read(tool_name):
            return None
        project_store = self._store.get(project_name)
        if not project_store:
            return None
        key = self._make_key(tool_name, args)
        entry = project_store.get(key)
        if not entry:
            return None
        data, ts = entry
        if time.time() - ts > self.ttl_sec:
            project_store.pop(key, None)
            return None
        # Возвращаем копию
        return json.loads(json.dumps(data))

    def put(self, project_name: str, tool_name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        if not self.enabled or not is_cacheable_read(tool_name):
            return
        # Не кешируем ошибки
        if result.get("isError"):
            return
        if project_name not in self._store:
            self._store[project_name] = {}
        key = self._make_key(tool_name, args)
        self._store[project_name][key] = (json.loads(json.dumps(result)), time.time())

    def invalidate_project(self, project_name: str) -> int:
        if project_name in self._store:
            removed = len(self._store[project_name])
            del self._store[project_name]
            return removed
        return 0

    def clear(self) -> None:
        self._store.clear()
