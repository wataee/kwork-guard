from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import re
import threading
import time
from typing import Any, Optional

from guard.fs_utils import atomic_write_json

logger = logging.getLogger("kwork-guard.cache")

READ_PREFIXES = ("read_", "list_", "get_metadata_")
READ_EXACT = {"get_module_structure", "get_symbol_info", "get_configuration_properties", "get_project_errors"}

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
    return any(tool_name.startswith(prefix) for prefix in READ_PREFIXES)


def is_write_tool(tool_name: str) -> bool:
    return tool_name in WRITE_TOOLS


class ReadCache:
    def __init__(
        self,
        ttl_sec: int = 300,
        enabled: bool = True,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self.ttl_sec = ttl_sec
        self.enabled = enabled
        if cache_dir is None:
            cache_dir = Path(__file__).resolve().parent.parent / "state" / "cache"
        self.cache_dir = cache_dir
        if self.enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._store: dict[str, dict[str, dict[str, Any]]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _sanitize_name(name: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", name.strip())
        return cleaned if cleaned else "common"

    def _cache_file(self, project_name: str) -> Path:
        safe_name = self._sanitize_name(project_name)
        return self.cache_dir / f"{safe_name}.json"

    def _load_project_cache(self, project_name: str) -> None:
        if project_name in self._store:
            return
        self._store[project_name] = {}
        target_file = self._cache_file(project_name)
        if not target_file.is_file():
            return
        try:
            with target_file.open("r", encoding="utf-8") as file_handle:
                raw_data = json.load(file_handle)
            now = time.time()
            if isinstance(raw_data, dict):
                for key, item in raw_data.items():
                    if isinstance(item, dict) and (now - item.get("ts", 0)) <= self.ttl_sec:
                        self._store[project_name][key] = item
        except Exception as exc:
            logger.warning("Cache load failure for %s (%s), resetting.", project_name, exc)
            self._store[project_name] = {}

    def _save_project_cache(self, project_name: str) -> None:
        if not self.enabled:
            return
        target_file = self._cache_file(project_name)
        try:
            data = self._store.get(project_name, {})
            atomic_write_json(target_file, data, indent=None)
        except Exception as exc:
            logger.error("Cache save failure for %s: %s", project_name, exc)

    @staticmethod
    def _make_key(tool_name: str, args: dict[str, Any]) -> str:
        serialized = json.dumps(args, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(f"{tool_name}:{serialized}".encode("utf-8")).hexdigest()

    def get(self, project_name: str, tool_name: str, args: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not self.enabled or not is_cacheable_read(tool_name):
            return None
        with self._lock:
            self._load_project_cache(project_name)
            project_store = self._store.get(project_name)
            if not project_store:
                return None
            key = self._make_key(tool_name, args)
            entry = project_store.get(key)
            if not entry:
                return None
            if time.time() - entry.get("ts", 0) > self.ttl_sec:
                project_store.pop(key, None)
                self._save_project_cache(project_name)
                return None
            data = entry.get("result")
            return json.loads(json.dumps(data)) if data is not None else None

    def put(self, project_name: str, tool_name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        if not self.enabled or not is_cacheable_read(tool_name):
            return
        if result.get("isError"):
            return
        with self._lock:
            self._load_project_cache(project_name)
            if project_name not in self._store:
                self._store[project_name] = {}
            key = self._make_key(tool_name, args)
            self._store[project_name][key] = {
                "result": json.loads(json.dumps(result)),
                "ts": time.time(),
                "tool": tool_name,
                "args": json.dumps(args, ensure_ascii=False),
            }
            self._save_project_cache(project_name)

    def invalidate_object(self, project_name: str, object_hint: Optional[str] = None) -> int:
        with self._lock:
            self._load_project_cache(project_name)
            project_store = self._store.get(project_name, {})
            if not object_hint:
                removed_count = len(project_store)
                self._store[project_name] = {}
                target_file = self._cache_file(project_name)
                if target_file.is_file():
                    try:
                        target_file.unlink()
                    except Exception as exc:
                        logger.warning("Cache file unlink failure: %s", exc)
                return removed_count

            keys_to_remove = []
            for key, entry in project_store.items():
                if entry.get("tool") == "get_project_errors":
                    keys_to_remove.append(key)
                    continue
                args_str = entry.get("args", "")
                result_str = json.dumps(entry.get("result", {}), ensure_ascii=False)
                if object_hint in args_str or object_hint in result_str:
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                project_store.pop(key, None)

            if keys_to_remove:
                self._save_project_cache(project_name)
            return len(keys_to_remove)

    def invalidate_project(self, project_name: str) -> int:
        return self.invalidate_object(project_name, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            if self.cache_dir.is_dir():
                for cache_file in self.cache_dir.glob("*.json"):
                    try:
                        cache_file.unlink()
                    except Exception as exc:
                        logger.warning("Cache clear unlink failure: %s", exc)
