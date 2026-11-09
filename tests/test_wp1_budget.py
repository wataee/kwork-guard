"""
tests/test_wp1_budget.py - Тесты бюджета заказа, отслеживания состояния и админ-эндпоинтов (WP1).
"""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import httpx
import pytest

import edt_guard
from tests.mock_upstream import MockServerThread

MOCK_PORT = 18765
TMP_STATE_DIR = Path(__file__).resolve().parent / "tmp_state"


@pytest.fixture(scope="module", autouse=True)
def setup_wp1():
    mock_server = MockServerThread(port=MOCK_PORT)
    mock_server.start()

    edt_guard.EDT_URL = f"http://127.0.0.1:{MOCK_PORT}/mcp"
    edt_guard.UP = edt_guard.Upstream()
    TMP_STATE_DIR.mkdir(parents=True, exist_ok=True)
    edt_guard.ORDER_MGR = edt_guard.OrderManager(state_dir=TMP_STATE_DIR)

    yield

    mock_server.stop()
    shutil.rmtree(TMP_STATE_DIR, ignore_errors=True)


@pytest.mark.asyncio
async def test_order_id_resolution():
    """Разрешение order_id из заголовка X-Order-Id или параметров."""
    headers = {"X-Order-Id": "Order_123"}
    assert edt_guard.ORDER_MGR.resolve_order_id(headers, {}) == "Order_123"

    headers_dirty = {"X-Order-Id": "../../evil/path!@#"}
    assert edt_guard.ORDER_MGR.resolve_order_id(headers_dirty, {}) == "evil_path"

    # Из аргументов projectName
    assert edt_guard.ORDER_MGR.resolve_order_id({}, {"projectName": "MyProject"}) == "MyProject"

    # По умолчанию
    assert edt_guard.ORDER_MGR.resolve_order_id({}, {}) == "default"


@pytest.mark.asyncio
async def test_budget_warning_and_exceeded():
    """Тест порога 80% (предупреждение) и 100% (блокировка)."""
    order_id = "test_budget_order"
    # Настраиваем маленький лимит для теста
    state = await edt_guard.ORDER_MGR.get_order(order_id)
    state.calls_count = 19  # 19 из 25 - ниже 80% (80% от 25 = 20)
    state.save()

    blocked, warn = await edt_guard.ORDER_MGR.check_budget(order_id, "list_projects")
    assert not blocked
    assert warn is None

    # Достигли 20 вызовов (80%) -> получаем предупреждение
    state.calls_count = 20
    state.save()
    blocked, warn = await edt_guard.ORDER_MGR.check_budget(order_id, "list_projects")
    assert not blocked
    assert warn is not None
    assert "80%" in warn

    # Достигли 25 вызовов (100%) -> блокировка
    state.calls_count = 25
    state.save()
    blocked, msg = await edt_guard.ORDER_MGR.check_budget(order_id, "list_projects")
    assert blocked is True
    assert "исчерпан" in msg

    # Спасительный гейт (get_project_errors / build_and_check) НЕ блокируется!
    blocked_safe, _ = await edt_guard.ORDER_MGR.check_budget(order_id, "get_project_errors")
    assert blocked_safe is False
    blocked_build, _ = await edt_guard.ORDER_MGR.check_budget(order_id, "build_and_check")
    assert blocked_build is False


@pytest.mark.asyncio
async def test_admin_endpoints_reset_and_extend():
    """Проверка админ-эндпоинтов reset и extend."""
    order_id = "admin_test_order"
    state = await edt_guard.ORDER_MGR.get_order(order_id)
    state.calls_count = 30
    state.save()

    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testclient") as client:
        # Проверяем список
        r_list = await client.get("/admin/orders")
        assert r_list.status_code == 200
        assert any(o["order_id"] == order_id for o in r_list.json())

        # Продлеваем на 10 вызовов
        r_ext = await client.post(f"/admin/orders/{order_id}/extend?calls=10")
        assert r_ext.status_code == 200
        assert r_ext.json()["extra_calls_allowed"] == 10

        # Сбрасываем заказ
        r_res = await client.post(f"/admin/orders/{order_id}/reset")
        assert r_res.status_code == 200
        assert r_res.json()["calls_count"] == 0
        assert r_res.json()["extra_calls_allowed"] == 0


@pytest.mark.asyncio
async def test_corrupted_json_recovery():
    """Поврежденный файл состояния восстанавливается без падения."""
    bad_order_id = "corrupted_order"
    bad_file = TMP_STATE_DIR / f"{bad_order_id}.json"
    bad_file.write_text("{ this is not a valid json !!!", encoding="utf-8")

    # Чтение не падает, а пересоздаёт валидный объект
    state = await edt_guard.ORDER_MGR.get_order(bad_order_id)
    assert state.calls_count == 0
    assert state.order_id == bad_order_id
    # Файл перезаписан в валидный JSON
    with bad_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["order_id"] == bad_order_id


@pytest.mark.asyncio
async def test_concurrent_calls_consistency():
    """20 параллельных вызовов через asyncio.gather не вызывают потерю счётчиков."""
    order_id = "concurrent_order"
    state = await edt_guard.ORDER_MGR.get_order(order_id)
    state.calls_count = 0
    state.save()

    async def single_record():
        await edt_guard.ORDER_MGR.record_call(order_id, "mock_tool", ok=True, resp_chars=100)

    # Запускаем 20 вызовов одновременно
    await asyncio.gather(*(single_record() for _ in range(20)))

    updated_state = await edt_guard.ORDER_MGR.get_order(order_id)
    assert updated_state.calls_count == 20
    assert updated_state.total_response_chars == 2000
