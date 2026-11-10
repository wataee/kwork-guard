from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import time
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
import httpx

from guard.config import load_config
from guard.fs_utils import atomic_write_json

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("cockpit.kwork_client")
if not logger.handlers:
    _handler = RotatingFileHandler(
        LOGS_DIR / "cockpit.log",
        maxBytes=2_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

ATTACHMENT_EXTS = {
    ".epf", ".erf", ".cfe", ".cf", ".dt", ".bsl", ".mxl",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".xlsx", ".xls", ".docx", ".doc", ".pdf", ".txt", ".zip", ".rar", ".7z", ".csv",
}

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
IGNORED_IMAGE_TOKENS = ("avatar", "emoji", "smiley", "logo", "favicon", "badge", "sprite", "svg")


class KworkAuthRequiredError(RuntimeError):
    def __init__(self, reason: str, message: str = "") -> None:
        self.reason = reason
        self.message = message or reason
        super().__init__(self.message)


@dataclass
class KworkAttachment:
    url: str
    filename: str
    extension: str
    size_bytes: int = 0
    message_id: str = ""
    message_text: str = ""
    timestamp: int = 0


@dataclass
class KworkMessage:
    message_id: str
    sender: str
    is_client: bool
    text: str
    timestamp: int
    attachments: list[KworkAttachment] = field(default_factory=list)


@dataclass
class KworkOrderRecord:
    order_id: str
    title: str
    buyer_username: str
    price_rub: float
    status: str
    created_at: str
    deadline_timestamp: int
    brief_text: str
    kwork_url: str = ""
    messages: list[KworkMessage] = field(default_factory=list)
    attachments: list[KworkAttachment] = field(default_factory=list)


@dataclass
class KworkDialogSummary:
    peer_id: str
    peer_name: str
    last_message_text: str
    last_message_at: int
    unread_count: int = 0
    has_attachments: bool = False
    kwork_url: str = ""
    messages: list[KworkMessage] = field(default_factory=list)
    attachments: list[KworkAttachment] = field(default_factory=list)


def _detect_auth_challenge(text: str) -> Optional[str]:
    low = text.lower()
    if any(tok in low for tok in ("g-recaptcha", "smart-captcha", "h-captcha", "cf-turnstile", "captcha_required", "captcha")):
        return "captcha_required"
    if any(tok in low for tok in ("two_factor", "two-factor", "2fa", "sms_code", "totp", "google authenticator")):
        return "2fa_required"
    return None


def _normalize_status(raw_status: Any) -> str:
    text = str(raw_status or "").strip().lower()
    if any(tok in text for tok in ("review", "провер", "check", "await")):
        return "review"
    if any(tok in text for tok in ("deliver", "done", "выполн", "заверш", "complete", "paid")):
        return "delivered"
    if any(tok in text for tok in ("cancel", "отмен", "arbitr")):
        return "cancelled"
    if any(tok in text for tok in ("warrant", "гарант", "rework", "доработ")):
        return "warranty"
    if any(tok in text for tok in ("new", "lead", "нов", "очеред", "queue", "ожида")):
        return "lead"
    return "in_progress"


def _parse_float(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.,]", "", str(value or "").replace(" ", ""))
    cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def _parse_timestamp(value: Any, fallback: int = 0) -> int:
    if value is None or value == "":
        return fallback
    if isinstance(value, (int, float)):
        iv = int(value)
        return iv if iv > 0 else fallback
    raw = str(value).strip()
    if raw.isdigit():
        return int(raw)
    try:
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(normalized)
        return int(dt.timestamp())
    except ValueError:
        logger.debug("ISO timestamp parse skipped for: %s", raw)

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(raw, fmt)
            return int(dt.timestamp())
        except ValueError:
            continue

    now = int(time.time())
    rel_sec = 0
    matched_rel = False
    for num_str, unit in re.findall(r"(\d+)\s*(д|дн|ч|час|м|мин)", raw.lower()):
        n = int(num_str)
        matched_rel = True
        if unit.startswith("д"):
            rel_sec += n * 86400
        elif unit.startswith("ч"):
            rel_sec += n * 3600
        elif unit.startswith("м"):
            rel_sec += n * 60
    if matched_rel:
        if any(tok in raw.lower() for tok in ("назад", "ago")):
            return max(0, now - rel_sec)
        return now + rel_sec

    return fallback


def _safe_filename(url: str, hint_name: str = "") -> str:
    candidate = hint_name.strip() if hint_name else ""
    if not candidate:
        parsed = urlparse(url)
        candidate = Path(parsed.path).name or "attachment.bin"
    candidate = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", candidate).strip(" ._")
    if not candidate:
        candidate = "attachment.bin"
    if "." not in candidate:
        ext = Path(urlparse(url).path).suffix
        candidate = f"{candidate}{ext or '.bin'}"
    return candidate


def _attachment_from_dict(
    item: Any,
    base_url: str,
    message_id: str = "",
    message_text: str = "",
    timestamp: int = 0,
) -> Optional[KworkAttachment]:
    if isinstance(item, str):
        full_url = urljoin(base_url, item.strip())
        if not full_url:
            return None
        fname = _safe_filename(full_url)
        return KworkAttachment(
            url=full_url,
            filename=fname,
            extension=Path(fname).suffix.lower(),
            message_id=message_id,
            message_text=message_text,
            timestamp=timestamp,
        )
    if not isinstance(item, dict):
        return None
    raw_url = str(item.get("url") or item.get("href") or item.get("src") or item.get("download_url") or "").strip()
    if not raw_url:
        return None
    full_url = urljoin(base_url, raw_url)
    hint = str(item.get("filename") or item.get("name") or item.get("title") or "")
    fname = _safe_filename(full_url, hint)
    size_val = int(item.get("size") or item.get("size_bytes") or 0)
    return KworkAttachment(
        url=full_url,
        filename=fname,
        extension=Path(fname).suffix.lower(),
        size_bytes=size_val,
        message_id=str(item.get("message_id") or message_id),
        message_text=str(item.get("message_text") or message_text),
        timestamp=_parse_timestamp(item.get("timestamp"), timestamp),
    )


def extract_attachments_from_html(
    soup: Any,
    base_url: str,
    message_id: str = "",
    message_text: str = "",
    timestamp: int = 0,
) -> list[KworkAttachment]:
    attachments: list[KworkAttachment] = []
    seen_urls: set[str] = set()

    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "").strip()
        if not href or href.startswith(("#", "javascript:")):
            continue
        full_url = urljoin(base_url, href)
        path_lower = urlparse(full_url).path.lower()
        ext = Path(path_lower).suffix
        hint = str(link.get("download") or link.get_text(" ", strip=True) or "")
        hint_ext = Path(hint).suffix.lower()
        is_file_link = ext in ATTACHMENT_EXTS or hint_ext in ATTACHMENT_EXTS or "/files/" in path_lower or "/download" in path_lower
        if not is_file_link or full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        fname = _safe_filename(full_url, hint if "." in hint else "")
        attachments.append(
            KworkAttachment(
                url=full_url,
                filename=fname,
                extension=Path(fname).suffix.lower(),
                message_id=message_id,
                message_text=message_text,
                timestamp=timestamp,
            )
        )

    for img in soup.find_all("img", src=True):
        src = str(img.get("data-src") or img.get("src") or "").strip()
        if not src or src.startswith("data:"):
            continue
        low_src = src.lower()
        cls_txt = " ".join(img.get("class") or []).lower()
        if any(tok in low_src or tok in cls_txt for tok in IGNORED_IMAGE_TOKENS):
            continue
        full_url = urljoin(base_url, src)
        ext = Path(urlparse(full_url).path.lower()).suffix
        if ext and ext not in IMAGE_EXTS:
            continue
        if full_url in seen_urls:
            continue
        seen_urls.add(full_url)
        fname = _safe_filename(full_url, str(img.get("alt") or ""))
        if Path(fname).suffix.lower() not in IMAGE_EXTS:
            fname = f"{Path(fname).stem}.png"
        attachments.append(
            KworkAttachment(
                url=full_url,
                filename=fname,
                extension=Path(fname).suffix.lower(),
                message_id=message_id,
                message_text=message_text,
                timestamp=timestamp,
            )
        )

    return attachments


def _extract_embedded_json_blocks(html_text: str) -> list[Any]:
    blocks: list[Any] = []
    soup = BeautifulSoup(html_text, "html.parser")
    for script in soup.find_all("script"):
        txt = script.string or script.get_text() or ""
        if not txt.strip():
            continue
        if script.get("type") == "application/json":
            try:
                blocks.append(json.loads(txt))
                continue
            except json.JSONDecodeError:
                logger.debug("Invalid JSON script tag ignored")
        for match in re.finditer(r"window\.(?:stateData|initialState|kworkData)\s*=\s*(\{.*?\});", txt, flags=re.S):
            try:
                blocks.append(json.loads(match.group(1)))
            except json.JSONDecodeError:
                continue
    return blocks


def _parse_messages_list(raw_msgs: Any, base_url: str) -> list[KworkMessage]:
    result: list[KworkMessage] = []
    if not isinstance(raw_msgs, list):
        return result
    for idx, msg in enumerate(raw_msgs):
        if not isinstance(msg, dict):
            continue
        mid = str(msg.get("id") or msg.get("message_id") or f"msg_{idx + 1}")
        sender = str(msg.get("sender") or msg.get("username") or msg.get("author") or "Клиент")
        text = str(msg.get("text") or msg.get("message") or msg.get("body") or "").strip()
        ts = _parse_timestamp(msg.get("timestamp") or msg.get("time") or msg.get("created_at") or msg.get("date"))
        is_client = bool(msg.get("is_client", not msg.get("is_me", msg.get("from_me", False))))
        atts: list[KworkAttachment] = []
        for att_raw in msg.get("attachments") or msg.get("files") or []:
            att = _attachment_from_dict(att_raw, base_url, mid, text, ts)
            if att is not None:
                atts.append(att)
        if "<" in text and ">" in text:
            frag = BeautifulSoup(text, "html.parser")
            plain = frag.get_text("\n", strip=True)
            for html_att in extract_attachments_from_html(frag, base_url, mid, plain, ts):
                if not any(existing.url == html_att.url for existing in atts):
                    atts.append(html_att)
            text = plain
        result.append(KworkMessage(message_id=mid, sender=sender, is_client=is_client, text=text, timestamp=ts, attachments=atts))
    result.sort(key=lambda item: item.timestamp)
    return result


def parse_orders_payload(raw_data: Any, base_url: str = "https://kwork.ru") -> list[KworkOrderRecord]:
    if isinstance(raw_data, bytes):
        raw_data = raw_data.decode("utf-8", errors="replace")

    if isinstance(raw_data, str):
        stripped = raw_data.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                raw_data = json.loads(stripped)
            except json.JSONDecodeError:
                raw_data = stripped

    if isinstance(raw_data, (dict, list)):
        items = raw_data
        if isinstance(raw_data, dict):
            resp = raw_data.get("response", raw_data.get("data", raw_data))
            if isinstance(resp, dict):
                items = resp.get("orders") or resp.get("items") or resp.get("list") or []
            elif isinstance(resp, list):
                items = resp
            else:
                items = []
        records: list[KworkOrderRecord] = []
        for row in items if isinstance(items, list) else []:
            if not isinstance(row, dict):
                continue
            oid = str(row.get("order_id") or row.get("id") or "").strip()
            if not oid:
                continue
            title = str(row.get("title") or row.get("name") or f"Заказ {oid}").strip()
            buyer = str(row.get("buyer_username") or row.get("buyer") or row.get("client_name") or row.get("username") or "Покупатель").strip()
            price = _parse_float(row.get("price_rub") or row.get("price") or row.get("amount"))
            status = _normalize_status(row.get("status") or row.get("stage"))
            created_raw = str(row.get("created_at") or datetime.date.today().isoformat())
            deadline_ts = _parse_timestamp(row.get("deadline_timestamp") or row.get("deadline_ts") or row.get("deadline") or row.get("time_left"))
            brief_text = str(row.get("brief_text") or row.get("description") or row.get("tz") or "").strip()
            kwork_url = str(row.get("kwork_url") or row.get("url") or urljoin(base_url, f"/track?id={oid}"))
            msgs = _parse_messages_list(row.get("messages") or [], base_url)
            atts: list[KworkAttachment] = []
            for att_raw in row.get("attachments") or row.get("files") or []:
                att = _attachment_from_dict(att_raw, base_url, message_id="brief", message_text=brief_text, timestamp=_parse_timestamp(created_raw))
                if att is not None:
                    atts.append(att)
            for m in msgs:
                for ma in m.attachments:
                    if not any(existing.url == ma.url and existing.message_id == ma.message_id for existing in atts):
                        atts.append(ma)
            records.append(
                KworkOrderRecord(
                    order_id=oid,
                    title=title,
                    buyer_username=buyer,
                    price_rub=price,
                    status=status,
                    created_at=created_raw,
                    deadline_timestamp=deadline_ts,
                    brief_text=brief_text,
                    kwork_url=kwork_url,
                    messages=msgs,
                    attachments=atts,
                )
            )
        return records

    html_text = str(raw_data or "")
    for block in _extract_embedded_json_blocks(html_text):
        parsed = parse_orders_payload(block, base_url=base_url)
        if parsed:
            return parsed

    soup = BeautifulSoup(html_text, "html.parser")
    cards = soup.select("[data-order-id], .order-item, .manage-order-item, .js-order-row")
    parsed_cards: list[KworkOrderRecord] = []
    for card in cards:
        oid = str(card.get("data-order-id") or card.get("data-id") or "").strip()
        link = card.find("a", href=re.compile(r"(?:track|order)", re.I))
        if not oid and link:
            match_id = re.search(r"(\d+)", str(link.get("href") or ""))
            if match_id:
                oid = match_id.group(1)
        if not oid:
            continue
        title_el = card.select_one(".order-title, .title, h3, h4, a")
        buyer_el = card.select_one(".buyer-name, .username, .user-name, [data-buyer]")
        price_el = card.select_one(".order-price, .price, .cost")
        status_el = card.select_one(".order-status, .status")
        deadline_el = card.select_one("[data-deadline], .deadline, .time-left")
        brief_el = card.select_one(".order-description, .brief, .tz")
        title = title_el.get_text(" ", strip=True) if title_el else f"Заказ {oid}"
        buyer = str(buyer_el.get("data-buyer") if buyer_el and buyer_el.get("data-buyer") else (buyer_el.get_text(" ", strip=True) if buyer_el else "Покупатель"))
        price = _parse_float(price_el.get_text(" ", strip=True) if price_el else 0)
        status = _normalize_status(status_el.get_text(" ", strip=True) if status_el else "in_progress")
        deadline_raw = deadline_el.get("data-deadline") if deadline_el and deadline_el.get("data-deadline") else (deadline_el.get_text(" ", strip=True) if deadline_el else "")
        deadline_ts = _parse_timestamp(deadline_raw)
        brief_text = brief_el.get_text("\n", strip=True) if brief_el else ""
        url = urljoin(base_url, str(link.get("href"))) if link else urljoin(base_url, f"/track?id={oid}")
        atts = extract_attachments_from_html(card, base_url, message_id="brief", message_text=brief_text)
        parsed_cards.append(
            KworkOrderRecord(
                order_id=oid,
                title=title,
                buyer_username=buyer,
                price_rub=price,
                status=status,
                created_at=datetime.date.today().isoformat(),
                deadline_timestamp=deadline_ts,
                brief_text=brief_text,
                kwork_url=url,
                messages=[],
                attachments=atts,
            )
        )
    return parsed_cards


def parse_order_detail_payload(
    raw_data: Any,
    order_id: str,
    base_url: str = "https://kwork.ru",
) -> tuple[str, list[KworkMessage], list[KworkAttachment], dict[str, Any]]:
    if isinstance(raw_data, bytes):
        raw_data = raw_data.decode("utf-8", errors="replace")
    if isinstance(raw_data, str) and raw_data.strip().startswith("{"):
        try:
            raw_data = json.loads(raw_data)
        except json.JSONDecodeError:
            logger.debug("Detail JSON decode fallback to HTML")

    if isinstance(raw_data, dict):
        data = raw_data.get("response", raw_data.get("data", raw_data))
        if not isinstance(data, dict):
            data = {}
        brief = str(data.get("brief_text") or data.get("description") or data.get("tz") or "").strip()
        msgs = _parse_messages_list(data.get("messages") or [], base_url)
        atts: list[KworkAttachment] = []
        for att_raw in data.get("attachments") or data.get("files") or []:
            att = _attachment_from_dict(att_raw, base_url, message_id="brief", message_text=brief)
            if att is not None:
                atts.append(att)
        for m in msgs:
            for ma in m.attachments:
                if not any(existing.url == ma.url and existing.message_id == ma.message_id for existing in atts):
                    atts.append(ma)
        meta: dict[str, Any] = {}
        if data.get("deadline_timestamp") or data.get("deadline"):
            meta["deadline_timestamp"] = _parse_timestamp(data.get("deadline_timestamp") or data.get("deadline"))
        if data.get("status"):
            meta["status"] = _normalize_status(data.get("status"))
        if data.get("title"):
            meta["title"] = str(data.get("title"))
        if data.get("buyer_username") or data.get("buyer"):
            meta["buyer_username"] = str(data.get("buyer_username") or data.get("buyer"))
        if data.get("price_rub") or data.get("price"):
            meta["price_rub"] = _parse_float(data.get("price_rub") or data.get("price"))
        return brief, msgs, atts, meta

    html_text = str(raw_data or "")
    for block in _extract_embedded_json_blocks(html_text):
        if isinstance(block, dict):
            brief, msgs, atts, meta = parse_order_detail_payload(block, order_id, base_url=base_url)
            if brief or msgs or atts:
                return brief, msgs, atts, meta

    soup = BeautifulSoup(html_text, "html.parser")
    brief_el = soup.select_one(".order-description, .tz-text, .brief-content, [data-tz]")
    brief = brief_el.get_text("\n", strip=True) if brief_el else ""
    msgs: list[KworkMessage] = []
    atts: list[KworkAttachment] = []

    if brief_el:
        for att in extract_attachments_from_html(brief_el, base_url, message_id="brief", message_text=brief):
            atts.append(att)

    msg_nodes = soup.select(".chat-message, .message-item, [data-message-id]")
    for idx, node in enumerate(msg_nodes):
        mid = str(node.get("data-message-id") or f"msg_{idx + 1}")
        sender_el = node.select_one(".sender, .username, .author")
        text_el = node.select_one(".message-text, .text, .body")
        ts_raw = node.get("data-timestamp") or (node.select_one(".time, .date").get_text(" ", strip=True) if node.select_one(".time, .date") else "")
        cls_txt = " ".join(node.get("class") or []).lower()
        is_client = "mine" not in cls_txt and "outgoing" not in cls_txt and "freelancer" not in cls_txt
        sender = sender_el.get_text(" ", strip=True) if sender_el else ("Клиент" if is_client else "Исполнитель")
        txt = text_el.get_text("\n", strip=True) if text_el else node.get_text("\n", strip=True)
        ts = _parse_timestamp(ts_raw)
        msg_atts = extract_attachments_from_html(node, base_url, message_id=mid, message_text=txt, timestamp=ts)
        msgs.append(KworkMessage(message_id=mid, sender=sender, is_client=is_client, text=txt, timestamp=ts, attachments=msg_atts))
        for ma in msg_atts:
            if not any(existing.url == ma.url and existing.message_id == ma.message_id for existing in atts):
                atts.append(ma)

    msgs.sort(key=lambda item: item.timestamp)
    return brief, msgs, atts, {}


def parse_dialogs_payload(raw_data: Any, base_url: str = "https://kwork.ru") -> list[KworkDialogSummary]:
    if isinstance(raw_data, bytes):
        raw_data = raw_data.decode("utf-8", errors="replace")
    if isinstance(raw_data, str) and (raw_data.strip().startswith("{") or raw_data.strip().startswith("[")):
        try:
            raw_data = json.loads(raw_data)
        except json.JSONDecodeError:
            logger.debug("Dialog JSON fallback to HTML")

    if isinstance(raw_data, (dict, list)):
        items = raw_data
        if isinstance(raw_data, dict):
            resp = raw_data.get("response", raw_data.get("data", raw_data))
            if isinstance(resp, dict):
                items = resp.get("dialogs") or resp.get("items") or resp.get("chats") or []
            elif isinstance(resp, list):
                items = resp
            else:
                items = []
        dialogs: list[KworkDialogSummary] = []
        for row in items if isinstance(items, list) else []:
            if not isinstance(row, dict):
                continue
            peer_id = str(row.get("peer_id") or row.get("user_id") or row.get("id") or "").strip()
            if not peer_id:
                continue
            peer_name = str(row.get("peer_name") or row.get("username") or row.get("name") or f"user_{peer_id}").strip()
            last_text = str(row.get("last_message_text") or row.get("last_message") or row.get("message") or "").strip()
            last_ts = _parse_timestamp(row.get("last_message_at") or row.get("updated_at") or row.get("time"))
            unread = int(row.get("unread_count") or row.get("unread") or 0)
            msgs = _parse_messages_list(row.get("messages") or [], base_url)
            if msgs and not last_ts:
                last_ts = max(m.timestamp for m in msgs)
            if msgs and not last_text:
                last_text = msgs[-1].text
            atts: list[KworkAttachment] = []
            for att_raw in row.get("attachments") or row.get("files") or []:
                att = _attachment_from_dict(att_raw, base_url, message_id="dialog", message_text=last_text, timestamp=last_ts)
                if att is not None:
                    atts.append(att)
            for m in msgs:
                for ma in m.attachments:
                    if not any(existing.url == ma.url and existing.message_id == ma.message_id for existing in atts):
                        atts.append(ma)
            has_atts = bool(row.get("has_attachments", False) or atts)
            kwork_url = str(row.get("kwork_url") or row.get("url") or urljoin(base_url, f"/inbox/{peer_id}"))
            dialogs.append(
                KworkDialogSummary(
                    peer_id=peer_id,
                    peer_name=peer_name,
                    last_message_text=last_text,
                    last_message_at=last_ts,
                    unread_count=unread,
                    has_attachments=has_atts,
                    kwork_url=kwork_url,
                    messages=msgs,
                    attachments=atts,
                )
            )
        return dialogs

    html_text = str(raw_data or "")
    for block in _extract_embedded_json_blocks(html_text):
        parsed = parse_dialogs_payload(block, base_url=base_url)
        if parsed:
            return parsed

    soup = BeautifulSoup(html_text, "html.parser")
    cards = soup.select("[data-peer-id], [data-dialog-id], .chat-list-item, .dialog-item")
    result: list[KworkDialogSummary] = []
    for card in cards:
        peer_id = str(card.get("data-peer-id") or card.get("data-dialog-id") or card.get("data-user-id") or "").strip()
        if not peer_id:
            continue
        name_el = card.select_one(".peer-name, .username, .name")
        preview_el = card.select_one(".last-message, .preview, .text")
        time_el = card.select_one("[data-timestamp], .time, .date")
        unread_el = card.select_one(".unread-count, .badge")
        peer_name = name_el.get_text(" ", strip=True) if name_el else f"user_{peer_id}"
        last_text = preview_el.get_text(" ", strip=True) if preview_el else ""
        ts_raw = time_el.get("data-timestamp") if time_el and time_el.get("data-timestamp") else (time_el.get_text(" ", strip=True) if time_el else "")
        last_ts = _parse_timestamp(ts_raw)
        unread_val = int(re.sub(r"\D", "", unread_el.get_text(strip=True)) or "0") if unread_el else 0
        atts = extract_attachments_from_html(card, base_url, message_id="dialog", message_text=last_text, timestamp=last_ts)
        result.append(
            KworkDialogSummary(
                peer_id=peer_id,
                peer_name=peer_name,
                last_message_text=last_text,
                last_message_at=last_ts,
                unread_count=unread_val,
                has_attachments=bool(atts) or bool(card.select_one(".has-attachment, .clip-icon")),
                kwork_url=urljoin(base_url, f"/inbox/{peer_id}"),
                messages=[],
                attachments=atts,
            )
        )
    return result


def parse_dialog_messages_payload(
    raw_data: Any,
    peer_id: str,
    base_url: str = "https://kwork.ru",
) -> tuple[list[KworkMessage], list[KworkAttachment]]:
    _, msgs, atts, _ = parse_order_detail_payload(raw_data, peer_id, base_url=base_url)
    return msgs, atts


class KworkClient:
    def __init__(
        self,
        base_dir: Optional[Path] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.base_dir = base_dir or ROOT_DIR
        self._transport = transport
        cfg = load_config()
        kw_cfg = cfg.get("kwork", {})
        self.base_url = str(kw_cfg.get("base_url") or "https://kwork.ru").rstrip("/")
        rel_sess = str(kw_cfg.get("session_file") or "state/kwork_session.json")
        self.session_file = self.base_dir / rel_sess
        self.cookies: dict[str, str] = {}
        self.token: str = ""
        self.username: str = str(kw_cfg.get("username") or "")
        self.load_session()

    @staticmethod
    def parse_cookie_input(raw_cookie: str) -> dict[str, str]:
        text = raw_cookie.strip()
        if not text:
            return {}
        if text.startswith("[") or text.startswith("{"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return {str(k): str(v) for k, v in parsed.items() if str(k).strip()}
                if isinstance(parsed, list):
                    jar: dict[str, str] = {}
                    for item in parsed:
                        if isinstance(item, dict) and item.get("name"):
                            jar[str(item["name"])] = str(item.get("value") or "")
                    return jar
            except json.JSONDecodeError:
                logger.debug("JSON cookie parse failed, falling back to Cookie header parser")

        jar = {}
        for part in text.split(";"):
            if "=" not in part:
                continue
            key, val = part.split("=", 1)
            clean_key = key.strip()
            if clean_key:
                jar[clean_key] = val.strip()
        return jar

    def load_session(self) -> bool:
        if not self.session_file.is_file():
            return False
        try:
            data = json.loads(self.session_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self.cookies = {str(k): str(v) for k, v in (data.get("cookies") or {}).items()}
                self.token = str(data.get("token") or "")
                self.username = str(data.get("username") or self.username)
                return bool(self.cookies or self.token)
        except Exception as exc:
            logger.warning("Failed to load Kwork session: %s", exc)
        return False

    def save_session(self) -> Path:
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            self.session_file,
            {
                "cookies": self.cookies,
                "token": self.token,
                "username": self.username,
                "updated_at": int(time.time()),
            },
        )
        return self.session_file

    def set_session_credentials(
        self,
        cookie_text: str = "",
        token: str = "",
        username: str = "",
    ) -> Path:
        if cookie_text.strip():
            self.cookies = self.parse_cookie_input(cookie_text)
        if token.strip():
            self.token = token.strip()
        if username.strip():
            self.username = username.strip()
        return self.save_session()

    def clear_session(self) -> None:
        self.cookies = {}
        self.token = ""
        if self.session_file.is_file():
            try:
                self.session_file.unlink()
            except OSError as exc:
                logger.warning("Failed to delete session file: %s", exc)

    def has_session(self) -> bool:
        return bool(self.cookies or self.token)

    def _build_headers(self) -> dict[str, str]:
        headers = dict(DEFAULT_HEADERS)
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _make_async_client(self, timeout: float = 20.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._build_headers(),
            cookies=self.cookies,
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            transport=self._transport,
        )

    def _assert_not_auth_blocked(self, response: httpx.Response) -> None:
        challenge = _detect_auth_challenge(response.text[:4000])
        if challenge == "captcha_required":
            raise KworkAuthRequiredError("captcha_required", "Kwork запросил капчу. Пройдите проверку в браузере и обновите cookie.")
        if challenge == "2fa_required":
            raise KworkAuthRequiredError("2fa_required", "Kwork запросил двухфакторную аутентификацию.")
        if response.status_code in (401, 403):
            raise KworkAuthRequiredError("unauthorized", f"Сессия Kwork недействительна (HTTP {response.status_code}).")
        final_path = str(response.url.path or "").lower()
        if "/login" in final_path or "/signin" in final_path:
            raise KworkAuthRequiredError("unauthorized", "Требуется повторная авторизация в Kwork.")

    async def login_with_credentials_async(self, login: str, password: str) -> dict[str, Any]:
        if not login.strip() or not password:
            raise KworkAuthRequiredError("missing_credentials", "Укажите логин и пароль Kwork или импортируйте cookie.")
        async with self._make_async_client() as client:
            resp = await client.post(
                "/api/user/signin",
                data={"l_username": login.strip(), "l_password": password},
                headers={"Accept": "application/json, text/html"},
            )
            self._assert_not_auth_blocked(resp)
            if "application/json" in resp.headers.get("content-type", ""):
                payload = resp.json()
                if payload.get("success") is False:
                    err_text = str(payload.get("error") or payload.get("message") or "Ошибка входа Kwork")
                    challenge = _detect_auth_challenge(err_text)
                    if challenge:
                        raise KworkAuthRequiredError(challenge, err_text)
                    raise KworkAuthRequiredError("invalid_credentials", err_text)
                token = str((payload.get("response") or {}).get("token") or payload.get("token") or "")
                if token:
                    self.token = token
            self.cookies.update(dict(resp.cookies))
            for k, v in client.cookies.items():
                self.cookies[k] = v
            self.username = login.strip()
            if not self.has_session():
                raise KworkAuthRequiredError("unauthorized", "Kwork не выдал cookie сессии после входа.")
            self.save_session()
            return {"ok": True, "username": self.username}

    async def check_session_async(self) -> tuple[bool, str]:
        if not self.has_session():
            return False, "missing_session"
        try:
            async with self._make_async_client(timeout=10.0) as client:
                resp = await client.get("/manage_orders?s=active")
                self._assert_not_auth_blocked(resp)
                return True, "ok"
        except KworkAuthRequiredError as exc:
            return False, exc.reason
        except Exception as exc:
            logger.warning("Kwork session check error: %s", exc)
            return False, f"network_error: {exc}"

    async def fetch_active_orders_async(self) -> list[KworkOrderRecord]:
        if not self.has_session():
            raise KworkAuthRequiredError("missing_session", "Сессия Kwork не настроена (нет cookie/токена).")

        async with self._make_async_client() as client:
            resp = await client.get("/manage_orders?s=active", headers={"Accept": "application/json, text/html"})
            self._assert_not_auth_blocked(resp)
            body: Any = resp.json() if "application/json" in resp.headers.get("content-type", "") else resp.text
            orders = parse_orders_payload(body, base_url=self.base_url)

            for order in orders:
                if order.messages and order.brief_text:
                    continue
                detail_url = f"/track?id={order.order_id}"
                try:
                    d_resp = await client.get(detail_url, headers={"Accept": "application/json, text/html"})
                    if d_resp.status_code == 200:
                        self._assert_not_auth_blocked(d_resp)
                        d_body: Any = d_resp.json() if "application/json" in d_resp.headers.get("content-type", "") else d_resp.text
                        brief, msgs, atts, meta = parse_order_detail_payload(d_body, order.order_id, base_url=self.base_url)
                        if brief and not order.brief_text:
                            order.brief_text = brief
                        if msgs and not order.messages:
                            order.messages = msgs
                        for a in atts:
                            if not any(existing.url == a.url and existing.message_id == a.message_id for existing in order.attachments):
                                order.attachments.append(a)
                        if meta.get("deadline_timestamp") and not order.deadline_timestamp:
                            order.deadline_timestamp = int(meta["deadline_timestamp"])
                        if meta.get("status"):
                            order.status = str(meta["status"])
                        if meta.get("price_rub") and not order.price_rub:
                            order.price_rub = float(meta["price_rub"])
                except KworkAuthRequiredError:
                    raise
                except Exception as exc:
                    logger.warning("Could not enrich order %s: %s", order.order_id, exc)

            return orders

    async def fetch_pm_dialogs_async(
        self,
        retention_days: int = 7,
        now_ts: Optional[int] = None,
    ) -> list[KworkDialogSummary]:
        if not self.has_session():
            raise KworkAuthRequiredError("missing_session", "Сессия Kwork не настроена (нет cookie/токена).")

        now = int(now_ts if now_ts is not None else time.time())
        cutoff = now - (max(1, int(retention_days)) * 86400)

        async with self._make_async_client() as client:
            resp = await client.get("/inbox", headers={"Accept": "application/json, text/html"})
            self._assert_not_auth_blocked(resp)
            body: Any = resp.json() if "application/json" in resp.headers.get("content-type", "") else resp.text
            dialogs = parse_dialogs_payload(body, base_url=self.base_url)

            recent: list[KworkDialogSummary] = []
            for dialog in dialogs:
                if dialog.last_message_at and dialog.last_message_at < cutoff:
                    continue
                if not dialog.messages:
                    try:
                        m_resp = await client.get(f"/inbox/{dialog.peer_id}", headers={"Accept": "application/json, text/html"})
                        if m_resp.status_code == 200:
                            self._assert_not_auth_blocked(m_resp)
                            m_body: Any = m_resp.json() if "application/json" in m_resp.headers.get("content-type", "") else m_resp.text
                            msgs, atts = parse_dialog_messages_payload(m_body, dialog.peer_id, base_url=self.base_url)
                            if msgs:
                                dialog.messages = msgs
                                if not dialog.last_message_at:
                                    dialog.last_message_at = max(m.timestamp for m in msgs)
                                if not dialog.last_message_text:
                                    dialog.last_message_text = msgs[-1].text
                            for a in atts:
                                if not any(existing.url == a.url and existing.message_id == a.message_id for existing in dialog.attachments):
                                    dialog.attachments.append(a)
                            if dialog.attachments:
                                dialog.has_attachments = True
                    except KworkAuthRequiredError:
                        raise
                    except Exception as exc:
                        logger.warning("Could not load messages for dialog %s: %s", dialog.peer_id, exc)

                if dialog.last_message_at >= cutoff:
                    recent.append(dialog)

            recent.sort(key=lambda item: item.last_message_at, reverse=True)
            return recent

    async def download_file_async(self, url: str, timeout: float = 30.0) -> bytes:
        async with self._make_async_client(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    def check_session(self) -> tuple[bool, str]:
        return asyncio.run(self.check_session_async())

    def login_with_credentials(self, login: str, password: str) -> dict[str, Any]:
        return asyncio.run(self.login_with_credentials_async(login, password))

    def fetch_active_orders(self) -> list[KworkOrderRecord]:
        return asyncio.run(self.fetch_active_orders_async())

    def fetch_pm_dialogs(self, retention_days: int = 7, now_ts: Optional[int] = None) -> list[KworkDialogSummary]:
        return asyncio.run(self.fetch_pm_dialogs_async(retention_days=retention_days, now_ts=now_ts))

    def download_file(self, url: str, timeout: float = 30.0) -> bytes:
        return asyncio.run(self.download_file_async(url, timeout=timeout))
