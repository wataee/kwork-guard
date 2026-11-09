#!/usr/bin/env python3
"""
tools/validate_brief.py - Валидация брифа заказа (WP5).
Проверяет brief.json по схеме и brief.md на заполненность секций и отсутствие {{...}}.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "templates" / "brief.schema.json"
IDENT_RE = re.compile(r"^[A-Za-zА-Яа-я_][A-Za-zА-Яа-я0-9_]*$")
ORDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
ALLOWED_KINDS = {"vpf", "report", "processor", "ext"}


def validate_json_brief(data: dict) -> list[str]:
    errors = []
    # Обязательные поля
    for req in ("order_id", "kind", "object_name", "configuration", "requirements"):
        if req not in data:
            errors.append(f"Поле '{req}' обязательно в brief.json.")

    if "order_id" in data and not ORDER_ID_RE.match(str(data["order_id"])):
        errors.append(f"order_id '{data['order_id']}' содержит недопустимые символы (разрешены буквы, цифры, _, -).")

    if "kind" in data and data["kind"] not in ALLOWED_KINDS:
        errors.append(f"kind '{data['kind']}' недопустим. Разрешены: {sorted(ALLOWED_KINDS)}.")

    if "object_name" in data and not IDENT_RE.match(str(data["object_name"])):
        errors.append(f"object_name '{data['object_name']}' должен быть валидным идентификатором 1С.")

    if "requirements" in data:
        if not isinstance(data["requirements"], list) or len(data["requirements"]) == 0:
            errors.append("requirements должен быть непустым списком строк.")

    return errors


def validate_md_brief(content: str) -> list[str]:
    errors = []
    # Проверка обязательных заголовков
    for sec in ("1. Основные сведения", "2. Среда выполнения", "4. Требования к макету"):
        if sec not in content:
            errors.append(f"В brief.md отсутствует обязательная секция '{sec}'.")

    # Проверка незаполненных плейсхолдеров {{...}}
    unresolved = re.findall(r"\{\{([^}]+)\}\}", content)
    if unresolved:
        errors.append(f"В brief.md остались незаполненные плейсхолдеры: {unresolved}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Валидация брифа ТЗ Kwork")
    parser.add_argument("--path", required=True, help="Путь к brief.json или brief.md")
    args = parser.parse_args()

    p = Path(args.path)
    if not p.is_file():
        print(f"[!] Файл не найден: {p}")
        return 1

    errors = []
    if p.suffix.lower() == ".json":
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            errors = validate_json_brief(data)
        except Exception as e:
            errors.append(f"Ошибка парсинга JSON: {e}")
    elif p.suffix.lower() == ".md":
        try:
            with p.open("r", encoding="utf-8") as f:
                content = f.read()
            errors = validate_md_brief(content)
        except Exception as e:
            errors.append(f"Ошибка чтения Markdown: {e}")
    else:
        print(f"[!] Неподдерживаемый формат: {p.suffix}. Ожидается .json или .md.")
        return 1

    if errors:
        print(f"[✗] Ошибки валидации '{p.name}':")
        for err in errors:
            print(f"  • {err}")
        return 1

    print(f"[✓] Бриф '{p.name}' успешно прошёл валидацию.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
