"""guard/state.py - Отслеживание состояния заказов, бюджетов и вызовов (WP1)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from guard.config import load_config

logger = logging.getLogger("kwork-guard.state")

SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Инструменты, которые разрешено вызывать даже при исчерпании бюджета (спасительные гейты)
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
        self.blocked_calls: list[dict[str, Any]] = []
        self._lock: Optional[asyncio.Lock] = None

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
            "blocked_calls": self.blocked_calls,
        }

    def load_from_dict(self, data: dict[str, Any]) -> None:
        self.created_at = data.get("created_at", self.created_at)
        self.updated_at = data.get("updated_at", self.updated_at)
        self.calls_count = data.get("calls_count", 0)
        self.total_response_chars = data.get("total_response_chars", 0)
        self.errors_count = data.get("errors_count", 0)
        self.extra_calls_allowed = data.get("extra_calls_allowed", 0)
        self.blocked_calls = data.get("blocked_calls", [])

    def save(self) -> None:
        self.updated_at = int(time.time())
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.state_file.with_suffix(".tmp")
        try:
            with tmp_file.open("w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            tmp_file.replace(self.state_file)
        except Exception as exc:
            logger.error("Ошибка сохранения состояния заказа %s: %exc", self.order_id, exc)


class OrderManager:
    def __init__(self, state_dir: Optional[Path] = None) -> None:
        if state_dir is None:
            state_dir = Path(__file__).resolve().parent.parent / "state"
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.orders: dict[str, OrderState] = {}
        self._manager_lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._manager_lock is None:
            self._manager_lock = asyncio.Lock()
        return self._manager_lock

    @staticmethod
    def resolve_order_id(headers: dict[str, str], arguments: dict[str, Any]) -> str:
        # 1. Заголовок X-Order-Id
        header_id = headers.get("x-order-id") or headers.get("X-Order-Id")
        if header_id:
            return sanitize_order_id(header_id)
        # 2. Имя проекта в аргументах
        proj = arguments.get("projectName") or arguments.get("name")
        if proj and isinstance(proj, str):
            return sanitize_order_id(proj)
        # 3. По умолчанию
        return "default"

    async def get_order(self, order_id: str) -> OrderState:
        safe_id = sanitize_order_id(order_id)
        async with self._get_lock():
            if safe_id in self.orders:
                return self.orders[safe_id]

            state = OrderState(safe_id, self.state_dir)
            if state.state_file.is_file():
                try:
                    with state.state_file.open("r", encoding="utf-8") as f:
                        data = json.load(f)
                    state.load_from_dict(data)
                except Exception as exc:
                    logger.warning("Поврежденный файл состояния заказа %s (%s). Создаётся новое состояние.", safe_id, exc)
                    state.save()
            else:
                state.save()

            self.orders[safe_id] = state
            return state

    async def check_budget(self, order_id: str, tool_name: str) -> tuple[bool, Optional[str]]:
        """
        Проверяет бюджет заказа.
        Возвращает:
          (is_blocked: bool, warning_or_stop_message: Optional[str])
        """
        config = load_config()
        limits = config.get("limits", {})
        max_calls = limits.get("max_calls_per_order", 25)
        max_chars = limits.get("max_response_chars_per_order", 150000)
        warn_ratio = limits.get("warn_at_ratio", 0.8)

        state = await self.get_order(order_id)
        effective_limit = max_calls + state.extra_calls_allowed

        # Если инструмент разрешён после лимита (спасительный гейт), не блокируем
        if tool_name in ALLOWED_AFTER_LIMIT:
            return False, None

        # Проверка жесткого лимита вызовов
        if state.calls_count >= effective_limit:
            msg = (
                f"Бюджет заказа '{order_id}' исчерпан ({state.calls_count}/{effective_limit} вызовов). "
                f"Сообщи пользователю итог и жди указаний."
            )
            return True, msg

        # Проверка жесткого лимита символов ответов
        if state.total_response_chars >= max_chars:
            msg = (
                f"Бюджет контекста заказа '{order_id}' исчерпан ({state.total_response_chars}/{max_chars} симв). "
                f"Сообщи пользователю итог и жди указаний."
            )
            return True, msg

        # Проверка порога предупреждения (например, 80%)
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
            if blocked_reason:
                state.blocked_calls.append({
                    "ts": int(time.time()),
                    "tool": tool_name,
                    "reason": blocked_reason,
                })
            else:
                state.calls_count += 1
                state.total_response_chars += resp_chars
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
        # Сканируем state_dir
        for file in sorted(self.state_dir.glob("*.json")):
            if file.name == "events.json":
                continue
            safe_id = file.stem
            state = await self.get_order(safe_id)
            results.append(state.to_dict())
        return results
