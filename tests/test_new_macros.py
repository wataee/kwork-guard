from __future__ import annotations

from pathlib import Path

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


@pytest.mark.asyncio
async def test_macros_scaffold_patch_complete_coverage(tmp_path: Path) -> None:
    import json
    from unittest.mock import MagicMock, patch
    from guard import macros

    class _FakeUpstream:
        def __init__(self, fail_on: str = "") -> None:
            self.fail_on = fail_on

        async def tool(self, name: str, args: dict):
            if self.fail_on == name:
                return f"failed {name}", True
            if name == "read_module_source":
                return "Процедура МояПроц()\n\tВозврат;\nКонецПроцедуры\n", False
            if name == "get_project_errors":
                if self.fail_on == "lines_only":
                    return "Предупреждение: игнор\nСтрока 5 проблема синтаксиса\n", False
                return "0 errors found", False
            return "OK", False

    res_bad_rep = await macros.macro_scaffold_report({"name": "1Bad", "query": "ВЫБРАТЬ 1"}, _FakeUpstream())
    assert res_bad_rep["isError"] is True
    res_empty_q = await macros.macro_scaffold_report({"name": "ОтчетТест", "query": "  "}, _FakeUpstream())
    assert res_empty_q["isError"] is True

    for fail_step in ("validate_query", "create_project", "create_metadata", "dcs", "write_module_source"):
        res_f = await macros.macro_scaffold_report({"name": "ОтчетТест", "query": "ВЫБРАТЬ 1"}, _FakeUpstream(fail_step))
        assert res_f["isError"] is True

    res_rep_ok = await macros.macro_scaffold_report({"name": "ОтчетТест", "query": "ВЫБРАТЬ 1"}, _FakeUpstream())
    assert res_rep_ok["isError"] is False

    res_bad_proc = await macros.macro_scaffold_processor({"name": "1Bad"}, _FakeUpstream())
    assert res_bad_proc["isError"] is True
    res_dry_proc = await macros.macro_scaffold_processor({"name": "ОбрТест", "dry_run": True}, _FakeUpstream())
    assert "[DRY RUN]" in res_dry_proc["content"][0]["text"]
    for fail_step in ("create_project", "write_module_source"):
        res_pf = await macros.macro_scaffold_processor({"name": "ОбрТест"}, _FakeUpstream(fail_step))
        assert res_pf["isError"] is True
    res_proc_ok = await macros.macro_scaffold_processor({"name": "ОбрТест"}, _FakeUpstream())
    assert res_proc_ok["isError"] is False

    res_crit_empty = await macros.macro_get_critical_errors({}, _FakeUpstream())
    assert res_crit_empty["isError"] is True
    res_crit_err = await macros.macro_get_critical_errors({"projectName": "P"}, _FakeUpstream("get_project_errors"))
    assert res_crit_err["isError"] is True
    res_crit_lines = await macros.macro_get_critical_errors({"projectName": "P"}, _FakeUpstream("lines_only"))
    assert res_crit_lines["isError"] is True

    for mode in ("before", "after"):
        res_pm = await macros.macro_patch_method(
            {
                "projectName": "P",
                "modulePath": "M.bsl",
                "methodName": "МояПроц",
                "new_source": "Процедура МояПроц()\nКонецПроцедуры",
                "wrap_mode": mode,
            },
            _FakeUpstream(),
        )
        assert res_pm["isError"] is False

    res_pm_bad = await macros.macro_patch_method(
        {
            "projectName": "P",
            "modulePath": "M.bsl",
            "methodName": "МояПроц",
            "new_source": "Процедура МояПроц()\nКонецПроцедуры",
            "wrap_mode": "invalid",
        },
        _FakeUpstream(),
    )
    assert res_pm_bad["isError"] is True

    res_pm_dry = await macros.macro_patch_method(
        {
            "projectName": "P",
            "modulePath": "M.bsl",
            "methodName": "МояПроц",
            "new_source": "Процедура МояПроц()\nКонецПроцедуры",
            "dry_run": True,
        },
        _FakeUpstream(),
    )
    assert "[DRY RUN]" in res_pm_dry["content"][0]["text"]

    res_pm_werr = await macros.macro_patch_method(
        {
            "projectName": "P",
            "modulePath": "M.bsl",
            "methodName": "МояПроц",
            "new_source": "Процедура МояПроц()\nКонецПроцедуры",
        },
        _FakeUpstream("write_module_source"),
    )
    assert res_pm_werr["isError"] is True

    res_co_empty = await macros.macro_complete_order({}, _FakeUpstream())
    assert res_co_empty["isError"] is True

    res_co_gerr = await macros.macro_complete_order({"projectName": "P"}, _FakeUpstream("get_project_errors"))
    assert res_co_gerr["isError"] is True

    res_co_berr = await macros.macro_complete_order(
        {"projectName": "P", "output_dir": str(tmp_path / "out_fail")},
        _FakeUpstream("build_external_objects"),
    )
    assert res_co_berr["isError"] is True

    tmpl_dir = tmp_path / "tmpls"
    tmpl_dir.mkdir()
    (tmpl_dir / "instructions_client.md").write_text("Привет {{client_name}} {{object_name}} {{order_id}} {{kind}}", encoding="utf-8")
    (tmpl_dir / "message_kwork.md").write_text("Заказ {{order_id}}", encoding="utf-8")
    (tmpl_dir / "checklist_manual.md").write_text("Чек {{object_name}}", encoding="utf-8")

    out_dir = tmp_path / "delivery"
    order_json = tmp_path / "order.json"
    order_json.write_text(json.dumps({"status": "in_progress"}), encoding="utf-8")

    with patch.object(macros, "TEMPLATES_DIR", tmpl_dir), \
         patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")):
        res_co_ok = await macros.macro_complete_order(
            {
                "projectName": "МойОбъект",
                "client_name": "Иван",
                "order_id": "o1",
                "output_dir": str(out_dir),
                "git_commit": True,
            },
            _FakeUpstream(),
        )
        assert res_co_ok["isError"] is False
        assert "Git: закоммичено" in res_co_ok["content"][0]["text"]
        assert json.loads(order_json.read_text(encoding="utf-8"))["status"] == "delivered"

    (tmpl_dir / "instructions_client.md").write_text("Unresolved {{missing_var}}", encoding="utf-8")
    with patch.object(macros, "TEMPLATES_DIR", tmpl_dir):
        res_co_unres = await macros.macro_complete_order(
            {"projectName": "МойОбъект", "output_dir": str(out_dir), "git_commit": False},
            _FakeUpstream(),
        )
        assert res_co_unres["isError"] is True

    res_no_rules = macros.macro_get_ai_rules({})
    assert res_no_rules["isError"] is False
    with patch.object(macros, "TEMPLATES_DIR", tmp_path / "missing_tmpls"):
        assert macros.macro_get_ai_rules({})["isError"] is True

