from __future__ import annotations

import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
from typing import Any
import urllib.error
import urllib.request

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("cockpit.intake_parser")
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


class IntakeParserService:
    def __init__(self) -> None:
        self.default_model = "google/gemini-2.5-flash"

    def parse_raw_kwork_text_regex(self, raw_text: str) -> dict[str, Any]:
        text = raw_text.strip()

        order_id_match = (
            re.search(r"kwork\.ru/(?:track\?id=|projects/|software/)(\d+)", text, re.IGNORECASE)
            or re.search(r"№\s*(\d+)", text)
            or re.search(r"заказ[^\d]{1,10}(\d+)", text, re.IGNORECASE)
        )
        if order_id_match:
            raw_id = f"kwork_{order_id_match.group(1)}"
        else:
            raw_id = f"order_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}"

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

        return {
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

    def parse_raw_kwork_text_llm(
        self,
        raw_text: str,
        api_key: str,
        model: str = "google/gemini-2.5-flash",
    ) -> dict[str, Any]:
        fallback_data = self.parse_raw_kwork_text_regex(raw_text)
        if not api_key or not api_key.strip():
            return fallback_data

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
            "model": model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": raw_text},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
        }

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(request_body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
                "User-Agent": "kwork-cockpit",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                resp_json = json.loads(resp.read().decode("utf-8"))
                content = resp_json["choices"][0]["message"]["content"]
                parsed_llm = json.loads(content)
                merged = {**fallback_data, **parsed_llm}
                merged["raw_text"] = raw_text
                return merged
        except Exception as exc:
            logger.warning("LLM parse fallback to regex due to error: %s", exc)
            return fallback_data
