from __future__ import annotations

import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
from typing import Any

from guard.config import load_config, select_model_for_task
from guard.dual_backend import DualBackendClient

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("cockpit.intake_parser")
if not logger.handlers:
    log_handler = RotatingFileHandler(
        LOGS_DIR / "cockpit.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    logger.addHandler(log_handler)
    logger.setLevel(logging.INFO)

CYR_TO_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def transliterate(text: str) -> str:
    result: list[str] = []
    for char in text:
        low = char.lower()
        if low in CYR_TO_LAT:
            lat = CYR_TO_LAT[low]
            result.append(lat.capitalize() if char.isupper() else lat)
        elif char.isalnum():
            result.append(char)
        else:
            result.append("_")
    joined = "".join(result)
    cleaned = re.sub(r"_+", "_", joined).strip("_")
    return cleaned if cleaned else "Order"


def generate_clarifying_questions(parsed_data: dict[str, Any], raw_text: str) -> list[str]:
    low = raw_text.lower()
    questions: list[str] = []

    cfg_name = str(parsed_data.get("config_name") or "")
    has_release_num = bool(re.search(r"\b(?:3\.0\.\d+|11\.\d+(?:\.\d+)?|2\.\d+(?:\.\d+)?)\b", low))
    if cfg_name in ("1С:Предприятие 8.3", "") or not has_release_num:
        questions.append(
            "1. Подскажите, пожалуйста, точную конфигурацию и номер релиза вашей базы 1С "
            "(например: УНФ 3.0.9, УТ 11.5.17, БП 3.0.150)?"
        )

    has_mode = bool(re.search(r"(?i)\b(?:файлов\w+|сервер\w+|фреш|fresh|облак\w+|локальн\w+)\b", low))
    if not has_mode:
        questions.append(
            "2. В каком режиме работает база: файловая, клиент-серверная или облако (1С:Фреш)? "
            "(Для 1С:Фреш есть строгие ограничения на безопасный режим и фоновые задания)."
        )

    has_output = bool(re.search(r"(?i)\b(?:pdf|excel|xlsx|mxl|табличн\w+|бсп|кнопк\w+|подсистем\w+|макет)\b", low))
    if not has_output:
        questions.append(
            "3. В каком формате нужен итоговый результат: внешняя печатная форма (.epf) / отчет СКД (.erf) "
            "под БСП или расширение (.cfe)? Нужна ли выгрузка в Excel/PDF?"
        )

    has_access = bool(re.search(r"(?i)\b(?:\.dt|выгрузк\w+|тестов\w+\s+баз|anydesk|teamviewer|рдп|rdp|доступ)\b", low))
    if not has_access:
        questions.append(
            "4. Как планируем тестировать разработку: сможете предоставить тестовую выгрузку (.dt) / "
            "копию базы или достаточно готового файла для вашей самостоятельной проверки?"
        )

    return questions


def format_clarifying_questions_text(questions: list[str]) -> str:
    if not questions:
        return "В ТЗ указаны все базовые технические детали для старта разработки."
    intro = "Добрый день! Изучил задачу — чтобы точно оценить и сделать без переделок, уточните пару деталей:"
    return intro + "\n\n" + "\n".join(questions)


class IntakeParserService:
    def __init__(self, dual_client: DualBackendClient | None = None) -> None:
        self.default_model = "google/gemini-2.5-flash"
        self.dual_client = dual_client if dual_client else DualBackendClient()

    def parse_raw_kwork_text_regex(self, raw_text: str) -> dict[str, Any]:
        text = raw_text.strip()

        order_id_match = (
            re.search(r"kwork\.ru/(?:track\?id=|projects/|software/)(\d+)", text, re.IGNORECASE)
            or re.search(r"№\s*(\d+)", text)
            or re.search(r"заказ[^\d]{1,10}(\d+)", text, re.IGNORECASE)
        )

        client_match = (
            re.search(r"kwork\.ru/user/([a-zA-Z0-9_-]+)", text, re.IGNORECASE)
            or re.search(r"(?:заказчик|клиент|пользователь)[:\s]+([A-Za-zА-Яа-я0-9_-]+)", text, re.IGNORECASE)
        )
        client_name = client_match.group(1).strip() if client_match else "Заказчик"

        price = 0.0
        price_match = re.search(r"(\d[\d\s]{2,8})\s*(?:руб|₽|rur|rub)", text, re.IGNORECASE)
        if price_match:
            raw_price = price_match.group(1).replace(" ", "")
            try:
                price = float(raw_price)
            except Exception as exc:
                logger.warning("Price parse error: %s", exc)

        config_name = "1С:Предприятие 8.3"
        config_rules = [
            (r"(?i)\b(?:ут\s*11|управлени[ея]\s+торговлей)\b", "1С:УТ 11"),
            (r"(?i)\b(?:бп\s*3|бухгалтери[яи])\b", "1С:БП 3.0"),
            (r"(?i)\b(?:зуп\s*3|зарплат[аы])\b", "1С:ЗУП 3.1"),
            (r"(?i)\b(?:унф|наш[ае]й?\s+фирм[оеы]й?)\b", "1С:УНФ 3.0"),
            (r"(?i)\b(?:комплексн\w+|ка\s*2)\b", "1С:КА 2"),
            (r"(?i)\b(?:erp|ерп)\b", "1С:ERP 2"),
            (r"(?i)\b(?:розниц\w+)\b", "1С:Розница 3.0"),
            (r"(?i)\b(?:автосервис)\b", "1С:Автосервис"),
            (r"(?i)\b(?:уат|автотранспорт\w*)\b", "1С:УАТ"),
        ]
        for pattern, cfg_label in config_rules:
            if re.search(pattern, text):
                config_name = cfg_label
                break

        kind = "ext"
        if re.search(r"(?i)(?:печатн\w+|впф|счет|накладн\w+|акт|упд|макет|форма\s+печати)", text):
            kind = "vpf"
        elif re.search(r"(?i)(?:отчет\w*|скд|диаграмм\w+|ведомост\w+)", text):
            kind = "report"
        elif re.search(r"(?i)(?:обработк\w+|загрузк\w+|выгрузк\w+|обмен\w*|импорт|экспорт)", text):
            kind = "processor"
        elif re.search(r"(?i)(?:расширен\w+|\.cfe|без\s+снятия|подсистем\w+)", text):
            kind = "ext"

        clean_slug = transliterate(client_name)
        object_name = f"{kind.capitalize()}{clean_slug}"
        if order_id_match:
            order_slug = f"{clean_slug.lower()}_{order_id_match.group(1)}_{kind}"
        else:
            order_slug = f"{clean_slug.lower()}_{kind}"

        clean_title = " ".join(text.split()[:12]) if text else order_slug
        if len(clean_title) > 80:
            clean_title = clean_title[:77] + "..."

        result: dict[str, Any] = {
            "order_id": order_slug,
            "client_name": client_name,
            "title": clean_title,
            "kind": kind,
            "price_rub": price,
            "config_name": config_name,
            "platform_ver": "8.3.24",
            "object_name": object_name,
            "kwork_url": "",
            "deadline_days": 2,
            "raw_text": text,
        }
        questions = generate_clarifying_questions(result, text)
        result["clarifying_questions"] = questions
        result["clarifying_questions_text"] = format_clarifying_questions_text(questions)
        return result

    def parse_raw_kwork_text_llm(
        self,
        raw_text: str,
        api_key: str,
        model: str = "",
    ) -> dict[str, Any]:
        fallback_data = self.parse_raw_kwork_text_regex(raw_text)
        if not api_key or not api_key.strip():
            return fallback_data

        cfg = load_config()
        routed_model = model.strip() or select_model_for_task("intake", cfg)

        system_instruction = (
            "Ты — 1С-архитектор. Проанализируй сообщение заказчика с Kwork и верни JSON со следующими полями:\n"
            "order_id (строка на латинице, напр. client_vpf_schet),\n"
            "client_name (строка),\n"
            "title (краткое название задачи до 60 символов),\n"
            "kind (строго один из: vpf, report, processor, ext),\n"
            "price_rub (число),\n"
            "config_name (напр. 1С:УТ 11, 1С:БП 3.0, 1С:УНФ 3.0),\n"
            "platform_ver (напр. 8.3.24),\n"
            "object_name (CamelCase имя 1С, напр. ВПФСчетСпецификация),\n"
            "deadline_days (целое число дней, по умолч. 2)."
        )

        request_body = {
            "model": routed_model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": raw_text},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }

        try:
            resp_json, _ = self.dual_client.chat_completion_sync(
                payload=request_body,
                task_type="intake",
                order_id=str(fallback_data.get("order_id", "default")),
                api_key_override=api_key,
                timeout=10.0,
            )
            content = resp_json["choices"][0]["message"]["content"]
            parsed_llm = json.loads(content)
            merged = {**fallback_data, **parsed_llm}
            merged["raw_text"] = raw_text
            questions = generate_clarifying_questions(merged, raw_text)
            merged["clarifying_questions"] = questions
            merged["clarifying_questions_text"] = format_clarifying_questions_text(questions)
            return merged
        except Exception as exc:
            logger.warning("LLM parse fallback to regex due to error: %s", exc)
            return fallback_data
