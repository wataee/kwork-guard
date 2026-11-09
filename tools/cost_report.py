#!/usr/bin/env python3
"""
tools/cost_report.py - Отчёт по вызовам, токенам и затратам по заказам (WP7).
Анализирует guard_log.jsonl и файлы state/<order_id>.json.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_PATH = BASE_DIR / "guard_log.jsonl"
STATE_DIR = BASE_DIR / "state"

# Примерная базовая цена OpenRouter за 1M токенов (например, Claude 3.7 Sonnet / DeepSeek)
DEFAULT_COST_PER_M = 3.0  # $3 за 1M токенов по умолчанию


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Отчёт по затратам и токенам kwork-guard")
    parser.add_argument("--price-per-m", type=float, default=DEFAULT_COST_PER_M,
                        help="Цена в $ за 1M токенов (по умолчанию 3.0)")
    parser.add_argument("--order", type=str, default=None,
                        help="Фильтр по конкретному order_id")
    return parser.parse_args()


def generate_report(price_per_m: float, filter_order: str | None = None) -> None:
    print("=" * 75)
    print("ОТЧЁТ ПО ЗАТРАТАМ И ВЫЗОВАМ KWORK-GUARD")
    print("=" * 75)

    # 1. Читаем state/<order_id>.json
    orders_data: dict[str, dict[str, Any]] = {}
    if STATE_DIR.is_dir():
        for file in sorted(STATE_DIR.glob("*.json")):
            try:
                with file.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                oid = data.get("order_id", file.stem)
                if filter_order and oid != filter_order:
                    continue
                orders_data[oid] = data
            except Exception:
                continue

    # 2. Читаем guard_log.jsonl
    log_calls = []
    if LOG_PATH.is_file():
        try:
            with LOG_PATH.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            log_calls.append(json.loads(line))
                        except Exception:
                            continue
        except Exception:
            pass

    if not orders_data and not log_calls:
        print("[!] Нет данных в state/ и guard_log.jsonl.")
        return

    # Метрики по логам
    order_calls: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in log_calls:
        oid = c.get("order_id", "default")
        if filter_order and oid != filter_order:
            continue
        order_calls[oid].append(c)

    all_order_ids = sorted(set(orders_data.keys()) | set(order_calls.keys()))

    total_all_calls = 0
    total_all_chars = 0
    total_all_est_tokens = 0.0
    total_all_cost = 0.0

    for oid in all_order_ids:
        state = orders_data.get(oid, {})
        calls = order_calls.get(oid, [])

        calls_count = state.get("calls_count", len(calls))
        chars_count = state.get("total_response_chars", sum(c.get("resp_chars", 0) for c in calls))
        errors_count = state.get("errors_count", sum(1 for c in calls if not c.get("ok", True)))
        blocked_count = len(state.get("blocked_calls", []))
        cache_hits = sum(1 for c in calls if c.get("cache_hit") or c.get("note") == "cache_hit")

        err_rate = (errors_count / calls_count * 100) if calls_count > 0 else 0.0
        cache_rate = (cache_hits / (calls_count + cache_hits) * 100) if (calls_count + cache_hits) > 0 else 0.0

        # Оценка токенов: символы / 3.5
        est_tokens = chars_count / 3.5
        est_cost = (est_tokens / 1_000_000) * price_per_m

        total_all_calls += calls_count
        total_all_chars += chars_count
        total_all_est_tokens += est_tokens
        total_all_cost += est_cost

        print(f"\n📁 Заказ: '{oid}'")
        print(f"   • Вызовов: {calls_count} | Ошибок: {errors_count} ({err_rate:.1f}%) | Блокировок бюджета: {blocked_count}")
        print(f"   • Кеш-хитов: {cache_hits} ({cache_rate:.1f}% экономии)")
        print(f"   • Символов ответов: {chars_count:,}".replace(",", " "))
        print(f"   • Оценка токенов: ~{int(est_tokens):,} токенов (грубая оценка: симв/3.5)".replace(",", " "))
        print(f"   • Расчётная стоимость: ~${est_cost:.4f} (@ ${price_per_m}/M токенов)")

        # Топ-5 тяжелых вызовов
        if calls:
            sorted_calls = sorted(calls, key=lambda x: x.get("resp_chars", 0), reverse=True)
            top5 = sorted_calls[:5]
            print("   • Топ-5 тяжелых вызовов:")
            for idx, c in enumerate(top5, 1):
                tool = c.get("tool", "unknown")
                chars = c.get("resp_chars", 0)
                ms = c.get("ms", 0)
                print(f"     {idx}. {tool} — {chars:,} симв ({ms} мс)".replace(",", " "))

    print("\n" + "-" * 75)
    print(f"ИТОГО ПО ВСЕМ ЗАКАЗАМ:")
    print(f"Всего вызовов: {total_all_calls} | Символов: {total_all_chars:,}".replace(",", " "))
    print(f"Всего токенов (оценка): ~{int(total_all_est_tokens):,}".replace(",", " "))
    print(f"Общая расчётная себестоимость API: ~${total_all_cost:.4f}")
    print("=" * 75)
    print("Примечание: расчёт стоимости является оценочным (базируется на симв/3.5).")


if __name__ == "__main__":
    args = parse_args()
    generate_report(args.price_per_m, args.order)
