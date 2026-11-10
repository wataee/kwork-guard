from __future__ import annotations

import pytest

from guard.macros import (
    macro_bsl_syntax_help,
    macro_bsl_lint_module,
    macro_get_ai_rules,
    macro_compact_schema,
)


def test_macro_bsl_syntax_help() -> None:
    res_type = macro_bsl_syntax_help({"query": "ТаблицаЗначений"})
    assert res_type["isError"] is False
    text = res_type["content"][0]["text"]
    assert "# Тип: ТаблицаЗначений" in text

    res_empty = macro_bsl_syntax_help({})
    assert "Укажите имя типа" in res_empty["content"][0]["text"]


def test_macro_bsl_lint_module() -> None:
    clean_code = "Процедура Тест()\nКонецПроцедуры"
    res_ok = macro_bsl_lint_module({"code": clean_code})
    assert res_ok["isError"] is False
    assert "0 ошибок" in res_ok["content"][0]["text"]

    bad_code = "Функция Тест()\nКонецФункции"
    res_err = macro_bsl_lint_module({"code": bad_code})
    assert res_err["isError"] is True
    assert "BSL007_MISSING_RETURN" in res_err["content"][0]["text"]


def test_macro_get_ai_rules() -> None:
    res = macro_get_ai_rules({})
    assert res["isError"] is False
    text = res["content"][0]["text"]
    assert "Стандарты и правила генерации кода BSL" in text
    assert "BSL002" in text


def test_macro_compact_schema() -> None:
    invalid_arg = macro_compact_schema({"metadata": "not_a_dict"})
    assert invalid_arg["isError"] is True

    valid_meta = {
        "metadata": {
            "name": "ОбработкаТест",
            "attributes": [{"name": "Поле1", "type": "Строка"}],
        }
    }
    res_ok = macro_compact_schema(valid_meta)
    assert res_ok["isError"] is False
    text = res_ok["content"][0]["text"]
    assert "Компактная схема: ОбработкаТест" in text
    assert "Поле1" in text
