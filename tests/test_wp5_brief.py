"""
tests/test_wp5_brief.py - Тестирование режима приёма ТЗ и валидации брифов (WP5).
"""
import json
import subprocess
import sys
from pathlib import Path
import pytest

VALIDATE_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "validate_brief.py"
ROOMODES_PATH = Path(__file__).resolve().parent.parent / ".roomodes"


def test_roomodes_kwork_intake():
    text = ROOMODES_PATH.read_text(encoding="utf-8")
    assert "slug: kwork-intake" in text
    assert "1С Приём ТЗ Kwork" in text
    
    # Режим intake не должен содержать доступ к MCP
    intake_section = text.split("slug: kwork-intake")[1]
    assert "mcp" not in intake_section


def test_validate_brief_json_valid(tmp_path):
    valid_data = {
        "order_id": "Order_63984035",
        "kind": "vpf",
        "object_name": "ПечатьЧекаТБанка",
        "configuration": "1С:Управление торговлей 11.5",
        "target_version": "8.3.24",
        "purposes": ["Документ.ЧекККМ"],
        "requirements": ["Добавить реквизиты Т-Банка", "Печать QR-кода"]
    }
    f = tmp_path / "brief.json"
    f.write_text(json.dumps(valid_data, ensure_ascii=False), encoding="utf-8")
    
    proc = subprocess.run(
        [sys.executable, str(VALIDATE_SCRIPT), "--path", str(f)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    assert proc.returncode == 0
    assert "[✓]" in proc.stdout


def test_validate_brief_json_invalid(tmp_path):
    invalid_data = {
        "order_id": "Bad Order ID With Spaces!",
        "kind": "unsupported_kind",
        "object_name": "123_Bad_Name",
        # missing configuration & requirements
    }
    f = tmp_path / "brief.json"
    f.write_text(json.dumps(invalid_data, ensure_ascii=False), encoding="utf-8")
    
    proc = subprocess.run(
        [sys.executable, str(VALIDATE_SCRIPT), "--path", str(f)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    assert proc.returncode == 1
    assert "[✗]" in proc.stdout
    assert "Поле 'configuration' обязательно" in proc.stdout
    assert "недопустимые символы" in proc.stdout


def test_validate_brief_md(tmp_path):
    # 1. С плейсхолдерами -> Ошибка
    tmpl_path = Path(__file__).resolve().parent.parent / "templates" / "brief.md"
    f_unresolved = tmp_path / "brief_unresolved.md"
    f_unresolved.write_text(tmpl_path.read_text(encoding="utf-8"), encoding="utf-8")
    
    proc1 = subprocess.run(
        [sys.executable, str(VALIDATE_SCRIPT), "--path", str(f_unresolved)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    assert proc1.returncode == 1
    assert "незаполненные плейсхолдеры" in proc1.stdout

    # 2. Полностью заполненный -> Успех
    filled_content = """# Бриф заказа 1С

## 1. Основные сведения
- **ID заказа:** Order_123
- **Клиент:** Алексей
- **Тип задачи:** vpf

## 2. Среда выполнения
- **Типовая конфигурация:** УНФ 3.0

## 4. Требования к макету
- Таблица с колонками: Номер, Товар, Количество
"""
    f_valid = tmp_path / "brief_valid.md"
    f_valid.write_text(filled_content, encoding="utf-8")
    
    proc2 = subprocess.run(
        [sys.executable, str(VALIDATE_SCRIPT), "--path", str(f_valid)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    assert proc2.returncode == 0
    assert "[✓]" in proc2.stdout
