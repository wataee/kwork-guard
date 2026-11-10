from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import datetime
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import shutil
import time
from typing import Any, Callable, Optional

from cockpit.services.intake_parser import IntakeParserService, transliterate
from cockpit.services.kwork_client import (
    KworkAttachment,
    KworkAuthRequiredError,
    KworkClient,
    KworkDialogSummary,
    KworkMessage,
    KworkOrderRecord,
)
from guard.config import load_config
from guard.fs_utils import atomic_write_json, atomic_write_text
from tools.order import get_orders_dir, load_order_json, save_order_json

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("cockpit.kwork_sync")
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

CATEGORY_1C_EXTS = {".epf", ".erf", ".cfe", ".cf", ".dt", ".bsl", ".mxl"}
CATEGORY_SCREENSHOT_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
CATEGORY_DOC_EXTS = {".xlsx", ".xls", ".docx", ".doc", ".pdf", ".txt", ".zip", ".rar", ".7z", ".csv"}


def categorize_extension(filename_or_ext: str) -> str:
    ext = filename_or_ext.lower().strip()
    if not ext.startswith("."):
        ext = Path(ext).suffix.lower()
    if ext in CATEGORY_1C_EXTS:
        return "1c_files"
    if ext in CATEGORY_SCREENSHOT_EXTS:
        return "screenshots"
    if ext in CATEGORY_DOC_EXTS:
        return "documents"
    return "documents"


def _sanitize_slug(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", raw.strip()).strip("_")
    return cleaned if cleaned else "item"


def _kind_from_parsed(raw_kind: str) -> str:
    mapping = {
        "extension": "ext",
        "ext": "ext",
        "vpf": "vpf",
        "report": "report",
        "processor": "processor",
        "tz": "intake",
        "intake": "intake",
        "project": "project",
    }
    return mapping.get(raw_kind.lower().strip(), "ext")


@dataclass
class SyncResult:
    ok: bool
    status: str
    message: str
    synced_orders_count: int = 0
    synced_dialogs_count: int = 0
    new_attachments_count: int = 0
    errors: list[str] = field(default_factory=list)


def get_attachment_counts(target_dir: Path) -> dict[str, int]:
    counts = {"1c_files": 0, "screenshots": 0, "documents": 0, "files": 0, "total": 0}
    manifest_path = target_dir / "attachments_manifest.json"
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = data.get("files") or []
            for entry in files:
                cat = str(entry.get("category") or "documents")
                if cat in counts:
                    counts[cat] += 1
                else:
                    counts["documents"] += 1
            counts["files"] = counts["1c_files"] + counts["documents"]
            counts["total"] = counts["files"] + counts["screenshots"]
            return counts
        except Exception as exc:
            logger.debug("Manifest count fallback for %s: %s", target_dir, exc)

    att_root = target_dir / "attachments"
    if att_root.is_dir():
        for cat in ("1c_files", "screenshots", "documents"):
            sub = att_root / cat
            if sub.is_dir():
                counts[cat] = sum(1 for item in sub.iterdir() if item.is_file())
    counts["files"] = counts["1c_files"] + counts["documents"]
    counts["total"] = counts["files"] + counts["screenshots"]
    return counts


def load_attachments_manifest(target_dir: Path) -> dict[str, Any]:
    manifest_path = target_dir / "attachments_manifest.json"
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("files"), list):
                return data
        except Exception as exc:
            logger.warning("Manifest load error for %s: %s", target_dir, exc)
    return {"files": [], "updated_at": 0}


class KworkSyncService:
    def __init__(
        self,
        base_dir: Optional[Path] = None,
        orders_dir: Optional[Path] = None,
        client: Optional[KworkClient] = None,
        intake_parser: Optional[IntakeParserService] = None,
    ) -> None:
        self.base_dir = base_dir or ROOT_DIR
        self.orders_dir = orders_dir or get_orders_dir()
        self.client = client or KworkClient(base_dir=self.base_dir)
        self.intake_parser = intake_parser or IntakeParserService()
        self.inbox_cache_dir = self.orders_dir / "_inbox_cache"
        self._last_sync_result: Optional[SyncResult] = None

    @property
    def last_sync_result(self) -> Optional[SyncResult]:
        return self._last_sync_result

    def get_retention_days(self) -> int:
        cfg = load_config()
        val = int(cfg.get("kwork", {}).get("chat_retention_days", 7) or 7)
        return max(1, val)

    def _find_existing_order_dir(self, order_id: str, kwork_url: str = "") -> Optional[Path]:
        if not self.orders_dir.is_dir():
            return None
        safe_id = _sanitize_slug(order_id)
        for candidate_name in (safe_id, f"kwork_{safe_id}"):
            cand = self.orders_dir / candidate_name
            if cand.is_dir() and (cand / "order.json").is_file():
                return cand
        for child in self.orders_dir.iterdir():
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            data = load_order_json(child)
            if not data:
                continue
            if str(data.get("order_id") or "") == order_id:
                return child
            if kwork_url and str(data.get("kwork_url") or "") == kwork_url:
                return child
        return None

    async def _download_and_store_attachments_async(
        self,
        target_dir: Path,
        attachments: list[KworkAttachment],
        owner_id: str,
    ) -> int:
        att_root = target_dir / "attachments"
        for sub in ("1c_files", "screenshots", "documents"):
            (att_root / sub).mkdir(parents=True, exist_ok=True)

        manifest = load_attachments_manifest(target_dir)
        existing_entries: list[dict[str, Any]] = list(manifest.get("files") or [])
        known_hashes: dict[str, dict[str, Any]] = {}
        known_urls: set[str] = set()
        for entry in existing_entries:
            rel = str(entry.get("relative_path") or "")
            if rel and (target_dir / rel).is_file():
                if entry.get("sha256"):
                    known_hashes[str(entry["sha256"])] = entry
                if entry.get("source_url"):
                    known_urls.add(str(entry["source_url"]))

        new_count = 0
        for att in attachments:
            if att.url in known_urls:
                continue
            try:
                content = await self.client.download_file_async(att.url)
            except Exception as exc:
                logger.warning("Failed to download attachment %s: %s", att.url, exc)
                continue

            if not content:
                continue
            digest = hashlib.sha256(content).hexdigest()
            if digest in known_hashes:
                known_urls.add(att.url)
                continue

            category = categorize_extension(att.extension or att.filename)
            cat_dir = att_root / category
            dest_name = att.filename
            dest_path = cat_dir / dest_name
            if dest_path.exists():
                stem = dest_path.stem
                suffix = dest_path.suffix
                dest_name = f"{stem}_{digest[:8]}{suffix}"
                dest_path = cat_dir / dest_name

            dest_path.write_bytes(content)
            rel_path = f"attachments/{category}/{dest_name}"
            record = {
                "file_id": digest[:12],
                "filename": dest_name,
                "category": category,
                "relative_path": rel_path,
                "sha256": digest,
                "source_url": att.url,
                "size_bytes": len(content),
                "message_id": att.message_id,
                "message_text": att.message_text,
                "timestamp": att.timestamp,
            }
            existing_entries.append(record)
            known_hashes[digest] = record
            known_urls.add(att.url)
            new_count += 1

        manifest["order_id"] = owner_id
        manifest["updated_at"] = int(time.time())
        manifest["files"] = existing_entries
        atomic_write_json(target_dir / "attachments_manifest.json", manifest)
        return new_count

    def _serialize_messages(self, messages: list[KworkMessage]) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for msg in sorted(messages, key=lambda item: item.timestamp):
            serialized.append(
                {
                    "message_id": msg.message_id,
                    "sender": msg.sender,
                    "is_client": msg.is_client,
                    "text": msg.text,
                    "timestamp": msg.timestamp,
                    "attachments": [
                        {
                            "filename": a.filename,
                            "url": a.url,
                            "extension": a.extension,
                        }
                        for a in msg.attachments
                    ],
                }
            )
        return serialized

    def _sync_single_order_metadata(
        self,
        order: KworkOrderRecord,
        order_dir: Path,
    ) -> dict[str, Any]:
        order_dir.mkdir(parents=True, exist_ok=True)
        existing = load_order_json(order_dir) or {}
        combined_text = "\n".join([order.title, order.brief_text] + [m.text for m in order.messages])
        parsed_intake = self.intake_parser.parse_raw_kwork_text_regex(combined_text)

        detected_kind = _kind_from_parsed(str(parsed_intake.get("kind") or "ext"))
        detected_cfg = str(parsed_intake.get("config_name") or "Общая 1С")

        existing_cfg = existing.get("config_info") if isinstance(existing.get("config_info"), dict) else {}
        config_name = existing_cfg.get("name") or detected_cfg
        kind_val = existing.get("kind") or detected_kind
        if existing.get("_manual_override") is True:
            kind_val = existing.get("kind", detected_kind)
            config_name = existing_cfg.get("name", detected_cfg)

        deadline_iso = existing.get("deadline") or ""
        if order.deadline_timestamp > 0:
            deadline_iso = datetime.datetime.fromtimestamp(order.deadline_timestamp).strftime("%Y-%m-%d %H:%M")

        created_iso = existing.get("created_at") or order.created_at or datetime.date.today().isoformat()
        history = existing.get("history") if isinstance(existing.get("history"), list) else []
        if not history:
            history = [
                {
                    "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                    "event": "kwork_synced",
                    "note": "Синхронизирован из Kwork",
                }
            ]

        payload: dict[str, Any] = {
            "order_id": existing.get("order_id") or order.order_id,
            "client_name": order.buyer_username or existing.get("client_name") or "Покупатель",
            "title": order.title or existing.get("title") or order.order_id,
            "kwork_url": order.kwork_url or existing.get("kwork_url") or "",
            "kind": kind_val,
            "status": order.status or existing.get("status") or "in_progress",
            "price_rub": float(order.price_rub if order.price_rub > 0 else existing.get("price_rub", 0.0)),
            "prepaid_rub": float(existing.get("prepaid_rub", 0.0)),
            "hours_spent": float(existing.get("hours_spent", 0.0)),
            "created_at": created_iso,
            "deadline": deadline_iso,
            "deadline_timestamp": int(order.deadline_timestamp or existing.get("deadline_timestamp") or 0),
            "delivered_at": existing.get("delivered_at"),
            "config_info": {
                "name": config_name,
                "version": str(existing_cfg.get("version") or ""),
                "platform": str(existing_cfg.get("platform") or "8.3"),
            },
            "tags": list(existing.get("tags") or [kind_val, "kwork"]),
            "history": history,
        }
        if "notes" in existing:
            payload["notes"] = existing["notes"]
        if "_manual_override" in existing:
            payload["_manual_override"] = existing["_manual_override"]

        save_order_json(order_dir, payload)

        brief_file = order_dir / "brief.md"
        if order.brief_text.strip() or not brief_file.is_file():
            brief_md = (
                f"# {payload['title']}\n\n"
                f"- **ID заказа:** {payload['order_id']}\n"
                f"- **Покупатель:** {payload['client_name']}\n"
                f"- **Kwork URL:** {payload['kwork_url']}\n"
                f"- **Конфигурация 1С:** {config_name}\n\n"
                f"## Техническое задание\n\n"
                f"{order.brief_text.strip() or 'Описание отсутствует.'}\n"
            )
            atomic_write_text(brief_file, brief_md, encoding="utf-8")

        chat_file = order_dir / "chat_history.json"
        atomic_write_json(
            chat_file,
            {
                "order_id": payload["order_id"],
                "updated_at": int(time.time()),
                "messages": self._serialize_messages(order.messages),
            },
        )
        return payload

    def _prune_old_inbox_dialogs(self, cutoff_ts: int) -> None:
        if not self.inbox_cache_dir.is_dir():
            return
        for item in list(self.inbox_cache_dir.iterdir()):
            if not item.is_dir():
                continue
            dialog_file = item / "dialog.json"
            if not dialog_file.is_file():
                continue
            try:
                data = json.loads(dialog_file.read_text(encoding="utf-8"))
                last_ts = int(data.get("last_message_at") or 0)
                if last_ts < cutoff_ts:
                    shutil.rmtree(item, ignore_errors=True)
            except Exception as exc:
                logger.warning("Error pruning inbox dir %s: %s", item, exc)

    async def sync_all_async(
        self,
        now_ts: Optional[int] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> SyncResult:
        self.orders_dir.mkdir(parents=True, exist_ok=True)
        now = int(now_ts if now_ts is not None else time.time())
        retention_days = self.get_retention_days()
        cutoff = now - (retention_days * 86400)

        if not self.client.has_session():
            res = SyncResult(
                ok=False,
                status="auth_required",
                message="Сессия Kwork не настроена (введите cookie в Настройках)",
            )
            self._last_sync_result = res
            return res

        synced_orders = 0
        synced_dialogs = 0
        new_attachments = 0
        errors: list[str] = []

        try:
            if progress_cb:
                progress_cb("Загрузка активных заказов Kwork...")
            orders = await self.client.fetch_active_orders_async()
            for ord_rec in orders:
                order_dir = self._find_existing_order_dir(ord_rec.order_id, ord_rec.kwork_url)
                if order_dir is None:
                    safe_dir_name = _sanitize_slug(ord_rec.order_id)
                    if not safe_dir_name.lower().startswith("kwork_"):
                        safe_dir_name = f"kwork_{safe_dir_name}"
                    order_dir = self.orders_dir / safe_dir_name
                self._sync_single_order_metadata(ord_rec, order_dir)
                new_attachments += await self._download_and_store_attachments_async(
                    order_dir,
                    ord_rec.attachments,
                    ord_rec.order_id,
                )
                synced_orders += 1

            if progress_cb:
                progress_cb("Синхронизация переписок (7 дн.)...")
            dialogs = await self.client.fetch_pm_dialogs_async(retention_days=retention_days, now_ts=now)
            self.inbox_cache_dir.mkdir(parents=True, exist_ok=True)
            self._prune_old_inbox_dialogs(cutoff)

            index_entries: list[dict[str, Any]] = []
            for dlg in dialogs:
                if dlg.last_message_at < cutoff:
                    continue
                safe_peer = _sanitize_slug(dlg.peer_id)
                peer_dir = self.inbox_cache_dir / safe_peer
                peer_dir.mkdir(parents=True, exist_ok=True)
                new_attachments += await self._download_and_store_attachments_async(
                    peer_dir,
                    dlg.attachments,
                    dlg.peer_id,
                )
                dlg_payload = {
                    "peer_id": dlg.peer_id,
                    "peer_name": dlg.peer_name,
                    "last_message_text": dlg.last_message_text,
                    "last_message_at": dlg.last_message_at,
                    "unread_count": dlg.unread_count,
                    "has_attachments": bool(dlg.has_attachments or dlg.attachments),
                    "kwork_url": dlg.kwork_url,
                    "messages": self._serialize_messages(dlg.messages),
                }
                atomic_write_json(peer_dir / "dialog.json", dlg_payload)
                counts = get_attachment_counts(peer_dir)
                index_entries.append(
                    {
                        "peer_id": dlg.peer_id,
                        "peer_name": dlg.peer_name,
                        "last_message_text": dlg.last_message_text,
                        "last_message_at": dlg.last_message_at,
                        "unread_count": dlg.unread_count,
                        "has_attachments": bool(dlg.has_attachments or counts["total"] > 0),
                        "kwork_url": dlg.kwork_url,
                        "attachment_counts": counts,
                    }
                )
                synced_dialogs += 1

            index_entries.sort(key=lambda item: int(item.get("last_message_at") or 0), reverse=True)
            atomic_write_json(
                self.inbox_cache_dir / "index.json",
                {"updated_at": now, "cutoff_ts": cutoff, "dialogs": index_entries},
            )

            res = SyncResult(
                ok=True,
                status="ok",
                message=f"Синхронизировано заказов: {synced_orders}, диалогов (7д): {synced_dialogs}, файлов: +{new_attachments}",
                synced_orders_count=synced_orders,
                synced_dialogs_count=synced_dialogs,
                new_attachments_count=new_attachments,
                errors=errors,
            )
            self._last_sync_result = res
            return res

        except KworkAuthRequiredError as auth_err:
            res = SyncResult(
                ok=False,
                status="auth_required",
                message=auth_err.message,
                errors=[auth_err.reason],
            )
            self._last_sync_result = res
            return res
        except Exception as exc:
            logger.error("Kwork sync failure: %s", exc, exc_info=True)
            res = SyncResult(
                ok=False,
                status="error",
                message=f"Ошибка синхронизации Kwork: {exc}",
                errors=[str(exc)],
            )
            self._last_sync_result = res
            return res

    def sync_all(
        self,
        now_ts: Optional[int] = None,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> SyncResult:
        return asyncio.run(self.sync_all_async(now_ts=now_ts, progress_cb=progress_cb))

    def list_recent_dialogs(self, now_ts: Optional[int] = None) -> list[dict[str, Any]]:
        now = int(now_ts if now_ts is not None else time.time())
        cutoff = now - (self.get_retention_days() * 86400)
        index_file = self.inbox_cache_dir / "index.json"
        dialogs: list[dict[str, Any]] = []
        if index_file.is_file():
            try:
                raw = json.loads(index_file.read_text(encoding="utf-8"))
                for entry in raw.get("dialogs") or []:
                    if int(entry.get("last_message_at") or 0) >= cutoff:
                        dialogs.append(entry)
                dialogs.sort(key=lambda item: int(item.get("last_message_at") or 0), reverse=True)
                return dialogs
            except Exception as exc:
                logger.warning("Failed to read inbox index: %s", exc)

        if self.inbox_cache_dir.is_dir():
            for child in self.inbox_cache_dir.iterdir():
                dfile = child / "dialog.json"
                if child.is_dir() and dfile.is_file():
                    try:
                        data = json.loads(dfile.read_text(encoding="utf-8"))
                        if int(data.get("last_message_at") or 0) >= cutoff:
                            data["attachment_counts"] = get_attachment_counts(child)
                            dialogs.append(data)
                    except Exception as exc:
                        logger.debug("Skipped malformed dialog %s: %s", child, exc)
        dialogs.sort(key=lambda item: int(item.get("last_message_at") or 0), reverse=True)
        return dialogs

    def get_dialog_detail(self, peer_id: str) -> Optional[dict[str, Any]]:
        safe_peer = _sanitize_slug(peer_id)
        peer_dir = self.inbox_cache_dir / safe_peer
        dfile = peer_dir / "dialog.json"
        if not dfile.is_file():
            return None
        try:
            data = json.loads(dfile.read_text(encoding="utf-8"))
            data["peer_dir"] = str(peer_dir)
            data["manifest"] = load_attachments_manifest(peer_dir)
            return data
        except Exception as exc:
            logger.warning("Failed to load dialog detail %s: %s", peer_id, exc)
            return None

    def create_order_workspace_from_dialog(
        self,
        peer_id: str,
        orders_facade: Any,
        order_slug: str = "",
        kind: Optional[str] = None,
        configuration: Optional[str] = None,
        price_rub: float = 0.0,
        deadline: str = "",
    ) -> tuple[Any, int]:
        detail = self.get_dialog_detail(peer_id)
        if not detail:
            raise ValueError(f"Диалог {peer_id} не найден в кэше")

        peer_name = str(detail.get("peer_name") or f"client_{peer_id}")
        messages = detail.get("messages") or []
        all_texts = [str(m.get("text") or "") for m in messages if str(m.get("text") or "").strip()]
        combined_text = "\n\n".join(all_texts) or str(detail.get("last_message_text") or "")

        parsed = self.intake_parser.parse_raw_kwork_text_regex(combined_text)
        chosen_kind = kind or _kind_from_parsed(str(parsed.get("kind") or "ext"))
        chosen_config = configuration or str(parsed.get("config_name") or "Общая 1С")
        chosen_price = price_rub if price_rub > 0 else float(parsed.get("price_rub") or 0.0)
        chosen_deadline = deadline or str(parsed.get("deadline_text") or "")

        slug = order_slug.strip() if order_slug.strip() else f"chat_{transliterate(peer_name)}_{_sanitize_slug(peer_id)}"
        title_src = str(parsed.get("title") or all_texts[0][:80] if all_texts else f"Заказ из чата {peer_name}")

        created = orders_facade.create_order(
            order_slug=slug,
            client=peer_name,
            title=title_src,
            kind=chosen_kind,
            price=chosen_price,
            configuration=chosen_config,
            deadline=chosen_deadline,
            kwork_url=str(detail.get("kwork_url") or ""),
        )
        order_dir = created.order_dir

        transcript_lines = [
            f"# {title_src}",
            "",
            f"- **Клиент:** {peer_name}",
            f"- **Конфигурация 1С:** {chosen_config}",
            f"- **Kwork чат:** {detail.get('kwork_url') or ''}",
            "",
            "## История переписки с клиентом",
            "",
        ]
        for m in messages:
            ts = int(m.get("timestamp") or 0)
            ts_str = datetime.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M") if ts > 0 else "—"
            sender = str(m.get("sender") or peer_name)
            txt = str(m.get("text") or "")
            transcript_lines.append(f"### [{ts_str}] {sender}")
            transcript_lines.append(txt)
            transcript_lines.append("")

        atomic_write_text(order_dir / "brief.md", "\n".join(transcript_lines), encoding="utf-8")
        atomic_write_json(
            order_dir / "chat_history.json",
            {"order_id": created.order_id, "updated_at": int(time.time()), "messages": messages},
        )

        copied_count = 0
        peer_dir = self.inbox_cache_dir / _sanitize_slug(peer_id)
        src_att_root = peer_dir / "attachments"
        dst_att_root = order_dir / "attachments"
        if src_att_root.is_dir():
            for sub in ("1c_files", "screenshots", "documents"):
                src_sub = src_att_root / sub
                dst_sub = dst_att_root / sub
                if src_sub.is_dir():
                    dst_sub.mkdir(parents=True, exist_ok=True)
                    for item in src_sub.iterdir():
                        if item.is_file():
                            shutil.copy2(item, dst_sub / item.name)
                            copied_count += 1

        src_manifest = load_attachments_manifest(peer_dir)
        if src_manifest.get("files"):
            src_manifest["order_id"] = created.order_id
            src_manifest["updated_at"] = int(time.time())
            atomic_write_json(order_dir / "attachments_manifest.json", src_manifest)

        refreshed = orders_facade.get_order(created.order_id) or created
        return refreshed, copied_count
