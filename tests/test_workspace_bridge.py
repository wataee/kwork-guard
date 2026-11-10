from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import edt_guard
from cockpit.app import CockpitApp
from cockpit.services.orders_facade import OrdersFacade
from cockpit.services.workspace_bridge import (
    launch_antigravity_workspace,
    prepare_antigravity_workspace,
)
from guard.fs_utils import atomic_write_json
from guard.macros import macro_get_order_context
from guard.state import OrderManager


def _make_sample_order_with_attachments(tmp_path: Path) -> tuple[Path, Path]:
    repo_root = Path(__file__).resolve().parent.parent
    shutil.copytree(repo_root / ".roo", tmp_path / ".roo")
    shutil.copytree(repo_root / "templates", tmp_path / "templates")
    shutil.copytree(repo_root / "mcp_configs", tmp_path / "mcp_configs")
    shutil.copytree(repo_root / "gold_templates", tmp_path / "gold_templates")

    orders_dir = tmp_path / "orders"
    facade = OrdersFacade(orders_dir=orders_dir, root_dir=tmp_path)
    order = facade.create_order(
        order_slug="order_ctx_test",
        client="buyer_ctx",
        title="Счет-договор для УТ 11.5",
        kind="vpf",
        price=4000.0,
        configuration="1С:УТ 11.5",
        deadline="2026-11-25 18:00",
        notes="Добавить логотип и таблицу доставки",
    )
    odir = order.order_dir
    (odir / "attachments" / "screenshots").mkdir(parents=True, exist_ok=True)
    (odir / "attachments" / "1c_files").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color=(99, 102, 241)).save(odir / "attachments" / "screenshots" / "layout.png")
    (odir / "attachments" / "1c_files" / "sample.epf").write_bytes(b"EPF")

    atomic_write_json(
        odir / "chat_history.json",
        {
            "order_id": order.order_id,
            "messages": [
                {
                    "message_id": "1",
                    "sender": "buyer_ctx",
                    "is_client": True,
                    "text": "Вот скриншот требуемой формы",
                    "timestamp": 1_750_000_000,
                    "attachments": [{"filename": "layout.png"}],
                }
            ],
        },
    )
    atomic_write_json(
        odir / "attachments_manifest.json",
        {
            "order_id": order.order_id,
            "files": [
                {
                    "file_id": "abc1",
                    "filename": "layout.png",
                    "category": "screenshots",
                    "relative_path": "attachments/screenshots/layout.png",
                    "message_text": "Вот скриншот требуемой формы",
                    "timestamp": 1_750_000_000,
                }
            ],
        },
    )
    return odir, tmp_path


def test_prepare_and_launch_antigravity_workspace(tmp_path: Path, monkeypatch) -> None:
    odir, root = _make_sample_order_with_attachments(tmp_path)
    monkeypatch.setenv("EDT_MCP_URL", "http://127.0.0.1:8765/mcp")

    prep = prepare_antigravity_workspace(odir, base_dir=root, prepare_global_mcp=True)
    assert prep["ok"] is True

    ctx_file = odir / "ORDER_CONTEXT.md"
    assert ctx_file.is_file()
    ctx_txt = ctx_file.read_text(encoding="utf-8")
    assert "attachments/screenshots/layout.png" in ctx_txt
    assert "attachments/1c_files/sample.epf" in ctx_txt
    assert "Вот скриншот требуемой формы" in ctx_txt
    assert "get_order_context" in ctx_txt

    roo_mcp = json.loads((odir / ".roo" / "mcp.json").read_text(encoding="utf-8"))
    edt_srv = roo_mcp["mcpServers"]["edt"]
    assert edt_srv["env"]["KWORK_ORDER_DIR"] == str(odir.resolve())
    assert edt_srv["env"]["KWORK_TASK_TYPE"] == "vpf"
    assert edt_srv["env"]["EDT_MCP_URL"] == "http://127.0.0.1:8765/mcp"

    assert (odir / ".roo" / "rules" / "rules.md").is_file()
    assert (odir / "AGENTS.md").is_file()
    assert "BSL" in (odir / "AGENTS.md").read_text(encoding="utf-8")

    with patch("cockpit.services.workspace_bridge.find_antigravity_executable", return_value=Path("C:/Antigravity.exe")), \
         patch("subprocess.Popen") as mock_popen:
        res = launch_antigravity_workspace(odir, base_dir=root)
        assert res["ok"] is True
        assert res["ide_used"] == "Antigravity"
        mock_popen.assert_called_once()


def test_hybrid_guard_get_order_context_and_guard_metrics(tmp_path: Path, monkeypatch) -> None:
    odir, root = _make_sample_order_with_attachments(tmp_path)
    prepare_antigravity_workspace(odir, base_dir=root, prepare_global_mcp=False)

    monkeypatch.setenv("KWORK_ORDER_DIR", str(odir))
    res = macro_get_order_context({})
    assert res["isError"] is False
    txt = res["content"][0]["text"]
    assert "attachments/screenshots/layout.png" in txt
    assert "attachments/1c_files/sample.epf" in txt

    isolated_mgr = OrderManager(state_dir=tmp_path / "guard_state")
    monkeypatch.setattr(edt_guard, "ORDER_MGR", isolated_mgr)

    client = TestClient(edt_guard.app)
    resp_ws = client.post(
        "/admin/active-workspace",
        json={"order_id": "order_ctx_test", "order_dir": str(odir), "task_type": "vpf"},
    )
    assert resp_ws.status_code == 200

    resp_ctx = client.post(
        "/mcp/vpf/order_ctx_test",
        json={
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "get_order_context", "arguments": {}},
        },
        headers={"X-Order-Dir": str(odir)},
    )
    assert resp_ctx.status_code == 200
    body_ctx = resp_ctx.json()
    assert "order_ctx_test" in body_ctx["result"]["content"][0]["text"]

    resp_lint_block = client.post(
        "/mcp/vpf/order_ctx_test",
        json={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {
                "name": "write_module_source",
                "arguments": {
                    "projectName": "Proj",
                    "modulePath": "ExternalDataProcessors/X/ObjectModule.bsl",
                    "source": "Если Истина Тогда\nСообщить(1);",
                },
            },
        },
        headers={"X-Order-Dir": str(odir)},
    )
    assert resp_lint_block.status_code == 200
    assert resp_lint_block.json()["result"]["isError"] is True

    resp_compact = client.post(
        "/mcp/vpf/order_ctx_test",
        json={
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {
                "name": "compact_schema",
                "arguments": {"metadata": {"name": "ЗаказКлиента", "attributes": [{"name": "Номер", "type": "String"}]}},
            },
        },
        headers={"X-Order-Dir": str(odir)},
    )
    assert resp_compact.status_code == 200

    metrics_file = odir / "guard_metrics.json"
    assert metrics_file.is_file()
    metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert metrics["linter_blocks"] >= 1
    assert metrics["schemas_compacted"] >= 1
    assert metrics["calls_count"] >= 2


def test_chats_and_order_detail_screenshots_ui(tmp_path: Path) -> None:
    orders_dir = tmp_path / "orders"
    orders_dir.mkdir(parents=True)
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "logs").mkdir(parents=True)

    cache_peer = orders_dir / "_inbox_cache" / "900"
    (cache_peer / "attachments" / "screenshots").mkdir(parents=True)
    Image.new("RGB", (40, 40), color=(52, 211, 153)).save(cache_peer / "attachments" / "screenshots" / "preview.png")
    now = int(time.time())
    atomic_write_json(
        cache_peer / "dialog.json",
        {
            "peer_id": "900",
            "peer_name": "ui_chat_buyer",
            "last_message_text": "Нужен счет-договор для УТ 11",
            "last_message_at": now - 300,
            "unread_count": 1,
            "has_attachments": True,
            "kwork_url": "https://kwork.ru/inbox/900",
            "messages": [
                {
                    "message_id": "m1",
                    "sender": "ui_chat_buyer",
                    "is_client": True,
                    "text": "Нужен счет-договор для УТ 11 за 4500 руб",
                    "timestamp": now - 300,
                    "attachments": [{"filename": "preview.png"}],
                }
            ],
        },
    )
    atomic_write_json(
        cache_peer / "attachments_manifest.json",
        {
            "order_id": "900",
            "files": [
                {
                    "file_id": "img1",
                    "filename": "preview.png",
                    "category": "screenshots",
                    "relative_path": "attachments/screenshots/preview.png",
                    "message_text": "Нужен счет-договор",
                    "timestamp": now - 300,
                }
            ],
        },
    )
    atomic_write_json(
        orders_dir / "_inbox_cache" / "index.json",
        {
            "updated_at": now,
            "cutoff_ts": now - 7 * 86400,
            "dialogs": [
                {
                    "peer_id": "900",
                    "peer_name": "ui_chat_buyer",
                    "last_message_text": "Нужен счет-договор для УТ 11",
                    "last_message_at": now - 300,
                    "unread_count": 1,
                    "has_attachments": True,
                }
            ],
        },
    )

    app = CockpitApp(base_dir=tmp_path)
    try:
        app.show_view("chats")
        app.update_idletasks()
        chats_view = app.views["chats"]
        chats_view.select_dialog("900")
        app.update_idletasks()
        assert chats_view._selected_peer_id == "900"

        chats_view.on_create_workspace_from_chat()
        app.wait_for_background_tasks()
        assert app.current_view_name == "order_detail"
        detail_view = app.views["order_detail"]
        assert detail_view.current_order_id is not None
        assert "1" in detail_view.gallery_title.cget("text")
    finally:
        app.destroy()
