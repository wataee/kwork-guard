"""
tests/test_wp2_macros.py - Тестирование макросов экстремальной экономии токенов (WP2).
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import pytest

import edt_guard
from tests.mock_upstream import MockServerThread, mock_state


@pytest.fixture(scope="module", autouse=True)
def run_mock_server():
    server = MockServerThread(port=18767)
    server.start()
    edt_guard.EDT_URL = "http://127.0.0.1:18767/mcp"
    edt_guard.UP.url = "http://127.0.0.1:18767/mcp"
    edt_guard.UP._session_id = None
    edt_guard.UP._tools_cache = None
    yield
    server.stop()


@pytest.fixture(autouse=True)
def reset_state():
    mock_state["call_history"].clear()
    edt_guard.CACHE.clear()


@pytest.mark.asyncio
async def test_bootstrap_gold(tmp_path):
    target = tmp_path / "VPF_Order1"
    
    # 1. Dry run
    res_dry = await edt_guard.MACRO_IMPL["bootstrap_gold"]({
        "template_name": "vpf_standard",
        "target_dir": str(target),
        "new_name": "VPF_Order1",
        "dry_run": True
    })
    assert not res_dry.get("isError")
    assert "[DRY RUN]" in res_dry["content"][0]["text"]
    assert not target.exists()

    # 2. Реальное создание
    res_real = await edt_guard.MACRO_IMPL["bootstrap_gold"]({
        "template_name": "vpf_standard",
        "target_dir": str(target),
        "new_name": "VPF_Order1"
    })
    assert not res_real.get("isError")
    assert "успешно создан" in res_real["content"][0]["text"]
    assert target.is_dir()
    
    # Проверка переименованных файлов и содержимого
    mdo_file = target / "src" / "ExternalDataProcessors" / "VPF_Order1" / "VPF_Order1.mdo"
    assert mdo_file.is_file()
    assert "VPF_Order1" in mdo_file.read_text(encoding="utf-8")
    
    bsl_file = target / "src" / "ExternalDataProcessors" / "VPF_Order1" / "ObjectModule.bsl"
    assert bsl_file.is_file()
    assert "VPF_Order1" in bsl_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_scaffold_report():
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Dry run
        res_dry = await client.post("/mcp/report", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "scaffold_report",
                "arguments": {
                    "name": "ОтчетПоОстаткам",
                    "query": "ВЫБРАТЬ 1 КАК Поле1",
                    "dry_run": True
                }
            }
        })
        assert res_dry.status_code == 200
        text_dry = res_dry.json()["result"]["content"][0]["text"]
        assert "[DRY RUN]" in text_dry

        # Выполнение
        res = await client.post("/mcp/report", json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "scaffold_report",
                "arguments": {
                    "name": "ОтчетПоОстаткам",
                    "query": "ВЫБРАТЬ 1 КАК Поле1"
                }
            }
        })
        assert res.status_code == 200
        text = res.json()["result"]["content"][0]["text"]
        assert "✓ validate_query" in text
        assert "✓ create_project" in text
        assert "✓ create_metadata (СКД)" in text
        assert "✓ dcs (настройка схемы)" in text
        assert "✓ write_module_source" in text


@pytest.mark.asyncio
async def test_scaffold_processor():
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "scaffold_processor",
                "arguments": {
                    "name": "ОбработкаЗагрузки",
                    "command_title": "Загрузить данные",
                    "action_code": "    // Выполнение"
                }
            }
        })
        assert res.status_code == 200
        text = res.json()["result"]["content"][0]["text"]
        assert "✓ create_project" in text
        assert "✓ write_module_source" in text


@pytest.mark.asyncio
async def test_get_critical_errors():
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Проект без ошибок
        res_ok = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "get_critical_errors",
                "arguments": {"projectName": "ЧистыйПроект"}
            }
        })
        assert res_ok.status_code == 200
        assert "критических ошибок не обнаружено" in res_ok.json()["result"]["content"][0]["text"]

        # Проект с ошибками: warning отфильтрован, error сохранен
        res_err = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "get_critical_errors",
                "arguments": {"projectName": "error_project"}
            }
        })
        assert res_err.status_code == 200
        text_err = res_err.json()["result"]["content"][0]["text"]
        assert "ERROR: ExternalDataProcessors" in text_err
        assert "WARNING: Style warning" not in text_err


@pytest.mark.asyncio
async def test_patch_method():
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {
                "name": "patch_method",
                "arguments": {
                    "projectName": "ТестПроект",
                    "modulePath": "ExternalDataProcessors/Тест/ObjectModule.bsl",
                    "methodName": "ЗаполнитьДанные",
                    "new_source": "Процедура ЗаполнитьДанные(Ссылка, ПараметрыШапки)\n    // Новый код\nКонецПроцедуры"
                }
            }
        })
        assert res.status_code == 200
        text = res.json()["result"]["content"][0]["text"]
        assert "успешно обновлен" in text
        
        # Проверяем, что write_module_source был вызван с новым кодом
        write_calls = [c for c in mock_state["call_history"] if c["params"].get("name") == "write_module_source"]
        assert len(write_calls) > 0
        written_source = write_calls[-1]["params"]["arguments"]["source"]
        assert "// Новый код" in written_source
        assert "СведенияОВнешнейОбработке" in written_source
