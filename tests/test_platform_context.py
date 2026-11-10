from __future__ import annotations

import pytest

from guard.platform_context import (
    PlatformContextService,
    PLATFORM_KNOWLEDGE,
    BslTypeDoc,
    BslMethodDoc,
)


def test_platform_context_get_type_doc() -> None:
    svc = PlatformContextService()
    doc = svc.get_type_doc("ТаблицаЗначений")
    assert doc is not None
    assert doc.name == "ТаблицаЗначений"
    assert "Колонки" in doc.properties
    assert "добавить" in doc.methods

    doc_case = svc.get_type_doc("  таблицазначений  ")
    assert doc_case is not None
    assert doc_case.name == "ТаблицаЗначений"

    unknown = svc.get_type_doc("НесуществующийТип")
    assert unknown is None


def test_platform_context_search_syntax() -> None:
    svc = PlatformContextService()
    empty = svc.search_syntax("")
    assert empty == []

    res_type = svc.search_syntax("запрос")
    assert any(item["name"] == "Запрос" for item in res_type)
    assert any(item["name"] == "Запрос.Выполнить" for item in res_type)

    res_method = svc.search_syntax("скопировать")
    assert any("Скопировать" in item["name"] for item in res_method)

    res_none = svc.search_syntax("abracadabra12345")
    assert res_none == []


def test_platform_context_format_syntax_help() -> None:
    svc = PlatformContextService()

    empty_help = svc.format_syntax_help("   ")
    assert "Укажите имя типа" in empty_help

    type_help = svc.format_syntax_help("ТаблицаЗначений")
    assert "# Тип: ТаблицаЗначений" in type_help
    assert "Колонки" in type_help
    assert "Добавить" in type_help

    method_help = svc.format_syntax_help("ТаблицаЗначений.НайтиСтроки")
    assert "# Метод: ТаблицаЗначений.НайтиСтроки" in method_help
    assert "Параметры:" in method_help
    assert "ПараметрыОтбора" in method_help

    method_not_found = svc.format_syntax_help("ТаблицаЗначений.НеизвестныйМетод")
    assert "В базе синтакс-помощника ничего не найдено" in method_not_found or "Результаты поиска" in method_not_found

    search_help = svc.format_syntax_help("выполнить")
    assert "Результаты поиска" in search_help
    assert "Запрос.Выполнить" in search_help

    not_found = svc.format_syntax_help("zz_non_existent_type_zz")
    assert "ничего не найдено" in not_found
