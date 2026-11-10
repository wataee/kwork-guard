from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from guard.library import list_snippets

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("cockpit.prompt_builder")
log_handler = RotatingFileHandler(
    LOGS_DIR / "cockpit.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
logger.addHandler(log_handler)
logger.setLevel(logging.INFO)


class PromptBuilderService:
    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir if root_dir else ROOT_DIR

    def build_roo_starter_prompt(self, order_dir: Path) -> str:
        order_json_file = order_dir / "order.json"
        brief_file = order_dir / "brief.md"

        order_data: dict[str, Any] = {}
        if order_json_file.is_file():
            try:
                order_data = json.loads(order_json_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Order json read error in prompt builder: %s", exc)

        brief_text = ""
        if brief_file.is_file():
            try:
                brief_text = brief_file.read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning("Brief read error in prompt builder: %s", exc)

        kind = order_data.get("kind", "ext")
        order_id = order_data.get("order_id", order_dir.name)
        client = order_data.get("client_name", "Заказчик")
        config_name = order_data.get("config_info", {}).get("name", "1С:Предприятие")
        platform_ver = order_data.get("config_info", {}).get("platform", "8.3.24")
        title = order_data.get("title", order_id)

        mode_map = {
            "vpf": "1С ВПФ и макеты (режим kwork-vpf)",
            "report": "1С Отчёты СКД (режим kwork-report)",
            "ext": "1С Расширения (режим kwork-ext)",
            "processor": "1С Обработки (режим kwork-vpf)",
            "intake": "Приём ТЗ Kwork (режим kwork-intake)",
        }
        recommended_mode = mode_map.get(kind, "1С Расширения (режим kwork-ext)")

        searchable = f"{title} {brief_text}".lower()
        matched_snippets: list[str] = []
        try:
            all_snippets = list_snippets()
            for s in all_snippets:
                sid = s.get("id", "")
                stitle = s.get("title", "")
                keywords = [sid, stitle] + s.get("tags", [])
                if any(kw.lower() in searchable for kw in keywords if len(kw) > 2):
                    matched_snippets.append(f"`{sid}` ({stitle})")
        except Exception as exc:
            logger.warning("Snippet matching error: %s", exc)

        scaffold_cmd = "scaffold_vpf"
        if kind == "report":
            scaffold_cmd = "scaffold_report"
        elif kind == "processor":
            scaffold_cmd = "scaffold_processor"
        elif kind == "ext":
            scaffold_cmd = "create_metadata / adopt_metadata_object"

        lines = [
            f"# Задание на разработку: {title}",
            "",
            f"**Заказчик:** {client} | **Заказ:** `{order_id}`",
            f"**Конфигурация:** {config_name} (Платформа {platform_ver})",
            f"**Целевой режим в Roo Code:** `{recommended_mode}`",
            "",
            "## 🎯 ТЗ и контекст задачи",
            brief_text.strip() if brief_text.strip() else f"Требуется реализовать {title} в строгом соответствии со стандартами 1С:БСП.",
            "",
            "## ⚡ Пошаговый пайплайн реализации (Strict Token-Saving)",
            f"1. **Сгенерируй каркас** через макрос `{scaffold_cmd}` за 1 вызов. Не пиши регистрацию БСП вручную!",
        ]

        if matched_snippets:
            lines.append(f"2. **Используй сниппеты библиотеки:** {', '.join(matched_snippets[:4])} через инструмент `library_get`.")
            lines.append("3. **Внедри бизнес-логику** через `patch_method` или `write_module_source`.")
        else:
            lines.append("2. **Внедри бизнес-логику** через `patch_method` или `write_module_source`.")

        lines.extend([
            "4. **Сверься со стандартами:** при сомнениях в методах используй `bsl_syntax_help(query='...')`. Никаких запросов в циклах и транзакций внутри Попытки!",
            "5. **Проверь синтаксис** через `build_and_check` (или `bsl_lint_module`). Ошибки компиляции недопустимы.",
            f"6. **Сформируй пакет сдачи** через макрос `complete_order(projectName='{order_id}', client_name='{client}')`.",
            "",
            "> ⚠️ **ВНИМАНИЕ:** Лимит вызовов к EDT MCP — строго 25 вызовов на заказ. Не зацикливайся, используй макросы!",
        ])

        return "\n".join(lines)
