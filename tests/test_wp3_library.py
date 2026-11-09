"""
tests/test_wp3_library.py - Тестирование библиотеки эталонов (WP3).
"""
import copy
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import httpx

import edt_guard
from guard.library import list_snippets, get_snippet, inject_snippets_into_code, load_index


def test_library_index_structure():
    data = load_index()
    assert "snippets" in data
    assert len(data["snippets"]) >= 8
    
    lib_dir = Path(__file__).resolve().parent.parent / "library"
    for s in data["snippets"]:
        assert "id" in s and s["id"]
        assert "title" in s and s["title"]
        assert s["type"] in ("bsl", "query")
        assert isinstance(s["tags"], list) and len(s["tags"]) > 0
        assert s["status"] in ("draft", "verified")
        src_path = lib_dir / s["source_file"]
        assert src_path.is_file(), f"Файл не найден: {src_path}"
        assert src_path.stat().st_size > 0


def test_library_list_and_get():
    all_snippets = list_snippets()
    assert len(all_snippets) >= 8
    
    vat_list = list_snippets(tags=["ндс"])
    assert any(s["id"] == "vat_rate_calc" for s in vat_list)
    
    query_list = list_snippets(snippet_type="query")
    assert all(s["type"] == "query" for s in query_list)
    assert any(s["id"] == "balances_vt_query" for s in query_list)
    
    # get_snippet
    code, meta, err = get_snippet("vat_rate_calc")
    assert err is None
    assert meta["id"] == "vat_rate_calc"
    assert "СтавкаНДС" in code
    
    # Non-existent
    ncode, nmeta, nerr = get_snippet("non_existent_snippet_id_123")
    assert ncode is None
    assert nerr is not None


def test_library_inject():
    base_code = "Процедура Тест()\n    // Базовый код\nКонецПроцедуры"
    injected, warnings = inject_snippets_into_code(["vat_rate_calc", "amount_in_words_bsp"], base_code)
    
    assert "// --- library:vat_rate_calc" in injected
    assert "// --- library:amount_in_words_bsp" in injected
    assert "Процедура Тест()" in injected
    # Проверяем предупреждения о статусе draft
    assert len(warnings) == 2
    assert all("draft" in w for w in warnings)


def test_library_verify_cli(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parent.parent / "tools" / "library_verify.py"
    
    # Запуск без --confirm -> код 2
    proc_no_confirm = subprocess.run(
        [sys.executable, str(script), "--id", "vat_rate_calc"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    assert proc_no_confirm.returncode == 2
    assert "требуется флаг --confirm" in proc_no_confirm.stdout

    # Запуск для несуществующего -> код 1
    proc_missing = subprocess.run(
        [sys.executable, str(script), "--id", "does_not_exist_xyz"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    assert proc_missing.returncode == 1


@pytest.mark.asyncio
async def test_library_mcp_tools():
    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. library_list
        res = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "library_list", "arguments": {"tags": ["ндс"]}}
        })
        assert res.status_code == 200
        content = res.json()["result"]["content"][0]["text"]
        items = json.loads(content)
        assert any(x["id"] == "vat_rate_calc" for x in items)
        
        # 2. library_get
        res2 = await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "library_get", "arguments": {"snippet_id": "vat_rate_calc"}}
        })
        assert res2.status_code == 200
        content2 = res2.json()["result"]["content"][0]["text"]
        assert "vat_rate_calc" in content2
        assert "СтавкаНДС" in content2
