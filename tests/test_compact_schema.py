from __future__ import annotations

import pytest

from guard.compact_schema import (
    compact_metadata_schema,
    format_schema_summary,
    sanitize_ident,
    validate_compact_schema,
)


def test_sanitize_ident() -> None:
    assert sanitize_ident("КорректноеИмя_1") == "КорректноеИмя_1"
    assert sanitize_ident("Name With Spaces") == "NameWithSpaces"
    assert sanitize_ident("123НевалидноНачало") == "НевалидноНачало" or "Элемент_" in sanitize_ident("123НевалидноНачало")
    fallback = sanitize_ident("???")
    assert fallback.startswith("Элемент_")


def test_compact_metadata_schema_full() -> None:
    raw = {
        "name": "ОбработкаЗагрузки",
        "synonym": "Обработка загрузки данных",
        "kind": "ExternalDataProcessor",
        "attributes": [
            {"name": "Организация", "synonym": "Организация", "type": "СправочникСсылка.Организации"},
            {"name": "ФайлДанных", "type": "Строка"},
            "ПростойРеквизитСтрока",
        ],
        "tabular_sections": [
            {
                "name": "Товары",
                "synonym": "Список товаров",
                "attributes": [
                    {"name": "Номенклатура", "type": "СправочникСсылка.Номенклатура"},
                    {"name": "Количество", "type": "Число"},
                ],
            }
        ],
        "forms": ["Форма", {"name": "ФормаНастроек"}],
    }

    compacted = compact_metadata_schema(raw)
    assert compacted["name"] == "ОбработкаЗагрузки"
    assert len(compacted["attributes"]) == 3
    assert compacted["attributes"][0]["name"] == "Организация"
    assert len(compacted["tabular_sections"]) == 1
    assert compacted["tabular_sections"][0]["name"] == "Товары"
    assert len(compacted["tabular_sections"][0]["attributes"]) == 2
    assert "Форма" in compacted["forms"]
    assert "ФормаНастроек" in compacted["forms"]


def test_compact_metadata_schema_empty() -> None:
    compacted = compact_metadata_schema({})
    assert compacted["name"] == "НовыйОбъект"
    assert compacted["attributes"] == []
    assert compacted["tabular_sections"] == []
    assert compacted["forms"] == []


def test_validate_compact_schema() -> None:
    valid_schema = {
        "name": "ВалидныйОбъект",
        "attributes": [{"name": "РеквизитОдин"}],
        "tabular_sections": [
            {"name": "Таблица", "attributes": [{"name": "КолонкаОдин"}]}
        ],
    }
    assert validate_compact_schema(valid_schema) == []

    invalid_schema = {
        "name": "123_Неверно",
        "attributes": [{"name": "Реквизит-Неверно"}],
        "tabular_sections": [
            {"name": "ТЧ!Неверно", "attributes": [{"name": "Колонка@Неверно"}]}
        ],
    }
    errors = validate_compact_schema(invalid_schema)
    assert len(errors) >= 3


def test_format_schema_summary() -> None:
    schema = {
        "name": "ТестовыйОтчет",
        "synonym": "Синоним отчета",
        "kind": "ExternalReport",
        "attributes": [{"name": "Период", "synonym": "Период отчета", "type": "СтандартныйПериод"}],
        "tabular_sections": [
            {
                "name": "Отборы",
                "synonym": "Пользовательские отборы",
                "attributes": [{"name": "Поле", "type": "Строка"}],
            }
        ],
        "forms": ["ФормаОтчета"],
    }
    summary = format_schema_summary(schema)
    assert "Компактная схема: ТестовыйОтчет" in summary
    assert "СтандартныйПериод" in summary
    assert "ТЧ `Отборы`" in summary
    assert "ФормаОтчета" in summary

    empty_summary = format_schema_summary({"name": "Пустой", "synonym": "Пустой", "kind": "Doc"})
    assert "(нет дополнительных реквизитов)" in empty_summary
    assert "(нет табличных частей)" in empty_summary
