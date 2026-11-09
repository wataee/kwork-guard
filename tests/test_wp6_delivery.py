"""
tests/test_wp6_delivery.py - Тестирование пакета сдачи заказа и макроса complete_order (WP6).
"""
import re
import sys
from pathlib import Path
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import edt_guard
from tests.mock_upstream import MockServerThread, mock_state


@pytest.fixture(scope="module", autouse=True)
def run_mock_server():
    server = MockServerThread(port=18768)
    server.start()
    edt_guard.EDT_URL = "http://127.0.0.1:18768/mcp"
    edt_guard.UP.url = "http://127.0.0.1:18768/mcp"
    edt_guard.UP._session_id = None
    edt_guard.UP._tools_cache = None
    yield
    server.stop()


@pytest.fixture(autouse=True)
def reset_state():
    mock_state["call_history"].clear()
    edt_guard.CACHE.clear()


@pytest.mark.asyncio
async def test_complete_order_dry_run(tmp_path):
    out_dir = tmp_path / "DryDelivery"
    res = await edt_guard.MACRO_IMPL["complete_order"]({
        "projectName": "ВПФ_ПечатьЧека",
        "client_name": "Алексей",
        "order_id": "63984035",
        "output_dir": str(out_dir),
        "dry_run": True
    })
    assert not res.get("isError")
    text = res["content"][0]["text"]
    assert "[DRY RUN]" in text
    assert not out_dir.exists()


@pytest.mark.asyncio
async def test_complete_order_with_errors(tmp_path):
    out_dir = tmp_path / "ErrorDelivery"
    res = await edt_guard.MACRO_IMPL["complete_order"]({
        "projectName": "error_project",
        "client_name": "Алексей",
        "order_id": "63984035",
        "output_dir": str(out_dir)
    })
    assert res.get("isError")
    text = res["content"][0]["text"]
    assert "обнаружены ошибки компиляции" in text
    # Сборка не должна производиться при ошибках
    build_calls = [c for c in mock_state["call_history"] if c["params"].get("name") == "build_external_objects"]
    assert len(build_calls) == 0


@pytest.mark.asyncio
async def test_complete_order_success(tmp_path):
    out_dir = tmp_path / "ClientDelivery"
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "complete_order",
                "arguments": {
                    "projectName": "ВПФ_ПечатьЧека",
                    "object_name": "ВПФ_ПечатьЧека",
                    "client_name": "Алексей Лебедев",
                    "order_id": "63984035",
                    "output_dir": str(out_dir),
                    "git_commit": False
                }
            }
        })
        assert res.status_code == 200
        text = res.json()["result"]["content"][0]["text"]
        assert "успешно сформирован" in text
        
        # Проверяем созданные файлы
        instr_file = out_dir / "ИНСТРУКЦИЯ_ДЛЯ_КЛИЕНТА.md"
        assert instr_file.is_file()
        instr_text = instr_file.read_text(encoding="utf-8")
        assert "Алексей Лебедев" in instr_text
        assert "63984035" in instr_text
        assert not re.search(r"\{\{([^}]+)\}\}", instr_text), "Остались незаполненные плейсхолдеры в инструкции"

        msg_file = out_dir / "СООБЩЕНИЕ_ДЛЯ_KWORK.txt"
        assert msg_file.is_file()
        msg_text = msg_file.read_text(encoding="utf-8")
        assert "Здравствуйте, Алексей Лебедев!" in msg_text
        assert not re.search(r"\{\{([^}]+)\}\}", msg_text), "Остались незаполненные плейсхолдеры в сообщении"

        check_file = out_dir / "ЧЕК_ЛИСТ_ПРОВЕРКИ.md"
        assert check_file.is_file()
        check_text = check_file.read_text(encoding="utf-8")
        assert not re.search(r"\{\{([^}]+)\}\}", check_text), "Остались незаполненные плейсхолдеры в чек-листе"

        # Проверяем, что был вызван build_external_objects
        build_calls = [c for c in mock_state["call_history"] if c["params"].get("name") == "build_external_objects"]
        assert len(build_calls) > 0
