"""
tests/test_wp4_lessons.py - Тестирование логирования событий ошибок и генерации уроков недели (WP4).
"""
import json
import subprocess
import sys
import time
from pathlib import Path
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import edt_guard
from tools.weekly_lessons import generate_report, suggest_rule

LESSONS_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "weekly_lessons.py"


def test_suggest_rule_patterns():
    # modify_metadata с пустым properties
    r1 = suggest_rule("modify_metadata", "modify_metadata: properties пуст", "prevalidate")
    assert "НИКОГДА не вызывай modify_metadata с пустым properties" in r1

    # get_tool_guide
    r2 = suggest_rule("get_tool_guide", "get_tool_guide", "")
    assert "mode='summary'" in r2

    # loop block
    r3 = suggest_rule("some_tool", "error", "loop_block")
    assert "запрещены повторные вызовы" in r3


def test_generate_report_from_events(tmp_path):
    events_file = tmp_path / "test_events.jsonl"
    now = int(time.time())
    
    # Записываем 3 одинаковые ошибки modify_metadata и 1 единичную ошибку
    events = [
        {"ts": now, "profile": "vpf", "tool": "modify_metadata", "signature": "modify_metadata: properties empty", "note": "prevalidate", "order_id": "ord1"},
        {"ts": now, "profile": "vpf", "tool": "modify_metadata", "signature": "modify_metadata: properties empty", "note": "prevalidate", "order_id": "ord2"},
        {"ts": now, "profile": "report", "tool": "modify_metadata", "signature": "modify_metadata: properties empty", "note": "prevalidate", "order_id": "ord3"},
        {"ts": now, "profile": "vpf", "tool": "list_projects", "signature": "list_projects: timeout", "note": "upstream", "order_id": "ord1"},
    ]
    with events_file.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    report = generate_report(events_file, min_count=2, days=7)
    
    assert "modify_metadata (3 раз)" in report
    assert "properties empty" in report
    assert "НИКОГДА не вызывай modify_metadata" in report
    assert "list_projects" not in report  # Единичная ошибка (< 2 раз) не должна попадать в рекомендации


def test_weekly_lessons_cli(tmp_path):
    events_file = tmp_path / "events.jsonl"
    out_file = tmp_path / "lessons.md"
    now = int(time.time())

    with events_file.open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": now, "profile": "vpf", "tool": "modify_metadata",
            "signature": "modify_metadata: properties empty", "note": "prevalidate", "order_id": "test"
        }) + "\n")
        f.write(json.dumps({
            "ts": now, "profile": "vpf", "tool": "modify_metadata",
            "signature": "modify_metadata: properties empty", "note": "prevalidate", "order_id": "test"
        }) + "\n")

    proc = subprocess.run(
        [sys.executable, str(LESSONS_SCRIPT), "--events", str(events_file), "--output", str(out_file), "--min-count", "2"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    assert proc.returncode == 0
    assert out_file.is_file()
    content = out_file.read_text(encoding="utf-8")
    assert "Уроки недели" in content
    assert "modify_metadata" in content


@pytest.mark.asyncio
async def test_live_event_recording(tmp_path, monkeypatch):
    test_events = tmp_path / "live_events.jsonl"
    monkeypatch.setattr(edt_guard, "EVENTS_PATH", test_events)

    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Вызов с ошибкой предвалидации
        await client.post("/mcp/vpf", json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "modify_metadata", "arguments": {"projectName": "П", "properties": []}}
        })

    assert test_events.is_file()
    lines = test_events.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    last_event = json.loads(lines[-1])
    assert last_event["tool"] == "modify_metadata"
    assert "properties" in last_event["signature"]
    assert last_event["note"] == "prevalidate"
