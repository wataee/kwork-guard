from __future__ import annotations

import pytest

from guard.bsl_linter import lint_bsl_code


def test_lint_bsl006_transaction_in_try() -> None:
    bad_code = """
    Попытка
        НачатьТранзакцию();
        ЗафиксироватьТранзакцию();
    Исключение
        ОтменитьТранзакцию();
    КонецПопытки;
    """
    issues = lint_bsl_code(bad_code)
    assert any(iss.code == "BSL006_TRANSACTION_USAGE" for iss in issues)

    good_code = """
    НачатьТранзакцию();
    Попытка
        ЗафиксироватьТранзакцию();
    Исключение
        ОтменитьТранзакцию();
    КонецПопытки;
    """
    good_issues = lint_bsl_code(good_code)
    assert not any(iss.code == "BSL006_TRANSACTION_USAGE" for iss in good_issues)


def test_lint_bsl007_missing_return() -> None:
    bad_func = """
    Функция ВычислитьСумму(А, Б)
        С = А + Б;
    КонецФункции
    """
    issues = lint_bsl_code(bad_func)
    assert any(iss.code == "BSL007_MISSING_RETURN" for iss in issues)

    good_func = """
    Функция ВычислитьСумму(А, Б)
        Возврат А + Б;
    КонецФункции
    """
    good_issues = lint_bsl_code(good_func)
    assert not any(iss.code == "BSL007_MISSING_RETURN" for iss in good_issues)


def test_lint_bsl008_readonly_currentdata() -> None:
    bad_code = """
    Процедура ТаблицаПриАктивизацииСтроки()
        Элементы.Таблица.ТекущиеДанные = Неопределено;
    КонецПроцедуры
    """
    issues = lint_bsl_code(bad_code)
    assert any(iss.code == "BSL008_READONLY_CURRENTDATA" for iss in issues)


def test_lint_bsl009_hardcoded_guid() -> None:
    bad_code = """
    Ид = "e0b3c6b2-84fc-4b5b-9d41-37d45f7d2bc1";
    """
    issues = lint_bsl_code(bad_code)
    assert any(iss.code == "BSL009_HARDCODED_GUID" for iss in issues)

    clean_code = """
    Ид = Ссылка.УникальныйИдентификатор();
    """
    clean_issues = lint_bsl_code(clean_code)
    assert not any(iss.code == "BSL009_HARDCODED_GUID" for iss in clean_issues)


def test_lint_bsl010_dynamic_goto() -> None:
    bad_code = """
    Если А > 0 Тогда
        Перейти ~МеткаКонец;
    КонецЕсли;
    ~МеткаКонец:
    """
    issues = lint_bsl_code(bad_code)
    assert any(iss.code == "BSL010_DYNAMIC_GOTO" for iss in issues)
