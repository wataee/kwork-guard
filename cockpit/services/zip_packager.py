from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
import zipfile

from guard.config import load_config

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR = ROOT_DIR / "templates"

logger = logging.getLogger("cockpit.zip_packager")
log_handler = RotatingFileHandler(
    LOGS_DIR / "cockpit.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
logger.addHandler(log_handler)
logger.setLevel(logging.INFO)


class DeliveryPackagerService:
    def __init__(self, templates_dir: Path | None = None, base_dir: Path | None = None) -> None:
        if base_dir and not templates_dir:
            templates_dir = base_dir / "templates"
        self.templates_dir = templates_dir if templates_dir else TEMPLATES_DIR

    def render_delivery_templates(self, order_dir: Path) -> list[str]:
        order_json_file = order_dir / "order.json"
        order_data: dict[str, Any] = {}
        if order_json_file.is_file():
            try:
                order_data = json.loads(order_json_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.error("Order JSON read error: %s", exc)

        order_id = str(order_data.get("order_id", order_dir.name))
        client_name = str(order_data.get("client_name", "Заказчик"))
        object_name = str(order_data.get("title", order_id))
        config_name = str(order_data.get("config_info", {}).get("name", "1С:Предприятие"))
        kind = str(order_data.get("kind", "ext"))

        context = {
            "order_id": order_id,
            "client_name": client_name,
            "object_name": object_name,
            "configuration": config_name,
            "kind": kind,
        }

        template_map = [
            ("instructions_client.md", "ИНСТРУКЦИЯ_ДЛЯ_КЛИЕНТА.md"),
            ("message_kwork.md", "СООБЩЕНИЕ_ДЛЯ_KWORK.txt"),
            ("checklist_manual.md", "ЧЕК_ЛИСТ_ПРОВЕРКИ.md"),
        ]

        delivery_dir = order_dir / "delivery"
        delivery_dir.mkdir(parents=True, exist_ok=True)

        errors: list[str] = []
        for src_name, dst_name in template_map:
            tpl_path = self.templates_dir / src_name
            if not tpl_path.is_file():
                continue

            content = tpl_path.read_text(encoding="utf-8")
            for key, val in context.items():
                content = content.replace(f"{{{{{key}}}}}", val)

            unresolved = re.findall(r"\{\{([^}]+)\}\}", content)
            if unresolved:
                errors.append(f"Нераскрытые шаблоны в {src_name}: {unresolved}")

            dst_file = delivery_dir / dst_name
            dst_file.write_text(content, encoding="utf-8")

        return errors

    def build_delivery_zip(self, order_dir: Path) -> Path:
        delivery_dir = order_dir / "delivery"
        delivery_dir.mkdir(parents=True, exist_ok=True)

        self.render_delivery_templates(order_dir)

        order_json_file = order_dir / "order.json"
        order_data: dict[str, Any] = {}
        if order_json_file.is_file():
            try:
                order_data = json.loads(order_json_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Order data read error for zip: %s", exc)

        order_id = str(order_data.get("order_id", order_dir.name))
        safe_obj = re.sub(r"[^A-Za-zА-Яа-я0-9_]", "_", str(order_data.get("title", order_id)))
        zip_name = f"Заказ_{order_id}_{safe_obj}.zip"
        zip_path = delivery_dir / zip_name

        artifact_candidates: list[Path] = []
        for search_folder in [delivery_dir, order_dir / "src", order_dir]:
            if search_folder.is_dir():
                for ext in ("*.epf", "*.erf", "*.cfe", "*.xml"):
                    for file_match in search_folder.glob(ext):
                        if file_match != zip_path and file_match not in artifact_candidates:
                            artifact_candidates.append(file_match)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for art in artifact_candidates:
                archive.write(art, arcname=art.name)

            instr_file = delivery_dir / "ИНСТРУКЦИЯ_ДЛЯ_КЛИЕНТА.md"
            if instr_file.is_file():
                archive.write(instr_file, arcname=instr_file.name)

            chk_file = delivery_dir / "ЧЕК_ЛИСТ_ПРОВЕРКИ.md"
            if chk_file.is_file():
                archive.write(chk_file, arcname=chk_file.name)

        cfg = load_config()
        configured_desktop_dir = cfg.get("workspace", {}).get("delivery_desktop_dir", "~/Desktop/1C_Delivery")
        target_desktop_dir = Path(os.path.expanduser(configured_desktop_dir))
        try:
            target_desktop_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(zip_path, target_desktop_dir / zip_name)
        except Exception as exc:
            logger.warning("Copy delivery zip to desktop error: %s", exc)

        return zip_path

    def reveal_in_explorer(self, file_path: Path) -> None:
        if not file_path.exists():
            return
        try:
            if sys.platform == "win32":
                subprocess.run(["explorer", f"/select,{file_path}"], check=False)
            elif sys.platform == "darwin":
                subprocess.run(["open", "-R", str(file_path)], check=False)
            else:
                subprocess.run(["xdg-open", str(file_path.parent)], check=False)
        except Exception as exc:
            logger.warning("Reveal in explorer error: %s", exc)
