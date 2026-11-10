from __future__ import annotations

import asyncio
import json
from pathlib import Path
import shutil
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import urllib.error

import httpx
import pytest

import edt_guard
from guard.bsl_linter import lint_bsl_code, LintIssue
from guard.cache import ReadCache
from guard.compactor import compact_mcp_result, strip_noise_from_json
from guard.openrouter_billing import fetch_openrouter_usage, record_order_cost_delta
from guard.state import OrderManager, OrderState, sanitize_order_id
from tools.cost_report import parse_args, generate_report
from tools.order import build_mcp_config, repair_order_mcp_config


def test_sanitize_order_id_various_inputs():
    assert sanitize_order_id("order_123") == "order_123"
    assert sanitize_order_id("  order 456 ") == "order_456"
    assert sanitize_order_id("!!!") == "default"
    assert sanitize_order_id("") == "default"


def test_resolve_order_id_priority():
    headers_both = {"X-Order-Id": "header_order", "x-order-id": "header_order"}
    args_proj = {"projectName": "proj_order"}

    res1 = OrderManager.resolve_order_id(headers_both, args_proj, "path_order")
    assert res1 == "path_order"

    res2 = OrderManager.resolve_order_id(headers_both, args_proj, "default")
    assert res2 == "header_order"

    res3 = OrderManager.resolve_order_id({}, args_proj, None)
    assert res3 == "proj_order"

    res4 = OrderManager.resolve_order_id({}, {}, None)
    assert res4 == "default"


def test_build_and_repair_mcp_config(tmp_path: Path):
    cfg_intake = build_mcp_config("intake", "order_1")
    assert cfg_intake == {"mcpServers": {}}

    cfg_vpf = build_mcp_config("vpf", "order_test", "127.0.0.1", 8766)
    assert "order_test" in cfg_vpf["mcpServers"]["edt"]["url"]
    assert cfg_vpf["mcpServers"]["edt"]["headers"]["X-Order-Id"] == "order_test"

    order_dir = tmp_path / "order_repair"
    order_dir.mkdir()
    (order_dir / "order.json").write_text(
        json.dumps({"order_id": "order_rep", "kind": "report"}, ensure_ascii=False),
        encoding="utf-8",
    )
    ok = repair_order_mcp_config(order_dir)
    assert ok is True
    repaired = json.loads((order_dir / ".roo" / "mcp.json").read_text(encoding="utf-8"))
    assert "order_rep" in repaired["mcpServers"]["edt"]["url"]

    bad_ok = repair_order_mcp_config(tmp_path / "non_existent")
    assert bad_ok is False


def test_disk_cache_persistence_and_ttl(tmp_path: Path):
    cache_dir = tmp_path / "cache_store"
    cache1 = ReadCache(ttl_sec=2, enabled=True, cache_dir=cache_dir)
    project = "ProjectAlpha"
    tool = "list_projects"
    args: dict[str, Any] = {"limit": 10}
    val = {"content": [{"type": "text", "text": "OK"}]}

    cache1.put(project, tool, args, val)
    disk_file = cache1._cache_file(project)
    assert disk_file.is_file()

    cache2 = ReadCache(ttl_sec=2, enabled=True, cache_dir=cache_dir)
    loaded = cache2.get(project, tool, args)
    assert loaded == val

    time.sleep(2.1)
    expired = cache2.get(project, tool, args)
    assert expired is None

    disk_file.write_text("{corrupted json", encoding="utf-8")
    cache3 = ReadCache(ttl_sec=2, enabled=True, cache_dir=cache_dir)
    res_corrupted = cache3.get(project, tool, args)
    assert res_corrupted is None


def test_disk_cache_selective_invalidation(tmp_path: Path):
    cache_dir = tmp_path / "cache_store_sel"
    cache = ReadCache(ttl_sec=60, enabled=True, cache_dir=cache_dir)
    project = "ERP_Base"

    cache.put(project, "read_module_source", {"modulePath": "ПечатьЧека.bsl"}, {"content": [{"type": "text", "text": "PrintCode"}]})
    cache.put(project, "get_metadata_object", {"name": "Документ.РеализацияТоваровУслуг"}, {"content": [{"type": "text", "text": "RealizationMeta"}]})
    cache.put(project, "get_project_errors", {"projectName": project}, {"content": [{"type": "text", "text": "NoErrors"}]})

    assert cache.get(project, "read_module_source", {"modulePath": "ПечатьЧека.bsl"}) is not None
    assert cache.get(project, "get_metadata_object", {"name": "Документ.РеализацияТоваровУслуг"}) is not None

    removed = cache.invalidate_object(project, "ПечатьЧека")
    assert removed >= 1

    assert cache.get(project, "read_module_source", {"modulePath": "ПечатьЧека.bsl"}) is None
    assert cache.get(project, "get_metadata_object", {"name": "Документ.РеализацияТоваровУслуг"}) is not None
    assert cache.get(project, "get_project_errors", {"projectName": project}) is None

    cleared = cache.invalidate_project(project)
    assert cleared >= 1
    assert cache.get(project, "get_metadata_object", {"name": "Документ.РеализацияТоваровУслуг"}) is None


def test_compactor_strips_uuids_and_empty_fields():
    data = {
        "name": "Номенклатура",
        "uuid": "12345678-1234-1234-1234-123456789abc",
        "empty_str": "",
        "empty_list": [],
        "empty_dict": {},
        "none_val": None,
        "title": {"ru": "Список товаров"},
        "nested": {
            "name": "Цена",
            "id": "abcdef12-3456-7890-abcd-ef1234567890",
            "active": True,
        },
    }
    cleaned = strip_noise_from_json(data)
    assert "uuid" not in cleaned
    assert "empty_str" not in cleaned
    assert "empty_list" not in cleaned
    assert "empty_dict" not in cleaned
    assert "none_val" not in cleaned
    assert cleaned["title"] == "Список товаров"
    assert "id" not in cleaned["nested"]
    assert cleaned["nested"]["name"] == "Цена"

    raw_json = json.dumps(data, ensure_ascii=False)
    compacted = compact_mcp_result("test_tool", raw_json, max_chars=40)
    assert len(compacted) <= 65
    assert "обрезано" in compacted


def test_compactor_non_json_input():
    raw_str = "Простой длинный текст без формата json, который превышает заданный лимит символов."
    compacted = compact_mcp_result("test_plain", raw_str, max_chars=25)
    assert len(compacted) <= 45
    assert "обрезано" in compacted


def test_bsl_linter_empty_code():
    assert len(lint_bsl_code("")) == 0


def test_bsl_linter_unbalanced_blocks():
    broken_if = "Процедура Тест()\nЕсли Истина Тогда\nСообщить(1);\nКонецПроцедуры"
    issues = lint_bsl_code(broken_if)
    assert any(i.code == "BSL001_UNBALANCED_BLOCKS" for i in issues)

    valid_if = "Процедура Тест()\nЕсли Истина Тогда\nСообщить(1);\nКонецЕсли;\nКонецПроцедуры"
    assert len(lint_bsl_code(valid_if)) == 0


def test_bsl_linter_keywords_in_strings_and_comments():
    code = (
        'Процедура Тест()\n'
        '    Строка = "Если это не блок КонецЕсли";\n'
        '    // Если закомментировано КонецЕсли\n'
        'КонецПроцедуры'
    )
    issues = lint_bsl_code(code)
    assert len(issues) == 0


def test_bsl_linter_query_in_loop():
    bad_loop = (
        "Процедура Тест()\n"
        "    Для Индекс = 1 По 10 Цикл\n"
        "        Выборка = Запрос.Выполнить().Выбрать();\n"
        "    КонецЦикла;\n"
        "КонецПроцедуры"
    )
    issues = lint_bsl_code(bad_loop)
    codes = [i.code for i in issues]
    assert "BSL002_QUERY_IN_LOOP" in codes


def test_bsl_linter_unresolved_template():
    code = "Процедура {{method_name}}()\nКонецПроцедуры"
    issues = lint_bsl_code(code)
    assert any(i.code == "BSL003_UNRESOLVED_TEMPLATE" for i in issues)


def test_bsl_linter_empty_except():
    bad_try = (
        "Процедура Тест()\n"
        "    Попытка\n"
        "        А = 1 / 0;\n"
        "    Исключение\n"
        "    КонецПопытки;\n"
        "КонецПроцедуры"
    )
    issues = lint_bsl_code(bad_try)
    assert any(i.code == "BSL005_EMPTY_EXCEPT" for i in issues)


def test_bsl_linter_missing_registration():
    code_no_reg = "Процедура Печать()\nКонецПроцедуры"
    issues = lint_bsl_code(code_no_reg, profile="vpf", is_object_module=True)
    assert any(i.code == "BSL004_MISSING_REGISTRATION" for i in issues)

    code_with_reg = (
        "Функция СведенияОВнешнейОбработке() Экспорт\n"
        "    Возврат Новый Структура;\n"
        "КонецФункции"
    )
    issues2 = lint_bsl_code(code_with_reg, profile="vpf", is_object_module=True)
    assert not any(i.code == "BSL004_MISSING_REGISTRATION" for i in issues2)


@pytest.mark.asyncio
async def test_macro_read_bsl_method():
    module_text = (
        "// Header\n"
        "&НаСервере\n"
        "Процедура ОбработатьЗаказ(ЗаказСсылка)\n"
        "    Строка = 1;\n"
        "КонецПроцедуры\n\n"
        "&НаКлиенте\n"
        "Функция ПолучитьСумму()\n"
        "    Возврат 100;\n"
        "КонецФункции\n"
    )

    with patch.object(edt_guard.UP, "tool", new_callable=AsyncMock) as mock_tool:
        mock_tool.return_value = (module_text, False)

        res_proc = await edt_guard.MACRO_IMPL["read_bsl_method"]({
            "projectName": "Proj",
            "modulePath": "ObjectModule.bsl",
            "methodName": "ОбработатьЗаказ",
        })
        assert not res_proc.get("isError")
        txt_proc = res_proc["content"][0]["text"]
        assert "&НаСервере" in txt_proc
        assert "ОбработатьЗаказ" in txt_proc

        res_func = await edt_guard.MACRO_IMPL["read_bsl_method"]({
            "projectName": "Proj",
            "modulePath": "ObjectModule.bsl",
            "methodName": "ПолучитьСумму",
        })
        assert not res_func.get("isError")
        txt_func = res_func["content"][0]["text"]
        assert "&НаКлиенте" in txt_func
        assert "ПолучитьСумму" in txt_func

        res_bad = await edt_guard.MACRO_IMPL["read_bsl_method"]({
            "projectName": "Proj",
            "modulePath": "ObjectModule.bsl",
            "methodName": "НесуществующийМетод",
        })
        assert res_bad.get("isError") is True


@pytest.mark.asyncio
async def test_mcp_url_with_order_id_routing():
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req_body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "library_list", "arguments": {}},
        }
        res = await client.post("/mcp/vpf/order_route_777", json=req_body)
        assert res.status_code == 200
        data = res.json()
        assert not data.get("error")

        state_777 = await edt_guard.ORDER_MGR.get_order("order_route_777")
        assert state_777.calls_count >= 1

        state_def = await edt_guard.ORDER_MGR.get_order("default")
        assert state_def.calls_count == 0


@pytest.mark.asyncio
async def test_linter_blocks_write_without_spending_budget():
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        bad_bsl = "Процедура Ошибка()\n    Для И = 1 По 5 Цикл\n        Запрос.Выполнить();\n    КонецЦикла;\nКонецПроцедуры"
        req_body = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "write_module_source",
                "arguments": {
                    "projectName": "order_lint_test",
                    "modulePath": "ObjectModule.bsl",
                    "source": bad_bsl,
                },
            },
        }
        res = await client.post("/mcp/vpf/order_lint_test", json=req_body)
        assert res.status_code == 200
        data = res.json()
        result = data.get("result", {})
        assert result.get("isError") is True
        assert "BSL Линтер обнаружил" in result["content"][0]["text"]

        state = await edt_guard.ORDER_MGR.get_order("order_lint_test")
        assert state.calls_count == 0


def test_openrouter_billing_success_and_fallback():
    mock_payload = json.dumps({"data": {"usage": 1.25, "limit": 10.0}}).encode("utf-8")
    mock_response = MagicMock()
    mock_response.read.return_value = mock_payload
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        res = fetch_openrouter_usage("test_key")
        assert res["usage_usd"] == 1.25
        assert res["limit_usd"] == 10.0
        assert res["remaining_usd"] == 8.75

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network down")):
        res_fail = fetch_openrouter_usage("test_key")
        assert res_fail["usage_usd"] is None

    no_key = fetch_openrouter_usage("")
    assert no_key["usage_usd"] is None


def test_record_order_cost_delta(tmp_path: Path):
    state = OrderState("order_delta", tmp_path)
    record_order_cost_delta(state, 0.05, 95.0)
    assert state.real_cost_usd == 0.05
    assert state.real_cost_rub == 4.75


def test_cost_report_cli_both_flags(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["cost_report.py", "--order", "order_test_1"])
    args1 = parse_args()
    assert args1.order == "order_test_1"

    monkeypatch.setattr("sys.argv", ["cost_report.py", "--order-id", "order_test_2"])
    args2 = parse_args()
    assert args2.order == "order_test_2"

    generate_report(3.0, "non_existent_filter")
    out = capsys.readouterr().out
    assert "ОТЧЁТ ПО ЗАТРАТАМ" in out


@pytest.mark.asyncio
async def test_admin_reset_and_extend_order_endpoints():
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testclient") as client:
        res_ext = await client.post("/admin/orders/order_adm_test/extend?calls=5")
        assert res_ext.status_code == 200
        data_ext = res_ext.json()
        assert data_ext["extra_calls_allowed"] == 5

        res_rst = await client.post("/admin/orders/order_adm_test/reset")
        assert res_rst.status_code == 200
        data_rst = res_rst.json()
        assert data_rst["calls_count"] == 0
        assert data_rst["extra_calls_allowed"] == 0
