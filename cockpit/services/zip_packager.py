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
from typing import Any, Optional
import zipfile

from guard.config import load_config
from guard.fs_utils import atomic_write_text, write_bsl_with_bom
from tools.verify_zero_defect import verify_bsl_directory

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR = ROOT_DIR / "templates"

logger = logging.getLogger("cockpit.zip_packager")
if not logger.handlers:
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

    def ensure_src_bsl_bom(self, order_dir: Path) -> int:
        updated = 0
        src_dir = order_dir / "src"
        if not src_dir.is_dir():
            return 0
        for bsl_file in src_dir.rglob("*.bsl"):
            try:
                raw_bytes = bsl_file.read_bytes()
                if not raw_bytes.startswith(b"\xef\xbb\xbf"):
                    content = raw_bytes.decode("utf-8", errors="replace")
                    write_bsl_with_bom(bsl_file, content)
                    updated += 1
            except Exception as exc:
                logger.warning("BOM conversion error on %s: %s", bsl_file, exc)
        return updated

    def _adapt_instructions_for_kind(self, content: str, kind: str) -> str:
        low_kind = kind.strip().lower()
        marker1 = "## Вариант 1."
        marker2 = "## Вариант 2."
        if marker1 not in content or marker2 not in content:
            return content

        idx1 = content.find(marker1)
        idx2 = content.find(marker2)
        header = content[:idx1].rstrip()

        if low_kind in ("ext", "extension"):
            body2 = content[idx2:].replace(marker2, "## Подключение расширения (.cfe)")
            return f"{header}\n\n{body2.strip()}\n"

        sec1 = content[idx1:idx2].rstrip()
        if sec1.endswith("---"):
            sec1 = sec1[:-3].rstrip()
        sec1 = sec1.replace(marker1, "## Подключение внешней формы / отчета / обработки")
        return f"{header}\n\n{sec1}\n"

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

            if src_name == "instructions_client.md":
                content = self._adapt_instructions_for_kind(content, kind)

            unresolved = re.findall(r"\{\{([^}]+)\}\}", content)
            if unresolved:
                errors.append(f"Нераскрытые шаблоны в {src_name}: {unresolved}")

            dst_file = delivery_dir / dst_name
            atomic_write_text(dst_file, content, encoding="utf-8")

        return errors

    def build_delivery_zip(self, order_dir: Path) -> Path:
        delivery_dir = order_dir / "delivery"
        delivery_dir.mkdir(parents=True, exist_ok=True)

        self.ensure_src_bsl_bom(order_dir)
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
                for ext in ("*.epf", "*.erf", "*.cfe", "*.xml", "*.bsl"):
                    for file_match in search_folder.rglob(ext):
                        if file_match != zip_path and file_match not in artifact_candidates:
                            artifact_candidates.append(file_match)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for art in artifact_candidates:
                arc_name = str(art.relative_to(order_dir)) if art.is_relative_to(order_dir / "src") else art.name
                archive.write(art, arcname=arc_name)

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

    def compile_and_package_order(
        self,
        order_dir: Path,
        onec_runner: Optional[Any] = None,
    ) -> dict[str, Any]:
        self.ensure_src_bsl_bom(order_dir)
        bsl_ok, bsl_notes = verify_bsl_directory(order_dir / "src", final_validation=True)
        template_errors = self.render_delivery_templates(order_dir)

        onec_note = "Тестовая база 1С не настроена — автозапуск пропущен"
        if onec_runner is not None:
            cfg = load_config()
            test_ib = str(cfg.get("onec", {}).get("test_ib_path") or "").strip()
            epf_files = list(order_dir.glob("*.epf")) + list(order_dir.glob("*.erf"))
            if test_ib and epf_files:
                ok_1c, msg_1c = onec_runner.launch_epf_in_thin_client(test_ib, epf_files[0])
                onec_note = msg_1c if ok_1c else f"1С предупреждение: {msg_1c}"

        zip_path = self.build_delivery_zip(order_dir)
        msg_file = order_dir / "delivery" / "СООБЩЕНИЕ_ДЛЯ_KWORK.txt"
        kwork_message = msg_file.read_text(encoding="utf-8") if msg_file.is_file() else ""

        return {
            "ok": bsl_ok and not template_errors,
            "zip_path": zip_path,
            "kwork_message": kwork_message,
            "bsl_clean": bsl_ok,
            "bsl_notes": bsl_notes,
            "template_errors": template_errors,
            "onec_note": onec_note,
        }

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
