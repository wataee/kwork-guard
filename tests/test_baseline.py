"""
tests/test_baseline.py - Базовые тесты прокси edt_guard (WP0).

Проверяет:
1. Инициализацию и приём сессии от EDT-MCP.
2. Фильтрацию списков инструментов по профилям (vpf, report, ext) и обрезку описаний.
3. Локальную предвалидацию (отбой пустых properties, авто-mode=summary).
4. Антицикл (блокировка при 2 одинаковых ошибках подряд).
5. Разбор ответов JSON и SSE (Server-Sent Events).
6. Восстановление сессии при 404 (session lost).
7. Выполнение макросов scaffold_vpf и build_and_check на mock-upstream.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
import pytest

# Настройка путей для импорта
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.mock_upstream import MockServerThread, mock_state
import edt_guard

MOCK_PORT = 18765
GUARD_PORT = 18766


@pytest.fixture(scope="module", autouse=True)
def setup_mock_and_guard():
    # Запускаем Mock EDT на порту 18765
    mock_server = MockServerThread(port=MOCK_PORT)
    mock_server.start()

    # Перенаправляем Upstream в edt_guard на наш Mock
    edt_guard.EDT_URL = f"http://127.0.0.1:{MOCK_PORT}/mcp"
    edt_guard.UP = edt_guard.Upstream()
    # Сбрасываем антицикл и кеш
    for g in edt_guard.GUARDS.values():
        g.same.clear()
        g.consec = 0
    edt_guard.CACHE.clear()

    yield

    mock_server.stop()


@pytest.mark.asyncio
async def test_initialize_and_session():
    """Проверка initialize и сохранения mcp-session-id."""
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "1.0"}}
        })
        assert r.status_code == 200
        data = r.json()
        assert data.get("result", {}).get("protocolVersion") == "2024-11-05"


@pytest.mark.asyncio
async def test_tools_list_profile_filtering():
    """Проверка белого списка инструментов для профиля vpf."""
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/list"
        })
        assert r.status_code == 200
        tools = r.json().get("result", {}).get("tools", [])
        tool_names = {t["name"] for t in tools}

        # Инструменты vpf и макросы должны быть
        assert "create_project" in tool_names
        assert "scaffold_vpf" in tool_names
        assert "build_and_check" in tool_names

        # Неподдерживаемый дебаг должен быть отфильтрован
        assert "unsupported_debug_tool" not in tool_names


@pytest.mark.asyncio
async def test_prevalidate_empty_properties():
    """Предвалидация: пустые properties в modify_metadata отбиваются локально."""
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "modify_metadata",
                "arguments": {
                    "projectName": "TestProject",
                    "fqn": "Catalog.Test",
                    "properties": [{}]
                }
            }
        })
        assert r.status_code == 200
        res = r.json().get("result", {})
        assert res.get("isError") is True
        text = res["content"][0]["text"]
        assert "modify_metadata: в properties[0] нет name/value" in text


@pytest.mark.asyncio
async def test_prevalidate_get_tool_guide_summary():
    """Предвалидация: get_tool_guide без параметров получает mode=summary."""
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        args = {"tool": "create_metadata"}
        edt_guard.prevalidate("get_tool_guide", args)
        assert args.get("mode") == "summary"


@pytest.mark.asyncio
async def test_anti_loop_guard():
    """Антицикл: 2 одинаковые ошибки подряд вызывают блокировку."""
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "jsonrpc": "2.0", "id": 10, "method": "tools/call",
            "params": {
                "name": "modify_metadata",
                "arguments": {"projectName": "P", "fqn": "C", "properties": [{}]}
            }
        }
        # 1-й вызов: ошибка предвалидации
        r1 = await client.post("/mcp/vpf", json=payload)
        assert "в properties[0] нет name/value" in r1.json()["result"]["content"][0]["text"]

        # 2-й вызов: ошибка предвалидации + фиксация 2-го сбоя
        r2 = await client.post("/mcp/vpf", json=payload)

        # 3-й вызов: срабатывает антицикл
        r3 = await client.post("/mcp/vpf", json=payload)
        text = r3.json()["result"]["content"][0]["text"]
        assert "СТОП: вызов modify_metadata с этими аргументами уже 2 раза" in text


@pytest.mark.asyncio
async def test_sse_response_handling():
    """Проверка корректного разбора ответов в формате SSE (text/event-stream)."""
    mock_state["force_sse"] = True
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0", "id": 20, "method": "tools/call",
            "params": {"name": "list_projects", "arguments": {}}
        })
        assert r.status_code == 200
        content = r.json().get("result", {}).get("content", [])
        assert len(content) > 0
        assert "executed successfully" in content[0]["text"]
    mock_state["force_sse"] = False


@pytest.mark.asyncio
async def test_session_loss_404_recovery():
    """Проверка переинициализации сессии при потере соединения (HTTP 404)."""
    edt_guard.CACHE.clear()
    mock_state["simulate_session_loss"] = True
    old_session = mock_state["current_session"]

    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0", "id": 30, "method": "tools/call",
            "params": {"name": "list_projects", "arguments": {}}
        })
        assert r.status_code == 200
        # Сессия должна была обновиться после потери
        assert mock_state["current_session"] != old_session
        content = r.json().get("result", {}).get("content", [])
        assert "executed successfully" in content[0]["text"]


@pytest.mark.asyncio
async def test_macro_scaffold_vpf():
    """Сквозная проверка макроса scaffold_vpf на mock-upstream."""
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0", "id": 40, "method": "tools/call",
            "params": {
                "name": "scaffold_vpf",
                "arguments": {
                    "name": "TestVPF",
                    "purposes": ["Документ.СчетНаОплатуПокупателю"],
                    "command_title": "Печать счета",
                    "header_fields": [
                        {"label": "Номер", "parameter": "Номер"},
                        {"label": "Дата", "parameter": "Дата"}
                    ],
                    "columns": [
                        {"title": "Товар", "parameter": "Номенклатура", "width": 30},
                        {"title": "Сумма", "parameter": "Сумма", "width": 15}
                    ],
                    "data_code": "// Выборка данных"
                }
            }
        })
        assert r.status_code == 200
        text = r.json().get("result", {}).get("content", [{}])[0].get("text", "")
        assert "✓ create_project" in text
        assert "✓ create_metadata (макет)" in text
        assert "✓ modify_metadata (ячейки макета)" in text
        assert "✓ write_module_source" in text


@pytest.mark.asyncio
async def test_macro_build_and_check():
    """Проверка макроса build_and_check: проверка ошибок и последующая сборка."""
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0", "id": 50, "method": "tools/call",
            "params": {
                "name": "build_and_check",
                "arguments": {"projectName": "TestVPF"}
            }
        })
        assert r.status_code == 200
        text = r.json().get("result", {}).get("content", [{}])[0].get("text", "")
        assert "Проверка: ошибок не найдено" in text
        assert "Сборка:" in text
