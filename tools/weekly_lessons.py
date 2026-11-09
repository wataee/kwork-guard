#!/usr/bin/env python3
"""
tools/weekly_lessons.py - Анализ повторяющихся сбоев и генерация правил (WP4).
Читает state/events.jsonl, агрегирует частые грабли и формирует lessons_draft.md.
"""
from __future__ import annotations

import argparse
import datetime
import json
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

DEFAULT_EVENTS_PATH = Path(__file__).resolve().parent.parent / "state" / "events.jsonl"
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent.parent / "lessons_draft.md"


def suggest_rule(tool: str, signature: str, note: str) -> str:
    """Генерирует конкретное правило для rules.md на основе характера ошибки."""
    sig_low = signature.lower()
    
    if "modify_metadata" in tool:
        if "properties" in sig_low or "empty" in sig_low:
            return "- НИКОГДА не вызывай modify_metadata с пустым properties. Передавай список [{'name': '...', 'value': '...'}] или объект 'template' для макета."
        return f"- При вызове modify_metadata строго сверяйся с примером: properties=[{{'name':'...','value':'...'}}]."
    
    if "get_tool_guide" in tool:
        return "- При вызове get_tool_guide ВСЕГДА передавай section='...' или mode='summary', чтобы экономить контекст."
    
    if note == "loop_block":
        return f"- Инструмент {tool}: запрещены повторные вызовы с одинаковыми аргументами после ошибки. Измени параметры или используй альтернативный инструмент."
    
    if note == "prevalidate":
        return f"- Инструмент {tool}: отклонён валидатором guard. Проверь обязательные поля перед вызовом."
        
    if "syntax" in sig_low or "синтаксическая" in sig_low:
        return "- Перед компиляцией и сдачей обязательно вызывай get_critical_errors для проверки синтаксиса."
        
    return f"- Инструмент {tool}: частые ошибки. Рекомендуется использовать проверенный макрос или эталонный сниппет из библиотеки."


def generate_report(events_path: Path, min_count: int = 2, days: int = 7) -> str:
    if not events_path.is_file():
        return "# Уроки недели\n\nНет накопленных событий ошибок в events.jsonl."

    cutoff_ts = int((datetime.datetime.now() - datetime.timedelta(days=days)).timestamp())
    
    events: list[dict[str, Any]] = []
    with events_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if ev.get("ts", 0) >= cutoff_ts:
                    events.append(ev)
            except json.JSONDecodeError:
                continue

    if not events:
        return f"# Уроки недели\n\nЗа последние {days} дней ошибок не зафиксировано."

    # Группировка
    grouped = defaultdict(list)
    for ev in events:
        sig = ev.get("signature", "unknown")
        grouped[sig].append(ev)

    repeating = {sig: evs for sig, evs in grouped.items() if len(evs) >= min_count}
    # Сортировка по убыванию частоты
    sorted_repeating = sorted(repeating.items(), key=lambda x: len(x[1]), reverse=True)

    lines = [
        "# Уроки недели и рекомендации для rules.md",
        "",
        f"**Период анализа:** последние {days} дней",
        f"**Всего зафиксировано событий ошибок:** {len(events)}",
        f"**Обнаружено повторяющихся проблем (>= {min_count} раз):** {len(sorted_repeating)}",
        "",
        "## 1. Повторяющиеся паттерны ошибок",
        ""
    ]

    suggested_rules = []

    if not sorted_repeating:
        lines.append("Повторяющихся систематических ошибок не обнаружено. Все ошибки были единичными.")
    else:
        for i, (sig, evs) in enumerate(sorted_repeating, 1):
            sample = evs[0]
            tool = sample.get("tool", "unknown")
            note = sample.get("note", "")
            profiles = sorted(set(e.get("profile", "") for e in evs if e.get("profile")))
            rule = suggest_rule(tool, sig, note)
            suggested_rules.append(rule)

            lines.append(f"### {i}. {tool} ({len(evs)} раз)")
            lines.append(f"- **Сигнатура:** `{sig}`")
            lines.append(f"- **Профили:** {', '.join(profiles)}")
            lines.append(f"- **Рекомендация:**")
            lines.append(f"  > {rule}")
            lines.append("")

    lines.append("## 2. Предлагаемые строки для добавления в .roo/rules-*/rules.md")
    lines.append("")
    if suggested_rules:
        # Убираем дубли
        unique_rules = list(dict.fromkeys(suggested_rules))
        for r in unique_rules:
            lines.append(r)
    else:
        lines.append("Новых правил не требуется.")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Анализ ошибок и генерация уроков недели")
    parser.add_argument("--events", default=str(DEFAULT_EVENTS_PATH), help="Путь к events.jsonl")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="Путь к выходному файлу отчета")
    parser.add_argument("--days", type=int, default=7, help="Количество дней для анализа")
    parser.add_argument("--min-count", type=int, default=2, help="Минимальный порог повторений")
    args = parser.parse_args()

    report = generate_report(Path(args.events), min_count=args.min_count, days=args.days)
    
    out_p = Path(args.output)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(report, encoding="utf-8")
    
    print(f"[✓] Отчет успешно сформирован: {out_p}")
    print(f"    (Проанализировано за {args.days} дней, порог: {args.min_count} повторений)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
