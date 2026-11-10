from __future__ import annotations

import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Optional

import httpx

from cockpit.services.kwork_sync import get_attachment_counts, load_attachments_manifest
from cockpit.services.system_doctor import SystemDoctorService
from guard.config import load_config, sync_antigravity_mcp_config
from guard.fs_utils import atomic_write_json, atomic_write_text
from tools.order import build_mcp_config, load_order_json

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("cockpit.workspace_bridge")
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


def _format_remaining(deadline_ts: int, deadline_str: str = "") -> str:
    if deadline_ts > 0:
        diff = deadline_ts - int(time.time())
        if diff <= 0:
            hours = abs(diff) // 3600
            mins = (abs(diff) % 3600) // 60
            return f"ПРОСРОЧЕН на {hours}ч {mins}м"
        days = diff // 86400
        hours = (diff % 86400) // 3600
        mins = (diff % 3600) // 60
        if days > 0:
            return f"{days}д {hours}ч {mins}м"
        return f"{hours}ч {mins}м"
    return deadline_str or "не указан"


def _map_rule_profile(kind: str) -> str:
    low = (kind or "ext").lower().strip()
    if low == "vpf":
        return "vpf"
    if low == "report":
        return "report"
    if low == "intake":
        return "intake"
    return "ext"


def _map_mcp_profile(kind: str) -> str:
    low = (kind or "ext").lower().strip()
    if low in ("vpf", "report", "ext", "intake"):
        return low
    if low == "processor":
        return "ext"
    return "ext"


def build_order_context_markdown(order_dir: Path) -> str:
    order_data = load_order_json(order_dir) or {}
    order_id = str(order_data.get("order_id") or order_dir.name)
    title = str(order_data.get("title") or order_id)
    client = str(order_data.get("client_name") or "Клиент")
    kind = str(order_data.get("kind") or "ext")
    status = str(order_data.get("status") or "in_progress")
    price = float(order_data.get("price_rub") or 0.0)
    cfg_info = order_data.get("config_info") if isinstance(order_data.get("config_info"), dict) else {}
    config_name = str(cfg_info.get("name") or "Общая 1С")
    deadline_str = str(order_data.get("deadline") or "")
    deadline_ts = int(order_data.get("deadline_timestamp") or 0)
    kwork_url = str(order_data.get("kwork_url") or "")

    brief_file = order_dir / "brief.md"
    brief_text = brief_file.read_text(encoding="utf-8", errors="replace").strip() if brief_file.is_file() else ""

    chat_file = order_dir / "chat_history.json"
    chat_messages: list[dict[str, Any]] = []
    if chat_file.is_file():
        try:
            chat_raw = json.loads(chat_file.read_text(encoding="utf-8"))
            chat_messages = list(chat_raw.get("messages") or [])
        except Exception as exc:
            logger.debug("Chat read warning for %s: %s", order_dir, exc)

    manifest = load_attachments_manifest(order_dir)
    manifest_by_rel: dict[str, dict[str, Any]] = {
        str(entry.get("relative_path") or ""): entry
        for entry in (manifest.get("files") or [])
        if entry.get("relative_path")
    }

    att_root = order_dir / "attachments"
    screenshots: list[str] = []
    onec_files: list[str] = []
    documents: list[str] = []
    if att_root.is_dir():
        for cat, target_list in (("screenshots", screenshots), ("1c_files", onec_files), ("documents", documents)):
            sub = att_root / cat
            if sub.is_dir():
                for file_item in sorted(sub.iterdir()):
                    if file_item.is_file():
                        target_list.append(f"attachments/{cat}/{file_item.name}")

    lines = [
        f"# Контекст заказа: {order_id}",
        "",
        f"- **Название:** {title}",
        f"- **Покупатель:** {client}",
        f"- **Статус:** {status}",
        f"- **Тип задачи:** {kind}",
        f"- **Конфигурация 1С:** {config_name}",
        f"- **Бюджет:** {price:.0f} ₽",
        f"- **Дедлайн:** {deadline_str or '—'} (осталось: {_format_remaining(deadline_ts, deadline_str)})",
        f"- **Kwork URL:** {kwork_url or '—'}",
        f"- **Workspace:** `{order_dir}`",
        "",
        "## Инструменты MCP (`edt-guard`)",
        "",
        "- Вызови `get_order_context`, если нужен быстрый программный доступ к ТЗ и списку вложений.",
        "- Вызови `get_ai_rules` и `bsl_lint_module` перед записью модулей BSL.",
        "- Для визуальных референсов от клиента читай файлы скриншотов по относительным путям ниже.",
        "",
        "## Техническое задание (brief.md)",
        "",
        brief_text or "ТЗ пока не заполнено.",
        "",
    ]

    if screenshots or onec_files or documents:
        lines.append("## Локальные вложения (относительные пути)")
        lines.append("")
        if screenshots:
            lines.append("### Скриншоты и макеты (`attachments/screenshots/`)")
            for rel in screenshots:
                meta = manifest_by_rel.get(rel, {})
                msg_note = str(meta.get("message_text") or "").strip().replace("\n", " ")[:120]
                suffix = f" — из сообщения: «{msg_note}»" if msg_note else ""
                lines.append(f"- `{rel}`{suffix}")
            lines.append("")
        if onec_files:
            lines.append("### Файлы 1С (`attachments/1c_files/`)")
            for rel in onec_files:
                lines.append(f"- `{rel}`")
            lines.append("")
        if documents:
            lines.append("### Документы и архивы (`attachments/documents/`)")
            for rel in documents:
                lines.append(f"- `{rel}`")
            lines.append("")

    if chat_messages:
        lines.append("## История переписки по заказу")
        lines.append("")
        for msg in chat_messages[-40:]:
            ts = int(msg.get("timestamp") or 0)
            ts_str = datetime.datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M") if ts > 0 else "—"
            role_label = "Клиент" if msg.get("is_client", True) else "Исполнитель"
            sender = str(msg.get("sender") or role_label)
            text = str(msg.get("text") or "").strip()
            lines.append(f"- **[{ts_str}] {sender} ({role_label}):** {text}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _copy_rules_and_agents(
    order_dir: Path,
    base_dir: Path,
    kind: str,
    context_md: str,
) -> tuple[Path, Path]:
    rule_profile = _map_rule_profile(kind)
    rule_src = base_dir / ".roo" / f"rules-kwork-{rule_profile}" / "rules.md"
    if not rule_src.is_file():
        rule_src = base_dir / ".roo" / "rules-kwork-vpf" / "rules.md"

    parts: list[str] = []
    if rule_src.is_file():
        parts.append(rule_src.read_text(encoding="utf-8", errors="replace").strip())

    bsl_rules = base_dir / "templates" / "bsl_ai_rules.md"
    if bsl_rules.is_file():
        parts.append(bsl_rules.read_text(encoding="utf-8", errors="replace").strip())

    combined_rules = "\n\n---\n\n".join(p for p in parts if p)
    roo_rules_file = order_dir / ".roo" / "rules" / "rules.md"
    roo_rules_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(roo_rules_file, combined_rules + "\n", encoding="utf-8")

    agents_content = f"{combined_rules}\n\n---\n\n{context_md}" if combined_rules else context_md
    agents_file = order_dir / "AGENTS.md"
    atomic_write_text(agents_file, agents_content, encoding="utf-8")
    return roo_rules_file, agents_file


def register_active_workspace(
    order_id: str,
    order_dir: Path,
    task_type: str,
    base_dir: Optional[Path] = None,
) -> Path:
    root = base_dir or ROOT_DIR
    state_file = root / "state" / "active_workspace.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {}
    if state_file.is_file():
        try:
            loaded = json.loads(state_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception as exc:
            logger.debug("Reset active_workspace.json due to read error: %s", exc)

    mapping = data.get("orders")
    if not isinstance(mapping, dict):
        mapping = {}
    mapping[order_id] = str(order_dir)
    data["orders"] = mapping
    data["active_order_id"] = order_id
    data["active_order_dir"] = str(order_dir)
    data["active_task_type"] = task_type
    data["updated_at"] = int(time.time())
    atomic_write_json(state_file, data)
    return state_file


def notify_running_guard_workspace(
    order_id: str,
    order_dir: Path,
    task_type: str,
    guard_port: int = 8766,
) -> bool:
    try:
        with httpx.Client(timeout=1.5) as client:
            resp = client.post(
                f"http://127.0.0.1:{guard_port}/admin/active-workspace",
                json={
                    "order_id": order_id,
                    "order_dir": str(order_dir),
                    "task_type": task_type,
                },
            )
            return resp.status_code == 200
    except Exception:
        return False


def prepare_antigravity_workspace(
    order_dir: Path,
    base_dir: Optional[Path] = None,
    prepare_global_mcp: bool = True,
) -> dict[str, Any]:
    root = base_dir or ROOT_DIR
    resolved_dir = order_dir.resolve()
    resolved_dir.mkdir(parents=True, exist_ok=True)

    order_data = load_order_json(resolved_dir) or {}
    order_id = str(order_data.get("order_id") or resolved_dir.name)
    kind = str(order_data.get("kind") or "ext")
    mcp_profile = _map_mcp_profile(kind)

    cfg = load_config()
    guard_port = int(cfg.get("guard_port", 8766))
    edt_url = str(cfg.get("upstream", {}).get("edt_url") or os.getenv("EDT_MCP_URL", "http://127.0.0.1:8765/mcp"))

    context_md = build_order_context_markdown(resolved_dir)
    context_path = resolved_dir / "ORDER_CONTEXT.md"
    atomic_write_text(context_path, context_md, encoding="utf-8")

    mcp_cfg = build_mcp_config(mcp_profile, order_id, guard_host="127.0.0.1", guard_port=guard_port)
    for server_cfg in (mcp_cfg.get("mcpServers") or {}).values():
        if isinstance(server_cfg, dict):
            headers = dict(server_cfg.get("headers") or {})
            headers["X-Order-Id"] = order_id
            headers["X-Order-Dir"] = str(resolved_dir)
            headers["X-Task-Type"] = kind
            server_cfg["headers"] = headers
            server_cfg["env"] = {
                "KWORK_ORDER_DIR": str(resolved_dir),
                "KWORK_TASK_TYPE": kind,
                "EDT_MCP_URL": edt_url,
            }

    roo_mcp_path = resolved_dir / ".roo" / "mcp.json"
    roo_mcp_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(roo_mcp_path, mcp_cfg)
    atomic_write_json(resolved_dir / ".mcp.json", mcp_cfg)

    roo_rules_path, agents_path = _copy_rules_and_agents(resolved_dir, root, kind, context_md)

    register_active_workspace(order_id, resolved_dir, kind, base_dir=root)
    is_isolated_root = root.resolve() != ROOT_DIR.resolve()
    mcp_target = (root / "state" / "antigravity_mcp_config.json") if is_isolated_root else None
    if prepare_global_mcp:
        try:
            sync_antigravity_mcp_config(
                profile=mcp_profile,
                order_id=order_id,
                host="127.0.0.1",
                port=guard_port,
                target_path=mcp_target,
            )
        except Exception as exc:
            logger.warning("Failed to sync global Antigravity mcp_config.json: %s", exc)
        if not is_isolated_root and not os.getenv("PYTEST_CURRENT_TEST"):
            notify_running_guard_workspace(order_id, resolved_dir, kind, guard_port=guard_port)

    counts = get_attachment_counts(resolved_dir)
    return {
        "ok": True,
        "order_id": order_id,
        "order_dir": str(resolved_dir),
        "profile": mcp_profile,
        "context_file": str(context_path),
        "roo_mcp_file": str(roo_mcp_path),
        "roo_rules_file": str(roo_rules_path),
        "agents_file": str(agents_path),
        "attachments": counts,
    }


def find_antigravity_executable(configured_path: str = "") -> Optional[Path]:
    if configured_path:
        cand = Path(configured_path)
        if cand.is_file():
            return cand
    which_bin = shutil.which("antigravity")
    if which_bin:
        return Path(which_bin)
    if sys.platform == "win32":
        local_app = os.getenv("LOCALAPPDATA", "")
        if local_app:
            for rel in (
                Path("Programs/antigravity/Antigravity.exe"),
                Path("Programs/antigravity/bin/antigravity.cmd"),
                Path("Programs/Antigravity/Antigravity.exe"),
            ):
                p = Path(local_app) / rel
                if p.is_file():
                    return p
    return None


def launch_antigravity_workspace(
    order_dir: Path,
    base_dir: Optional[Path] = None,
    system_doctor: Optional[SystemDoctorService] = None,
    onec_runner: Any = None,
) -> dict[str, Any]:
    root = base_dir or ROOT_DIR
    prep = prepare_antigravity_workspace(order_dir, base_dir=root, prepare_global_mcp=True)

    doc = system_doctor or SystemDoctorService(root)
    guard_online = doc.guard_runner.is_guard_running(port=int(load_config().get("guard_port", 8766)))
    edt_online = doc.guard_runner.is_edt_running()
    warnings: list[str] = []
    if not guard_online:
        warnings.append("Шлюз kwork-guard (порт 8766) не отвечает")
    if not edt_online:
        warnings.append("1C:EDT MCP (порт 8765) не отвечает")

    ide_used = ""
    launched = False
    agy_exe = find_antigravity_executable()
    if agy_exe is not None:
        try:
            subprocess.Popen([str(agy_exe), str(order_dir)], cwd=str(order_dir))
            ide_used = "Antigravity"
            launched = True
        except Exception as exc:
            warnings.append(f"Не удалось запустить Antigravity: {exc}")

    if not launched and onec_runner is not None:
        ok_code, msg_code = onec_runner.open_in_vscode(order_dir)
        if ok_code:
            ide_used = "VS Code"
            launched = True
        else:
            warnings.append(msg_code)

    if not launched and sys.platform == "win32":
        try:
            os.startfile(str(order_dir))
            ide_used = "Explorer"
            launched = True
        except Exception as exc:
            warnings.append(f"Не удалось открыть папку: {exc}")

    return {
        "ok": launched,
        "ide_used": ide_used or "None",
        "guard_online": guard_online,
        "edt_online": edt_online,
        "prep": prep,
        "warnings": warnings,
    }
