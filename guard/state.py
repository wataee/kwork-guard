from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Optional

from guard.config import load_config
from guard.fs_utils import atomic_write_json

logger = logging.getLogger("kwork-guard.state")

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

ALLOWED_AFTER_LIMIT = {
    "get_project_errors",
    "get_critical_errors",
    "build_external_objects",
    "build_and_check",
    "complete_order",
}


def sanitize_order_id(raw_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", raw_id.strip())
    cleaned = cleaned.strip("_")
    return cleaned if cleaned else "default"


class OrderState:
    def __init__(self, order_id: str, state_dir: Path) -> None:
        self.order_id = sanitize_order_id(order_id)
        self.state_file = state_dir / f"{self.order_id}.json"
        self.created_at = int(time.time())
        self.updated_at = int(time.time())
        self.calls_count = 0
        self.total_response_chars = 0
        self.errors_count = 0
        self.extra_calls_allowed = 0
        self.real_cost_usd = 0.0
        self.real_cost_rub = 0.0
        self.virtual_cost_usd = 0.0
        self.virtual_cost_rub = 0.0
        self.input_tokens = 0
        self.output_tokens = 0
        self.economy_mode = False
        self.linter_blocks = 0
        self.schemas_compacted = 0
        self.task_type = ""
        self.order_dir: Optional[Path] = None
        self.blocked_calls: list[dict[str, Any]] = []
        self._lock: Optional[asyncio.Lock] = None
        self._thread_lock = threading.RLock()

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "calls_count": self.calls_count,
            "total_response_chars": self.total_response_chars,
            "errors_count": self.errors_count,
            "extra_calls_allowed": self.extra_calls_allowed,
            "real_cost_usd": self.real_cost_usd,
            "real_cost_rub": self.real_cost_rub,
            "virtual_cost_usd": self.virtual_cost_usd,
            "virtual_cost_rub": self.virtual_cost_rub,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "economy_mode": self.economy_mode,
            "linter_blocks": self.linter_blocks,
            "schemas_compacted": self.schemas_compacted,
            "task_type": self.task_type,
            "order_dir": str(self.order_dir) if self.order_dir else "",
            "blocked_calls": self.blocked_calls,
        }

    def load_from_dict(self, data: dict[str, Any]) -> None:
        self.created_at = data.get("created_at", self.created_at)
        self.updated_at = data.get("updated_at", self.updated_at)
        self.calls_count = data.get("calls_count", 0)
        self.total_response_chars = data.get("total_response_chars", 0)
        self.errors_count = data.get("errors_count", 0)
        self.extra_calls_allowed = data.get("extra_calls_allowed", 0)
        self.real_cost_usd = float(data.get("real_cost_usd", 0.0))
        self.real_cost_rub = float(data.get("real_cost_rub", 0.0))
        self.virtual_cost_usd = float(data.get("virtual_cost_usd", 0.0))
        self.virtual_cost_rub = float(data.get("virtual_cost_rub", 0.0))
        self.input_tokens = int(data.get("input_tokens", 0))
        self.output_tokens = int(data.get("output_tokens", 0))
        self.economy_mode = bool(data.get("economy_mode", False))
        self.linter_blocks = int(data.get("linter_blocks", 0))
        self.schemas_compacted = int(data.get("schemas_compacted", 0))
        self.task_type = str(data.get("task_type") or "")
        raw_dir = str(data.get("order_dir") or "").strip()
        if raw_dir and Path(raw_dir).is_dir():
            self.order_dir = Path(raw_dir)
        self.blocked_calls = data.get("blocked_calls", [])

    def save(self) -> None:
        with self._thread_lock:
            self.updated_at = int(time.time())
            payload = self.to_dict()
            try:
                atomic_write_json(self.state_file, payload)
            except Exception as exc:
                logger.error("State save failure for %s: %s", self.order_id, exc)
            if self.order_dir is not None and self.order_dir.is_dir():
                metrics_file = self.order_dir / "guard_metrics.json"
                try:
                    atomic_write_json(metrics_file, payload)
                except Exception as exc:
                    logger.warning("Order guard_metrics.json save failure for %s: %s", self.order_id, exc)


class OrderManager:
    def __init__(self, state_dir: Optional[Path] = None) -> None:
        if state_dir is None:
            state_dir = Path(__file__).resolve().parent.parent / "state"
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.orders: dict[str, OrderState] = {}
        self.order_dirs: dict[str, Path] = {}
        self._manager_lock: Optional[asyncio.Lock] = None
        self._thread_lock = threading.RLock()
        self._load_active_workspace_map()

    def _load_active_workspace_map(self) -> None:
        env_dir = os.getenv("KWORK_ORDER_DIR", "").strip()
        if env_dir and Path(env_dir).is_dir():
            p = Path(env_dir).resolve()
            self.order_dirs[sanitize_order_id(p.name)] = p
        ws_file = self.state_dir / "active_workspace.json"
        if ws_file.is_file():
            try:
                data = json.loads(ws_file.read_text(encoding="utf-8"))
                mapping = data.get("orders") or {}
                for oid, dir_str in mapping.items():
                    cand = Path(str(dir_str))
                    if cand.is_dir():
                        self.order_dirs[sanitize_order_id(oid)] = cand
            except Exception as exc:
                logger.debug("Workspace map load skipped: %s", exc)

    def register_order_dir(
        self,
        order_id: str,
        order_dir: Path,
        task_type: str = "",
    ) -> None:
        safe_id = sanitize_order_id(order_id)
        resolved = order_dir.resolve()
        if not resolved.is_dir():
            return
        with self._thread_lock:
            self.order_dirs[safe_id] = resolved
            state = self.get_order_sync(safe_id)
            changed = False
            if state.order_dir != resolved:
                state.order_dir = resolved
                changed = True
            if task_type and state.task_type != task_type:
                state.task_type = task_type
                changed = True
            if changed:
                state.save()

    def resolve_order_dir(
        self,
        order_id: str,
        headers: Optional[dict[str, str]] = None,
    ) -> Optional[Path]:
        if headers:
            raw_hdr = headers.get("x-order-dir") or headers.get("X-Order-Dir") or ""
            if raw_hdr.strip() and Path(raw_hdr.strip()).is_dir():
                resolved = Path(raw_hdr.strip()).resolve()
                self.register_order_dir(order_id, resolved, headers.get("x-task-type") or headers.get("X-Task-Type") or "")
                return resolved

        safe_id = sanitize_order_id(order_id)
        if safe_id in self.order_dirs and self.order_dirs[safe_id].is_dir():
            return self.order_dirs[safe_id]

        ws_file = self.state_dir / "active_workspace.json"
        if ws_file.is_file():
            try:
                data = json.loads(ws_file.read_text(encoding="utf-8"))
                mapping = data.get("orders") or {}
                if order_id in mapping and Path(mapping[order_id]).is_dir():
                    cand = Path(mapping[order_id]).resolve()
                    self.order_dirs[safe_id] = cand
                    return cand
                if safe_id in mapping and Path(mapping[safe_id]).is_dir():
                    cand = Path(mapping[safe_id]).resolve()
                    self.order_dirs[safe_id] = cand
                    return cand
                active_dir = str(data.get("active_order_dir") or "").strip()
                if safe_id == "default" and active_dir and Path(active_dir).is_dir():
                    return Path(active_dir).resolve()
            except Exception as exc:
                logger.debug("Workspace resolution read failed: %s", exc)

        env_dir = os.getenv("KWORK_ORDER_DIR", "").strip()
        if env_dir and Path(env_dir).is_dir():
            return Path(env_dir).resolve()

        cfg = load_config()
        orders_root_raw = str(cfg.get("workspace", {}).get("orders_dir") or "").strip()
        if orders_root_raw:
            orders_root = Path(orders_root_raw)
            for candidate_name in (order_id, safe_id):
                cand = orders_root / candidate_name
                if cand.is_dir() and (cand / "order.json").is_file():
                    self.order_dirs[safe_id] = cand.resolve()
                    return cand.resolve()
        return None

    def _get_lock(self) -> asyncio.Lock:
        if self._manager_lock is None:
            self._manager_lock = asyncio.Lock()
        return self._manager_lock

    @staticmethod
    def resolve_order_id(
        headers: dict[str, str],
        arguments: dict[str, Any],
        path_order_id: Optional[str] = None,
    ) -> str:
        if path_order_id:
            cleaned_path = sanitize_order_id(path_order_id)
            if cleaned_path != "default":
                return cleaned_path

        header_id = headers.get("x-order-id") or headers.get("X-Order-Id")
        if header_id:
            cleaned_header = sanitize_order_id(header_id)
            if cleaned_header != "default":
                return cleaned_header

        proj = arguments.get("projectName") or arguments.get("name")
        if proj and isinstance(proj, str):
            cleaned_proj = sanitize_order_id(proj)
            if cleaned_proj != "default":
                return cleaned_proj

        return "default"

    def get_order_sync(self, order_id: str) -> OrderState:
        safe_id = sanitize_order_id(order_id)
        with self._thread_lock:
            if safe_id in self.orders:
                return self.orders[safe_id]

            state = OrderState(safe_id, self.state_dir)
            if state.state_file.is_file():
                try:
                    with state.state_file.open("r", encoding="utf-8") as file_handle:
                        data = json.load(file_handle)
                    state.load_from_dict(data)
                except Exception as exc:
                    logger.warning("Corrupted order state %s (%s), recreating.", safe_id, exc)
                    state.save()
            else:
                state.save()

            if safe_id in self.order_dirs and self.order_dirs[safe_id].is_dir():
                state.order_dir = self.order_dirs[safe_id]

            self.orders[safe_id] = state
            return state

    async def get_order(self, order_id: str) -> OrderState:
        async with self._get_lock():
            return self.get_order_sync(order_id)

    async def check_budget(self, order_id: str, tool_name: str) -> tuple[bool, Optional[str]]:
        config = load_config()
        limits = config.get("limits", {})
        max_calls = limits.get("max_calls_per_order", 25)
        max_chars = limits.get("max_response_chars_per_order", 150000)
        warn_ratio = limits.get("warn_at_ratio", 0.8)

        state = await self.get_order(order_id)
        effective_limit = max_calls + state.extra_calls_allowed

        if tool_name in ALLOWED_AFTER_LIMIT:
            return False, None

        if state.calls_count >= effective_limit:
            message = (
                f"Бюджет заказа '{order_id}' исчерпан ({state.calls_count}/{effective_limit} вызовов). "
                f"Сообщи пользователю итог и жди указаний."
            )
            return True, message

        if state.total_response_chars >= max_chars:
            message = (
                f"Бюджет контекста заказа '{order_id}' исчерпан ({state.total_response_chars}/{max_chars} симв). "
                f"Сообщи пользователю итог и жди указаний."
            )
            return True, message

        warn_threshold = int(effective_limit * warn_ratio)
        if state.calls_count >= warn_threshold:
            pct = int((state.calls_count / effective_limit) * 100)
            left = effective_limit - state.calls_count
            warning = (
                f"[ВНИМАНИЕ: Бюджет вызовов заказа израсходован на {pct}%. "
                f"Осталось {left} вызовов. Используй макросы!]"
            )
            return False, warning

        return False, None

    async def record_call(
        self,
        order_id: str,
        tool_name: str,
        ok: bool,
        resp_chars: int,
        blocked_reason: Optional[str] = None,
    ) -> None:
        state = await self.get_order(order_id)
        async with state._get_lock():
            if state.order_dir is None:
                resolved = self.resolve_order_dir(order_id)
                if resolved is not None:
                    state.order_dir = resolved
            if blocked_reason:
                state.blocked_calls.append({
                    "ts": int(time.time()),
                    "tool": tool_name,
                    "reason": blocked_reason,
                })
                if blocked_reason == "linter_blocked":
                    state.linter_blocks += 1
            else:
                state.calls_count += 1
                state.total_response_chars += resp_chars
                if tool_name == "compact_schema" and ok:
                    state.schemas_compacted += 1
                if not ok:
                    state.errors_count += 1
            state.save()

    async def reset_order(self, order_id: str) -> dict[str, Any]:
        state = await self.get_order(order_id)
        async with state._get_lock():
            state.calls_count = 0
            state.total_response_chars = 0
            state.errors_count = 0
            state.extra_calls_allowed = 0
            state.economy_mode = False
            state.blocked_calls.clear()
            state.save()
            return state.to_dict()

    async def extend_order(self, order_id: str, extra_calls: int) -> dict[str, Any]:
        state = await self.get_order(order_id)
        async with state._get_lock():
            state.extra_calls_allowed += max(1, extra_calls)
            state.save()
            return state.to_dict()

    async def list_orders(self) -> list[dict[str, Any]]:
        results = []
        for file_item in sorted(self.state_dir.glob("*.json")):
            if file_item.name == "events.json":
                continue
            safe_id = file_item.stem
            state = await self.get_order(safe_id)
            results.append(state.to_dict())
        return results
