#!/usr/bin/env python3
"""
tools/library_verify.py - Утверждение сниппетов библиотеки (WP3).
Устанавливает статус 'verified' только при наличии явного флага --confirm.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

INDEX_PATH = Path(__file__).resolve().parent.parent / "library" / "index.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Верификация сниппетов kwork-guard")
    parser.add_argument("--id", required=True, help="Идентификатор сниппета")
    parser.add_argument("--confirm", action="store_true", help="Явное подтверждение верификации")
    args = parser.parse_args()

    if not INDEX_PATH.is_file():
        print(f"[!] Файл индекса не найден: {INDEX_PATH}")
        return 1

    with INDEX_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    snippets = data.get("snippets", [])
    found = None
    for s in snippets:
        if s["id"] == args.id:
            found = s
            break

    if not found:
        print(f"[!] Сниппет с ID '{args.id}' не найден в библиотеке.")
        return 1

    if not args.confirm:
        print(f"[*] Сниппет найден: '{found['title']}' (текущий статус: {found.get('status')})")
        print("[!] Для перевода в статус 'verified' требуется флаг --confirm.")
        return 2

    # Утверждение
    found["status"] = "verified"
    found["verified_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    with INDEX_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[✓] Сниппет '{args.id}' успешно переведён в статус 'verified' ({found['verified_at']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
