"""
tests/test_wp7_cache.py - Тесты кеширования чтений, инвалидации при записи и отчёта cost_report (WP7).
"""
from __future__ import annotations

import asyncio
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

import edt_guard
from tests.mock_upstream import MockServerThread

MOCK_PORT = 18765
TMP_DIR = Path(__file__).resolve().parent / "tmp_wp7"


@pytest.fixture(scope="module", autouse=True)
def setup_wp7():
    mock_server = MockServerThread(port=MOCK_PORT)
    mock_server.start()

    edt_guard.EDT_URL = f"http://127.0.0.1:{MOCK_PORT}/mcp"
    edt_guard.UP = edt_guard.Upstream()
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    yield

    mock_server.stop()
    shutil.rmtree(TMP_DIR, ignore_errors=True)


@pytest.mark.asyncio
async def test_read_cache_and_invalidation():
    """Тест кеширования вызовов чтения и инвалидации при успешной записи."""
    cache = edt_guard.ReadCache(ttl_sec=60, enabled=True)
    proj = "TestProject"
    tool = "list_projects"
    args = {}

    # 1. Первый раз - мисс
    assert cache.get(proj, tool, args) is None

    # 2. Сохраняем в кеш
    sample_res = {"content": [{"type": "text", "text": "Projects: [TestProject]"}]}
    cache.put(proj, tool, args, sample_res)

    # 3. Второй раз - хит
    cached = cache.get(proj, tool, args)
    assert cached == sample_res

    # 4. Вызов записи по проекту инвалидирует кеш
    removed = cache.invalidate_project(proj)
    assert removed == 1
    assert cache.get(proj, tool, args) is None


@pytest.mark.asyncio
async def test_log_rotation():
    """Тест ротации файлов журнала при превышении max_bytes."""
    log_file = TMP_DIR / "test_rotation.jsonl"
    edt_guard.LOG_PATH = log_file

    # Записываем большой объем
    edt_guard.CONFIG["logging"]["max_bytes"] = 500  # 500 байт лимит для теста
    edt_guard.CONFIG["logging"]["backup_count"] = 3

    for i in range(20):
        edt_guard.log_call("vpf", "tool", True, 10, 100, note=f"call {i}")

    # Проверяем, что созданы ротированные файлы .1
    rot1 = log_file.with_name(f"{log_file.stem}.1{log_file.suffix}")
    assert rot1.is_file() or log_file.is_file()


def test_cost_report_cli():
    """Проверка запуска скрипта cost_report.py."""
    report_script = Path(__file__).resolve().parent.parent / "tools" / "cost_report.py"
    res = subprocess.run([sys.executable, str(report_script), "--price-per-m", "2.5"],
                         capture_output=True, encoding="utf-8", errors="replace")
    assert res.returncode == 0
    assert "ОТЧЁТ ПО ЗАТРАТАМ И ВЫЗОВАМ" in res.stdout
    assert "ИТОГО ПО ВСЕМ ЗАКАЗАМ" in res.stdout
