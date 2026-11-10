from __future__ import annotations

import datetime
import json
from pathlib import Path
import time

import httpx
import pytest

from cockpit.services.kwork_client import (
    KworkAuthRequiredError,
    KworkClient,
    parse_dialogs_payload,
    parse_orders_payload,
)
from cockpit.services.kwork_sync import KworkSyncService, categorize_extension
from cockpit.services.orders_facade import OrdersFacade
from cockpit.ui.theme import (
    DANGER_BG,
    DANGER_FG,
    SUCCESS_BG,
    SUCCESS_FG,
    WARNING_BG,
    WARNING_FG,
    format_deadline_countdown,
)
from cockpit.ui.view_orders import _sort_key_by_urgency


def test_categorize_extension_all_buckets() -> None:
    for ext in (".epf", ".erf", ".cfe", ".cf", ".dt", ".bsl", ".mxl"):
        assert categorize_extension(f"sample{ext}") == "1c_files"
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        assert categorize_extension(f"screen{ext}") == "screenshots"
    for ext in (".xlsx", ".xls", ".docx", ".pdf", ".txt", ".zip", ".rar"):
        assert categorize_extension(f"spec{ext}") == "documents"


def test_deadline_countdown_tiers_and_urgency_sort() -> None:
    now = 1_750_000_000
    txt_over, fg_over, bg_over, rem_over = format_deadline_countdown(now - 3600, now_ts=now)
    assert fg_over == DANGER_FG and bg_over == DANGER_BG and rem_over < 0
    assert "Просрочен" in txt_over

    txt_urg, fg_urg, bg_urg, _ = format_deadline_countdown(now + 2 * 3600, now_ts=now)
    assert fg_urg == DANGER_FG and bg_urg == DANGER_BG
    assert "Осталось" in txt_urg

    txt_warn, fg_warn, bg_warn, _ = format_deadline_countdown(now + 12 * 3600, now_ts=now)
    assert fg_warn == WARNING_FG and bg_warn == WARNING_BG

    txt_ok, fg_ok, bg_ok, _ = format_deadline_countdown(now + 48 * 3600, now_ts=now)
    assert fg_ok == SUCCESS_FG and bg_ok == SUCCESS_BG

    class StubOrder:
        def __init__(self, oid: str, stage: str, dl_ts: int) -> None:
            self.order_id = oid
            self.stage = stage
            self.deadline_timestamp = dl_ts
            self.deadline = ""

    items = [
        StubOrder("far", "in_progress", int(time.time()) + 5 * 86400),
        StubOrder("done_item", "delivered", int(time.time()) + 100),
        StubOrder("urgent", "in_progress", int(time.time()) + 3600),
    ]
    items.sort(key=_sort_key_by_urgency)
    assert [i.order_id for i in items] == ["urgent", "far", "done_item"]


def test_kwork_client_cookie_parsing_and_captcha_handling(tmp_path: Path) -> None:
    def captcha_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/user/signin":
            return httpx.Response(
                200,
                text='<div class="g-recaptcha" data-sitekey="abc">Captcha required</div>',
                headers={"content-type": "text/html"},
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(captcha_handler)
    client = KworkClient(base_dir=tmp_path, transport=transport)

    parsed_hdr = KworkClient.parse_cookie_input("PHPSESSID=xyz123; kwork_uid=42")
    assert parsed_hdr == {"PHPSESSID": "xyz123", "kwork_uid": "42"}

    parsed_json = KworkClient.parse_cookie_input('[{"name": "sid", "value": "tok_99"}]')
    assert parsed_json == {"sid": "tok_99"}

    client.set_session_credentials(cookie_text="PHPSESSID=xyz123", token="bearer_1", username="dev1c")
    client2 = KworkClient(base_dir=tmp_path)
    assert client2.cookies == {"PHPSESSID": "xyz123"}
    assert client2.token == "bearer_1"
    assert client2.username == "dev1c"

    with pytest.raises(KworkAuthRequiredError) as exc_info:
        client.login_with_credentials("dev1c", "secret")
    assert exc_info.value.reason == "captcha_required"


def test_kwork_sync_orders_attachments_dedup_and_7day_chats(tmp_path: Path) -> None:
    now = 1_750_000_000
    orders_dir = tmp_path / "orders"
    orders_dir.mkdir(parents=True)

    sample_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    sample_epf = b"EPF_BINARY_STUB_CONTENT"
    sample_xlsx = b"PK\x03\x04XLSX_SPEC_CONTENT"

    stale_dir = orders_dir / "_inbox_cache" / "peer_old"
    stale_dir.mkdir(parents=True)
    (stale_dir / "dialog.json").write_text(
        json.dumps({"peer_id": "peer_old", "last_message_at": now - (9 * 86400)}),
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/manage_orders":
            return httpx.Response(
                200,
                json={
                    "response": {
                        "orders": [
                            {
                                "id": "900123",
                                "title": "Внешняя печатная форма счета для 1С:УТ 11.5",
                                "buyer_username": "komplekto_buyer",
                                "price_rub": 5500,
                                "status": "inprogress",
                                "created_at": "2025-06-15",
                                "deadline_timestamp": now + 10 * 3600,
                                "kwork_url": "https://kwork.ru/track?id=900123",
                                "brief_text": "Нужна ВПФ Счет-договор (.epf) под УТ 11.5.",
                            }
                        ]
                    }
                },
            )
        if path == "/track":
            return httpx.Response(
                200,
                json={
                    "response": {
                        "brief_text": "Нужна ВПФ Счет-договор (.epf) под УТ 11.5.",
                        "messages": [
                            {
                                "id": "m1",
                                "sender": "komplekto_buyer",
                                "is_client": True,
                                "text": "Вот макет на картинке и текущая форма",
                                "timestamp": now - 1200,
                                "attachments": [
                                    {"url": "/files/maket_screen.png", "filename": "maket_screen.png"},
                                    {"url": "/files/old_form.epf", "filename": "old_form.epf"},
                                ],
                            },
                            {
                                "id": "m2",
                                "sender": "komplekto_buyer",
                                "is_client": True,
                                "text": "Дубликат картинки и спецификация",
                                "timestamp": now - 600,
                                "attachments": [
                                    {"url": "/files/maket_screen_copy.png", "filename": "maket_screen_copy.png"},
                                    {"url": "/files/spec_tz.xlsx", "filename": "spec_tz.xlsx"},
                                ],
                            },
                        ],
                    }
                },
            )
        if path == "/inbox":
            return httpx.Response(
                200,
                json={
                    "response": {
                        "dialogs": [
                            {
                                "peer_id": "501",
                                "peer_name": "fresh_client",
                                "last_message_text": "Нужен отчет по продажам в БП 3.0",
                                "last_message_at": now - (2 * 86400),
                                "unread_count": 2,
                                "has_attachments": True,
                            },
                            {
                                "peer_id": "502",
                                "peer_name": "expired_client",
                                "last_message_text": "Старое сообщение",
                                "last_message_at": now - (8 * 86400),
                                "unread_count": 1,
                            },
                        ]
                    }
                },
            )
        if path == "/inbox/501":
            return httpx.Response(
                200,
                json={
                    "response": {
                        "messages": [
                            {
                                "id": "pm1",
                                "sender": "fresh_client",
                                "is_client": True,
                                "text": "Нужен отчет по продажам для Бухгалтерии 3.0",
                                "timestamp": now - (2 * 86400),
                                "attachments": [
                                    {"url": "/files/chat_ref.png", "filename": "chat_ref.png"},
                                ],
                            }
                        ]
                    }
                },
            )
        if path in ("/files/maket_screen.png", "/files/maket_screen_copy.png", "/files/chat_ref.png"):
            return httpx.Response(200, content=sample_png)
        if path == "/files/old_form.epf":
            return httpx.Response(200, content=sample_epf)
        if path == "/files/spec_tz.xlsx":
            return httpx.Response(200, content=sample_xlsx)
        return httpx.Response(404)

    client = KworkClient(base_dir=tmp_path, transport=httpx.MockTransport(handler))
    client.set_session_credentials(cookie_text="PHPSESSID=test_sess", username="timur")
    svc = KworkSyncService(base_dir=tmp_path, orders_dir=orders_dir, client=client)

    res1 = svc.sync_all(now_ts=now)
    assert res1.ok is True
    assert res1.synced_orders_count == 1
    assert res1.synced_dialogs_count == 1
    assert res1.new_attachments_count == 4

    ord_dir = orders_dir / "kwork_900123"
    assert (ord_dir / "order.json").is_file()
    assert (ord_dir / "brief.md").is_file()
    assert (ord_dir / "chat_history.json").is_file()
    assert (ord_dir / "attachments" / "screenshots" / "maket_screen.png").is_file()
    assert not (ord_dir / "attachments" / "screenshots" / "maket_screen_copy.png").exists()
    assert (ord_dir / "attachments" / "1c_files" / "old_form.epf").is_file()
    assert (ord_dir / "attachments" / "documents" / "spec_tz.xlsx").is_file()

    manifest = json.loads((ord_dir / "attachments_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 3
    screen_entry = [f for f in manifest["files"] if f["filename"] == "maket_screen.png"][0]
    assert "картинке" in screen_entry["message_text"]

    res2 = svc.sync_all(now_ts=now + 60)
    assert res2.ok is True
    assert res2.new_attachments_count == 0

    assert not stale_dir.exists()
    dialogs = svc.list_recent_dialogs(now_ts=now)
    assert [d["peer_id"] for d in dialogs] == ["501"]

    dialogs_after_cutoff = svc.list_recent_dialogs(now_ts=now + (8 * 86400))
    assert dialogs_after_cutoff == []

    facade = OrdersFacade(orders_dir=orders_dir, root_dir=tmp_path)
    created_ord, copied_cnt = svc.create_order_workspace_from_dialog("501", orders_facade=facade)
    assert copied_cnt == 1
    assert (created_ord.order_dir / "attachments" / "screenshots" / "chat_ref.png").is_file()
    assert "Бухгалтерии 3.0" in (created_ord.order_dir / "brief.md").read_text(encoding="utf-8")


def test_parse_orders_and_dialogs_html_fallback() -> None:
    html_orders = """
    <div class="order-item" data-order-id="777">
      <a href="/track?id=777" class="order-title">Доработка отчета УТ 11</a>
      <span class="buyer-name" data-buyer="ivan_1c">ivan_1c</span>
      <span class="order-price">4500 ₽</span>
      <span class="order-status">В работе</span>
      <span class="deadline" data-deadline="1750010000">2 дня</span>
      <div class="order-description">Описание <img src="https://kwork.ru/files/shot1.png" /></div>
    </div>
    """
    orders = parse_orders_payload(html_orders)
    assert len(orders) == 1
    assert orders[0].order_id == "777"
    assert orders[0].buyer_username == "ivan_1c"
    assert orders[0].price_rub == 4500.0
    assert orders[0].attachments[0].filename == "shot1.png"

    html_dialogs = """
    <div class="dialog-item" data-peer-id="333">
      <span class="peer-name">olga_buh</span>
      <span class="last-message">Здравствуйте, посмотрите файл</span>
      <span class="time" data-timestamp="1750005000">12:00</span>
      <span class="unread-count">3</span>
      <span class="has-attachment"></span>
    </div>
    """
    dialogs = parse_dialogs_payload(html_dialogs)
    assert len(dialogs) == 1
    assert dialogs[0].peer_id == "333"
    assert dialogs[0].unread_count == 3
    assert dialogs[0].has_attachments is True
