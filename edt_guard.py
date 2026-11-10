from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import time
from typing import Any, Optional

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from guard.bsl_linter import format_diagnostics_for_llm, lint_bsl_code
from guard.cache import ReadCache, is_cacheable_read, is_write_tool
from guard.compactor import compact_mcp_result, prune_verified_bsl_history
from guard.config import get_active_provider, load_config, set_active_provider
from guard.dual_backend import (
    DualBackendClient,
    parse_clean_json_arguments,
    strip_empty_schema_fields,
)
from guard.library import (
    build_context_attachment,
    get_snippet,
    inject_snippets_into_code,
    list_snippets,
)
from guard.platform_context import PlatformContextService
from guard.state import OrderManager
from guard.macros import (
    macro_bootstrap_gold,
    macro_scaffold_report,
    macro_scaffold_processor,
    macro_get_critical_errors,
    macro_patch_method,
    macro_read_bsl_method,
    macro_complete_order,
    macro_bsl_syntax_help,
    macro_bsl_lint_module,
    macro_get_ai_rules,
    macro_compact_schema,
    macro_get_order_context,
)

_LOGS_DIR = Path(__file__).resolve().parent / "logs"
_LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOGGER = logging.getLogger("kwork-guard")
if not LOGGER.handlers:
    _log_handler = RotatingFileHandler(
        _LOGS_DIR / "guard_stdout.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    _log_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
    LOGGER.addHandler(_log_handler)
    LOGGER.setLevel(logging.INFO)

CONFIG = load_config()
ORDER_MGR = OrderManager()
DUAL_CLIENT = DualBackendClient(ORDER_MGR)
PLATFORM_CTX = PlatformContextService()
VERIFIED_MODULES: set[str] = set()
CACHE = ReadCache(
    ttl_sec=CONFIG.get("cache", {}).get("ttl_sec", 300),
    enabled=CONFIG.get("cache", {}).get("enabled", True),
)

EDT_URL = os.getenv("EDT_MCP_URL", "http://127.0.0.1:8765/mcp")
EDT_TOKEN = os.getenv("EDT_MCP_TOKEN", "")
KWORK_ORDER_DIR = os.getenv("KWORK_ORDER_DIR", "").strip()
KWORK_TASK_TYPE = os.getenv("KWORK_TASK_TYPE", "").strip()
if KWORK_ORDER_DIR and Path(KWORK_ORDER_DIR).is_dir():
    _kdir = Path(KWORK_ORDER_DIR).resolve()
    ORDER_MGR.register_order_dir(_kdir.name, _kdir, KWORK_TASK_TYPE)
PORT = int(os.getenv("GUARD_PORT", "8766"))
MAX_RESULT_CHARS = int(os.getenv("MAX_RESULT_CHARS", "6000"))
MAX_DESC_CHARS = int(os.getenv("MAX_DESC_CHARS", "300"))
LOG_PATH = Path(os.getenv("GUARD_LOG", Path(__file__).with_name("guard_log.jsonl")))
EVENTS_PATH = Path(os.getenv("GUARD_EVENTS", Path(__file__).parent / "state" / "events.jsonl"))

MAX_SAME_FAILS = 2
MAX_CONSEC_ERRORS = 5
LOOP_WINDOW_SEC = 15 * 60

MODULE_PATH_TMPL = "ExternalDataProcessors/{name}/ObjectModule.bsl"
EXTERNAL_OBJECT_TMPL = "ExternalDataProcessor.{name}"
TEMPLATE_FQN_TMPL = "ExternalDataProcessor.{name}.Template.{template}"
ERRORS_ARGS = {"severity": "ERRORS", "limit": 20}
BUILD_OUTPUT_ARG = "outputDir"
DEFAULT_BSP_VERSION = "2.2.2.1"

_COMMON = {
    "list_projects", "get_tool_guide", "get_project_errors",
    "library_list", "library_get",
    "bootstrap_gold", "scaffold_processor", "get_critical_errors", "patch_method",
    "read_bsl_method", "complete_order",
}

PROFILES: dict[str, Optional[set[str]]] = {
    "vpf": _COMMON | {
        "create_project", "import_project_from_file", "create_metadata", "modify_metadata",
        "get_metadata_objects", "get_metadata_details", "search_in_code", "read_method_source",
        "find_references", "validate_query", "list_modules", "get_applications",
        "read_module_source", "write_module_source", "get_template_screenshot",
        "get_module_structure", "build_external_objects",
        "scaffold_vpf", "build_and_check",
    },
    "report": _COMMON | {
        "create_project", "import_project_from_file", "create_metadata", "modify_metadata",
        "get_metadata_objects", "get_metadata_details", "search_in_code", "read_method_source",
        "list_modules", "dcs", "search_in_dcs", "validate_query",
        "read_module_source", "write_module_source",
        "get_module_structure", "build_external_objects", "build_and_check", "scaffold_report",
    },
    "ext": _COMMON | {
        "import_project_from_file", "get_metadata_objects", "get_metadata_details",
        "adopt_metadata_object", "create_metadata", "modify_metadata", "search_in_code",
        "read_method_source", "get_module_structure", "write_module_source", "find_references",
        "validate_query", "revalidate_objects", "apply_quick_fix", "export_configuration_to_file",
    },
    "full": None,
}

def tool_result(text: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": error}


def result_text(result: dict) -> str:
    parts = []
    for item in (result or {}).get("content", []) or []:
        if item.get("type") == "text":
            parts.append(item.get("text", ""))
        elif item.get("type") == "resource":
            parts.append((item.get("resource") or {}).get("text", ""))
    return "\n".join(p for p in parts if p)


_SUCCESS_FALSE = re.compile(r'"success"\s*:\s*false')


def is_error(resp: dict) -> bool:
    if resp.get("error"):
        return True
    res = resp.get("result") or {}
    if res.get("isError"):
        return True
    return bool(_SUCCESS_FALSE.search(result_text(res)[:300]))


def cap_text(result: dict, tool_name: str = "") -> dict:
    for item in (result or {}).get("content", []) or []:
        txt = item.get("text")
        if isinstance(txt, str):
            item["text"] = compact_mcp_result(tool_name, txt, MAX_RESULT_CHARS)
    return result


def rotate_log(log_path: Path, backup_count: int) -> None:
    try:
        for i in range(backup_count - 1, 0, -1):
            src = log_path.with_name(f"{log_path.stem}.{i}{log_path.suffix}")
            dst = log_path.with_name(f"{log_path.stem}.{i+1}{log_path.suffix}")
            if src.is_file():
                src.replace(dst)
        dst1 = log_path.with_name(f"{log_path.stem}.1{log_path.suffix}")
        if log_path.is_file():
            log_path.replace(dst1)
    except Exception as exc:
        logging.getLogger("kwork-guard").warning("Log rotation error: %s", exc)


def normalize_error_signature(tool: str, err_text: str, note: str) -> str:
    raw = err_text.strip() if err_text.strip() else note
    raw = " ".join(raw.split())
    raw = re.sub(r"['\"][A-Za-zА-Яа-я0-9_./\\]+['\"]", "<id>", raw)
    raw = re.sub(r"\[\d+,\s*\d+\]", "[line, col]", raw)
    raw = re.sub(r"\b\d+\b", "<N>", raw)
    if len(raw) > 200:
        raw = raw[:200]
    return f"{tool}: {raw}" if not raw.startswith(tool) else raw


def record_event(profile: str, tool: str, err_text: str, note: str, order_id: str) -> None:
    try:
        EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
        sig = normalize_error_signature(tool, err_text, note)
        with EVENTS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": int(time.time()),
                "profile": profile,
                "tool": tool,
                "signature": sig,
                "note": note,
                "order_id": order_id,
            }, ensure_ascii=False) + "\n")
    except OSError as err:
        LOGGER.warning("IO error: %s", err)


def log_call(profile: str, tool: str, ok: bool, ms: int, resp_chars: int,
             note: str = "", order_id: str = "default", cache_hit: bool = False,
             err_text: str = "") -> None:
    try:
        max_bytes = CONFIG.get("logging", {}).get("max_bytes", 5242880)
        backup_count = CONFIG.get("logging", {}).get("backup_count", 3)
        if LOG_PATH.is_file() and LOG_PATH.stat().st_size >= max_bytes:
            rotate_log(LOG_PATH, backup_count)

        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": int(time.time()),
                "order_id": order_id,
                "profile": profile,
                "tool": tool,
                "ok": ok,
                "ms": ms,
                "resp_chars": resp_chars,
                "note": note,
                "cache_hit": cache_hit
            }, ensure_ascii=False) + "\n")
        
        if not ok:
            record_event(profile, tool, err_text, note, order_id)
    except OSError as err:
        LOGGER.warning("IO error: %s", err)


def parse_body(r: httpx.Response) -> Optional[dict]:
    
    if not r.content:
        return None
    if "text/event-stream" in r.headers.get("content-type", ""):
        last = None
        for line in r.text.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and "id" in obj and ("result" in obj or "error" in obj):
                last = obj
        return last
    return r.json()


class Upstream:
    def __init__(self) -> None:
        self.sid: Optional[str] = None
        self.ready = False
        self._rid = 0
        self._lock: Optional[asyncio.Lock] = None
        self._http: Optional[httpx.AsyncClient] = None
        self._http_loop: Optional[asyncio.AbstractEventLoop] = None
        self._tools_cache: Optional[tuple[float, list]] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def http(self) -> httpx.AsyncClient:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._http is None or self._http_loop != loop or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0))
            self._http_loop = loop
        return self._http

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if EDT_TOKEN:
            h["Authorization"] = f"Bearer {EDT_TOKEN}"
        if self.sid:
            h["Mcp-Session-Id"] = self.sid
        return h

    async def _initialize(self) -> None:
        async with self._get_lock():
            if self.ready:
                return
            self.sid = None
            self._rid += 1
            r = await self.http.post(EDT_URL, headers=self._headers(), json={
                "jsonrpc": "2.0", "id": self._rid, "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                           "clientInfo": {"name": "edt-guard", "version": "1.0"}}})
            r.raise_for_status()
            self.sid = r.headers.get("mcp-session-id")
            await self.http.post(EDT_URL, headers=self._headers(),
                                 json={"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.ready = True

    async def rpc(self, method: str, params: Optional[dict] = None, _retry: bool = True) -> dict:
        if not self.ready:
            await self._initialize()
        self._rid += 1
        r = await self.http.post(EDT_URL, headers=self._headers(), json={
            "jsonrpc": "2.0", "id": self._rid, "method": method, "params": params or {}})
        if r.status_code in (400, 404) and _retry:
            self.ready = False
            return await self.rpc(method, params, _retry=False)
        r.raise_for_status()
        body = parse_body(r)
        if body is None:
            return {"error": {"code": -32603, "message": "Пустой ответ EDT-MCP"}}
        return body

    async def tool(self, name: str, args: dict) -> tuple[str, bool]:
        
        try:
            resp = await self.rpc("tools/call", {"name": name, "arguments": args})
        except httpx.HTTPError as e:
            return f"EDT-MCP недоступен: {e}", True
        if resp.get("error"):
            return str(resp["error"].get("message", resp["error"])), True
        return result_text(resp.get("result", {})), is_error(resp)

    async def list_tools(self) -> list:
        now = time.time()
        if self._tools_cache and now - self._tools_cache[0] < 60:
            return self._tools_cache[1]
        resp = await self.rpc("tools/list")
        tools = (resp.get("result") or {}).get("tools", [])
        self._tools_cache = (now, tools)
        return tools


UP = Upstream()

class LoopGuard:
    def __init__(self) -> None:
        self.same: dict[str, tuple[int, float]] = {}
        self.consec = 0

    @staticmethod
    def key(name: str, args: dict) -> str:
        return hashlib.md5((name + json.dumps(args, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()

    def check(self, name: str, args: dict) -> Optional[str]:
        n, ts = self.same.get(self.key(name, args), (0, 0.0))
        if n and time.time() - ts > LOOP_WINDOW_SEC:
            n = 0
        if n >= MAX_SAME_FAILS:
            return (f"СТОП: вызов {name} с этими аргументами уже {n} раза завершился ошибкой. "
                    f"Не повторяй его. Измени аргументы или прочитай "
                    f"get_tool_guide(section=...) по {name}. Если не ясно — спроси пользователя.")
        if self.consec >= MAX_CONSEC_ERRORS:
            self.consec = 0
            return (f"СТОП: {MAX_CONSEC_ERRORS} ошибок подряд. Прекрати вызовы инструментов, "
                    f"кратко опиши пользователю, что не получается, и жди указаний.")
        return None

    def record(self, name: str, args: dict, failed: bool) -> None:
        k = self.key(name, args)
        if failed:
            n, _ = self.same.get(k, (0, 0.0))
            self.same[k] = (n + 1, time.time())
            self.consec += 1
        else:
            self.same.pop(k, None)
            self.consec = 0


GUARDS: dict[str, LoopGuard] = {p: LoopGuard() for p in PROFILES}

MODIFY_EXAMPLE = ('Свойства: {"projectName":"П","fqn":"Catalog.X","properties":'
                  '[{"name":"Synonym","value":"Текст"}]}. '
                  'Для макета SpreadsheetDocument вместо properties передают объект "template" '
                  '(cells/merges/areas/columnWidths).')


def prevalidate(name: str, args: dict) -> Optional[str]:
    if name == "modify_metadata":
        if "properties" in args:
            props = args.get("properties")
            if not isinstance(props, list) or not props:
                return "modify_metadata: properties пуст. " + MODIFY_EXAMPLE
            for i, p in enumerate(props):
                if not isinstance(p, dict) or not str(p.get("name", "")).strip() or "value" not in p:
                    return f"modify_metadata: в properties[{i}] нет name/value. " + MODIFY_EXAMPLE
        elif not any(k in args for k in ("template", "interface", "commandInterface")) \
                and set(args) <= {"projectName", "fqn"}:
            return "modify_metadata: не указано, что менять. " + MODIFY_EXAMPLE
    if name == "get_tool_guide" and not any(k in args for k in ("mode", "section", "query")):
        args["mode"] = "summary"
    return None


IDENT = re.compile(r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*$")
PURPOSE = re.compile(r"^[A-Za-zА-Яа-яЁё]+\.[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*$")


def bsl(s: str) -> str:
    
    return str(s).replace('"', '""').replace("'", "’")


MACRO_TOOLS: dict[str, dict] = {
    "scaffold_vpf": {
        "name": "scaffold_vpf",
        "description": (
            "Создаёт внешнюю печатную форму под БСП ЦЕЛИКОМ за один вызов: проект, макет "
            "SpreadsheetDocument (области Шапка/Строка/Подвал) и ObjectModule с "
            "СведенияОВнешнейОбработке и Печать. Регистрацию БСП НЕ пиши сам. Логику выборки "
            "данных передай в data_code (тело процедуры ЗаполнитьДанные)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Имя обработки (идентификатор 1С, без пробелов)"},
                "purposes": {"type": "array", "items": {"type": "string"},
                             "description": "Назначение БСП, напр. ['Документ.СчетНаОплатуПокупателю']"},
                "command_title": {"type": "string", "description": "Название кнопки печати"},
                "command_id": {"type": "string", "description": "Идентификатор команды (по умолчанию = name)"},
                "title": {"type": "string", "description": "Заголовок в макете"},
                "header_fields": {"type": "array", "description": "Поля шапки",
                                  "items": {"type": "object", "properties": {
                                      "label": {"type": "string"}, "parameter": {"type": "string"}},
                                      "required": ["label", "parameter"]}},
                "columns": {"type": "array", "description": "Колонки табличной части (минимум 2)",
                            "items": {"type": "object", "properties": {
                                "title": {"type": "string"}, "parameter": {"type": "string"},
                                "width": {"type": "number"}}, "required": ["title", "parameter"]}},
                "data_code": {"type": "string",
                              "description": ("BSL — тело процедуры ЗаполнитьДанные(Ссылка, ПараметрыШапки, "
                                              "СтрокиТаблицы, ПараметрыПодвала). Шапка: Структура по parameter полей "
                                              "шапки; СтрокиТаблицы: Массив структур по parameter колонок; "
                                              "ПараметрыПодвала: Итого.")},
                "template_name": {"type": "string", "description": "Имя макета, по умолчанию Макет"},
                "bsp_version": {"type": "string", "description": f"Версия БСП, по умолчанию {DEFAULT_BSP_VERSION}"},
                "info": {"type": "string", "description": "Описание обработки для БСП"},
                "snippet_ids": {"type": "array", "items": {"type": "string"},
                                "description": "ID сниппетов из библиотеки для внедрения в data_code"},
                "skip_create_project": {"type": "boolean",
                                        "description": "true — проект уже создан, пропустить create_project"},
            },
            "required": ["name", "purposes", "columns"],
        },
    },
    "build_and_check": {
        "name": "build_and_check",
        "description": ("Проверяет проект на ошибки (get_project_errors) и, если ошибок нет, собирает "
                        ".epf/.erf (build_external_objects). Возвращает краткий итог."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectName": {"type": "string"},
                "objectName": {"type": "string", "description": "По умолчанию = projectName"},
                "outputDir": {"type": "string", "description": "Каталог для готового файла (необязательно)"},
                "force_build": {"type": "boolean", "description": "Собрать, даже если есть ошибки"},
            },
            "required": ["projectName"],
        },
    },
    "library_list": {
        "name": "library_list",
        "description": "Возвращает список проверенных сниппетов 1С (BSL/Query) из библиотеки эталонов.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Фильтр по тегам"},
                "snippet_type": {"type": "string", "description": "Фильтр по типу: bsl или query"},
            },
        },
    },
    "library_get": {
        "name": "library_get",
        "description": "Возвращает текст и метаданные эталонного сниппета 1С по его id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "snippet_id": {"type": "string", "description": "ID сниппета"},
            },
            "required": ["snippet_id"],
        },
    },
    "bootstrap_gold": {
        "name": "bootstrap_gold",
        "description": "Клонирует проект из эталона (vpf_standard / report_standard) на диск за 0 токенов вызовов MCP.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template_name": {"type": "string", "description": "Имя эталона: vpf_standard или report_standard"},
                "target_dir": {"type": "string", "description": "Путь к целевой папке проекта на диске"},
                "new_name": {"type": "string", "description": "Новое имя объекта 1С"},
                "dry_run": {"type": "boolean", "description": "Режим симуляции без записи"},
            },
            "required": ["template_name", "target_dir", "new_name"],
        },
    },
    "scaffold_report": {
        "name": "scaffold_report",
        "description": "Создаёт внешний отчет на СКД под БСП целиком за один вызов: проект, схема СКД и ObjectModule.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Имя отчета (идентификатор 1С)"},
                "query": {"type": "string", "description": "Текст запроса 1С"},
                "title": {"type": "string", "description": "Представление отчета"},
                "bsp_version": {"type": "string", "description": "Версия БСП, по умолчанию 2.2.2.1"},
                "skip_create_project": {"type": "boolean", "description": "Пропустить create_project"},
                "dry_run": {"type": "boolean", "description": "Режим симуляции без записи"},
            },
            "required": ["name", "query"],
        },
    },
    "scaffold_processor": {
        "name": "scaffold_processor",
        "description": "Создаёт внешнюю обработку под БСП с командой ВыполнитьКоманду целиком за один вызов.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Имя обработки (идентификатор 1С)"},
                "command_title": {"type": "string", "description": "Представление команды"},
                "action_code": {"type": "string", "description": "BSL-код внутри процедуры ВыполнитьКоманду"},
                "bsp_version": {"type": "string", "description": "Версия БСП, по умолчанию 2.2.2.1"},
                "skip_create_project": {"type": "boolean", "description": "Пропустить create_project"},
                "dry_run": {"type": "boolean", "description": "Режим симуляции без записи"},
            },
            "required": ["name"],
        },
    },
    "get_critical_errors": {
        "name": "get_critical_errors",
        "description": "Возвращает только критические ошибки компиляции без стилевого шума и предупреждений.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectName": {"type": "string", "description": "Имя проекта в EDT"},
                "limit": {"type": "integer", "description": "Максимум строк (по умолчанию 10)"},
            },
            "required": ["projectName"],
        },
    },
    "patch_method": {
        "name": "patch_method",
        "description": "Хирургически заменяет или оборачивает конкретный метод BSL в модуле без перезаписи всего файла.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectName": {"type": "string", "description": "Имя проекта в EDT"},
                "modulePath": {"type": "string", "description": "Путь к модулю BSL"},
                "methodName": {"type": "string", "description": "Имя функции или процедуры"},
                "new_source": {"type": "string", "description": "Новый BSL-код метода"},
                "wrap_mode": {"type": "string", "enum": ["replace", "before", "after"], "description": "Режим: replace (по умолч.), before или after"},
                "dry_run": {"type": "boolean", "description": "Режим симуляции без записи"},
            },
            "required": ["projectName", "modulePath", "methodName", "new_source"],
        },
    },
    "complete_order": {
        "name": "complete_order",
        "description": "Формирует итоговый пакет сдачи заказа: проверка без ошибок, компиляция, генерация инструкции и сообщений для Kwork, опциональный Git-коммит.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectName": {"type": "string", "description": "Имя проекта в EDT"},
                "object_name": {"type": "string", "description": "Имя объекта (по умолчанию = projectName)"},
                "kind": {"type": "string", "enum": ["vpf", "report", "processor", "ext"], "description": "Тип разработки"},
                "client_name": {"type": "string", "description": "Имя заказчика с Kwork"},
                "order_id": {"type": "string", "description": "Номер / ID заказа с Kwork"},
                "output_dir": {"type": "string", "description": "Каталог сдачи (по умолчанию ~/Desktop/1C_Delivery/<order_id>)"},
                "git_commit": {"type": "boolean", "description": "Создать коммит в локальном git"},
                "dry_run": {"type": "boolean", "description": "Режим симуляции пакета сдачи"},
            },
            "required": ["projectName"],
        },
    },
    "read_bsl_method": {
        "name": "read_bsl_method",
        "description": "Извлекает конкретную функцию или процедуру из модуля BSL по имени с директивами компиляции и номерами строк без чтения всего файла.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "projectName": {"type": "string", "description": "Имя проекта в EDT"},
                "modulePath": {"type": "string", "description": "Путь к модулю BSL"},
                "methodName": {"type": "string", "description": "Имя функции или процедуры"},
            },
            "required": ["projectName", "modulePath", "methodName"],
        },
    },
    "bsl_syntax_help": {
        "name": "bsl_syntax_help",
        "description": "Синтакс-помощник платформы 1С:Предприятие. Защита от галлюцинаций.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    "bsl_lint_module": {
        "name": "bsl_lint_module",
        "description": "Статический анализ модуля BSL на 10 стандартов качества.",
        "inputSchema": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    },
    "get_ai_rules": {
        "name": "get_ai_rules",
        "description": "Возвращает свод правил и стандартов разработки BSL.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "compact_schema": {
        "name": "compact_schema",
        "description": "Преобразует структуру метаданных в ультра-компактный JSON.",
        "inputSchema": {
            "type": "object",
            "properties": {"metadata": {"type": "object"}},
            "required": ["metadata"],
        },
    },
    "get_order_context": {
        "name": "get_order_context",
        "description": "Возвращает полный контекст текущего заказа (ORDER_CONTEXT.md), историю сообщений, список скриншотов и файлов вложений.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "ID заказа (опционально)"},
                "order_dir": {"type": "string", "description": "Путь к папке заказа (опционально)"},
            },
        },
    },
}

PROFILE_MACROS: dict[str, list[str]] = {
    "vpf": [
        "scaffold_vpf", "build_and_check", "library_list", "library_get",
        "bootstrap_gold", "scaffold_processor", "get_critical_errors", "patch_method",
        "read_bsl_method", "complete_order", "bsl_syntax_help", "bsl_lint_module",
        "get_ai_rules", "compact_schema", "get_order_context"
    ],
    "report": [
        "scaffold_report", "build_and_check", "library_list", "library_get",
        "bootstrap_gold", "scaffold_processor", "get_critical_errors", "patch_method",
        "read_bsl_method", "complete_order", "bsl_syntax_help", "bsl_lint_module",
        "get_ai_rules", "compact_schema", "get_order_context"
    ],
    "ext": [
        "library_list", "library_get",
        "bootstrap_gold", "scaffold_processor", "get_critical_errors", "patch_method",
        "read_bsl_method", "complete_order", "bsl_syntax_help", "bsl_lint_module",
        "get_ai_rules", "compact_schema", "get_order_context"
    ],
    "full": [
        "scaffold_vpf", "scaffold_report", "scaffold_processor", "build_and_check",
        "library_list", "library_get", "bootstrap_gold", "get_critical_errors", "patch_method",
        "read_bsl_method", "complete_order", "bsl_syntax_help", "bsl_lint_module",
        "get_ai_rules", "compact_schema", "get_order_context"
    ],
}


def validate_scaffold(a: dict) -> Optional[str]:
    name = a.get("name", "")
    if not IDENT.match(name):
        return "name должен быть идентификатором 1С (буквы/цифры/_, без пробелов, не с цифры)."
    tpl = a.get("template_name", "Макет")
    if not IDENT.match(tpl):
        return "template_name должен быть идентификатором 1С."
    purposes = a.get("purposes") or []
    if not purposes or not all(isinstance(x, str) and PURPOSE.match(x) for x in purposes):
        return "purposes: непустой список вида 'Документ.Имя' / 'Справочник.Имя'."
    cols = a.get("columns") or []
    if len(cols) < 2:
        return "columns: нужно минимум 2 колонки."
    for i, c in enumerate(cols):
        if not c.get("title") or not IDENT.match(str(c.get("parameter", ""))):
            return f"columns[{i}]: нужны title и parameter (идентификатор)."
    for i, f in enumerate(a.get("header_fields") or []):
        if not f.get("label") or not IDENT.match(str(f.get("parameter", ""))):
            return f"header_fields[{i}]: нужны label и parameter (идентификатор)."
    if a.get("command_id") and not IDENT.match(a["command_id"]):
        return "command_id должен быть идентификатором 1С."
    return None


def build_template(a: dict) -> dict:
    cols = a["columns"]
    last = len(cols) - 1
    fields = a.get("header_fields") or []
    cells: list[dict] = []
    merges: list[dict] = []

    cells.append({"row": 0, "col": 0, "text": a.get("title") or a.get("command_title") or a["name"],
                  "bold": True, "fontSize": 14, "hAlign": "Center"})
    merges.append({"fromRow": 0, "fromCol": 0, "toRow": 0, "toCol": last})

    r = 2
    for f in fields:
        cells.append({"row": r, "col": 0, "text": f["label"] + ":", "bold": True})
        cells.append({"row": r, "col": 1, "parameter": f["parameter"], "wrap": True})
        if last > 1:
            merges.append({"fromRow": r, "fromCol": 1, "toRow": r, "toCol": last})
        r += 1

    hdr_row = r + 1
    data_row = hdr_row + 1
    foot_row = data_row + 1
    for c, col in enumerate(cols):
        cells.append({"row": hdr_row, "col": c, "text": col["title"], "bold": True, "hAlign": "Center"})
        cells.append({"row": data_row, "col": c, "parameter": col["parameter"]})
    cells.append({"row": foot_row, "col": last - 1, "text": "Итого:", "bold": True, "hAlign": "Right"})
    cells.append({"row": foot_row, "col": last, "parameter": "Итого", "bold": True})

    return {
        "cells": cells,
        "merges": merges,
        "areas": [
            {"name": "Шапка", "fromRow": 0, "fromCol": 0, "toRow": hdr_row, "toCol": last},
            {"name": "Строка", "fromRow": data_row, "fromCol": 0, "toRow": data_row, "toCol": last},
            {"name": "Подвал", "fromRow": foot_row, "fromCol": 0, "toRow": foot_row, "toCol": last},
        ],
        "columnWidths": [{"col": i, "width": c.get("width") or (35 if i == 1 else 12)}
                         for i, c in enumerate(cols)],
    }


def default_data_code(a: dict) -> str:
    lines = ["\t// Заполнить данные: заполнить данные из Ссылка. Поля шапки, строки таблицы и итог задаются здесь."]
    for f in a.get("header_fields") or []:
        lines.append(f'\tПараметрыШапки.Вставить("{f["parameter"]}", "");')
    lines.append('\tПараметрыПодвала.Вставить("Итого", 0);')
    return "\n".join(lines)


def build_object_module(a: dict) -> str:
    name = a["name"]
    tpl = a.get("template_name", "Макет")
    cmd_id = a.get("command_id") or name
    cmd_title = bsl(a.get("command_title") or name)
    info = bsl(a.get("info") or cmd_title)
    bsp = bsl(a.get("bsp_version") or DEFAULT_BSP_VERSION)
    purposes = "\n".join(f'\tПараметрыРегистрации.Назначение.Добавить("{bsl(p)}");' for p in a["purposes"])
    data_code = (a.get("data_code") or "").rstrip() or default_data_code(a)
    cols = ", ".join(c["parameter"] for c in a["columns"])

    return f"""#Если Сервер Или ТолстыйКлиентОбычноеПриложение Или ВнешнееСоединение Тогда

#Область СведенияОВнешнейОбработке

// Сведения для регистрации внешней обработки в БСП.
Функция СведенияОВнешнейОбработке() Экспорт

	ПараметрыРегистрации = ДополнительныеОтчетыИОбработки.СведенияОВнешнейОбработке("{bsp}");
	ПараметрыРегистрации.Вид = ДополнительныеОтчетыИОбработкиКлиентСервер.ВидОбработкиПечатнаяФорма();
	ПараметрыРегистрации.Версия = "1.0";
	ПараметрыРегистрации.БезопасныйРежим = Истина;
	ПараметрыРегистрации.Информация = НСтр("ru = '{info}'");
{purposes}

	Команда = ПараметрыРегистрации.Команды.Добавить();
	Команда.Представление = НСтр("ru = '{cmd_title}'");
	Команда.Идентификатор = "{cmd_id}";
	Команда.Использование = ДополнительныеОтчетыИОбработкиКлиентСервер.ТипКомандыВызовСерверногоМетода();
	Команда.ПоказыватьОповещение = Ложь;
	Команда.Модификатор = "ПечатьMXL";

	Возврат ПараметрыРегистрации;

КонецФункции

#КонецОбласти

#Область Печать

// Обработчик команды печати БСП.
Процедура Печать(МассивОбъектов, КоллекцияПечатныхФорм, ОбъектыПечати, ПараметрыВывода) Экспорт

	ПечатнаяФорма = УправлениеПечатью.СведенияОПечатнойФорме(КоллекцияПечатныхФорм, "{cmd_id}");
	Если ПечатнаяФорма = Неопределено Тогда
		Возврат;
	КонецЕсли;

	ПечатнаяФорма.ТабличныйДокумент = СформироватьТабличныйДокумент(МассивОбъектов, ОбъектыПечати);
	ПечатнаяФорма.СинонимМакета = НСтр("ru = '{cmd_title}'");

КонецПроцедуры

Функция СформироватьТабличныйДокумент(МассивОбъектов, ОбъектыПечати)

	ТабличныйДокумент = Новый ТабличныйДокумент;
	ТабличныйДокумент.КлючПараметровПечати = "ПАРАМЕТРЫ_ПЕЧАТИ_{name}";
	ТабличныйДокумент.ОриентацияСтраницы = ОриентацияСтраницы.Портрет;

	Макет = ПолучитьМакет("{tpl}");
	ОбластьШапка = Макет.ПолучитьОбласть("Шапка");
	ОбластьСтрока = Макет.ПолучитьОбласть("Строка");
	ОбластьПодвал = Макет.ПолучитьОбласть("Подвал");

	Первый = Истина;
	Для Каждого Ссылка Из МассивОбъектов Цикл

		Если Не Первый Тогда
			ТабличныйДокумент.ВывестиГоризонтальныйРазделительСтраниц();
		КонецЕсли;
		Первый = Ложь;

		НомерСтрокиНачало = ТабличныйДокумент.ВысотаТаблицы + 1;

		ПараметрыШапки = Новый Структура;
		СтрокиТаблицы = Новый Массив;
		ПараметрыПодвала = Новый Структура;
		ЗаполнитьДанные(Ссылка, ПараметрыШапки, СтрокиТаблицы, ПараметрыПодвала);

		ЗаполнитьЗначенияСвойств(ОбластьШапка.Параметры, ПараметрыШапки);
		ТабличныйДокумент.Вывести(ОбластьШапка);

		Для Каждого ДанныеСтроки Из СтрокиТаблицы Цикл
			ЗаполнитьЗначенияСвойств(ОбластьСтрока.Параметры, ДанныеСтроки);
			ТабличныйДокумент.Вывести(ОбластьСтрока);
		КонецЦикла;

		ЗаполнитьЗначенияСвойств(ОбластьПодвал.Параметры, ПараметрыПодвала);
		ТабличныйДокумент.Вывести(ОбластьПодвал);

		УправлениеПечатью.ЗадатьОбластьПечатиДокумента(ТабличныйДокумент, НомерСтрокиНачало, ОбъектыПечати, Ссылка);

	КонецЦикла;

	Возврат ТабличныйДокумент;

КонецФункции

// Заполняет данные печати для одного объекта.
// Колонки таблицы (ключи структуры строки): {cols}
Процедура ЗаполнитьДанные(Ссылка, ПараметрыШапки, СтрокиТаблицы, ПараметрыПодвала)

{data_code}

КонецПроцедуры

#КонецОбласти

#КонецЕсли
"""


async def macro_scaffold_vpf(a: dict) -> dict:
    err = validate_scaffold(a)
    if err:
        return tool_result("scaffold_vpf: " + err, True)
    name = a["name"]
    tpl = a.get("template_name", "Макет")
    if a.get("dry_run"):
        mod_preview = build_object_module(a)
        return tool_result(
            f"[DRY RUN] scaffold_vpf:\n"
            f"- Имя: {name}\n"
            f"- Назначение: {a.get('purposes')}\n"
            f"- Шаблон: {tpl}\n\n"
            f"Превью модуля:\n{mod_preview[:400]}..."
        )
    steps: list[str] = []

    def fail(label: str, text: str, hint: str = "") -> dict:
        steps.append(f"✗ {label}")
        return tool_result("scaffold_vpf остановлен.\n" + "\n".join(steps) + "\n" + text[:700] +
                           ("\n" + hint if hint else ""), True)

    if not a.get("skip_create_project"):
        text, bad = await UP.tool("create_project", {
            "projectKind": "externalObjects", "name": name,
            "externalObject": EXTERNAL_OBJECT_TMPL.format(name=name)})
        if bad:
            return fail("create_project", text, "Если проект уже есть — повтори с skip_create_project=true.")
        steps.append("✓ create_project")

    text, bad = await UP.tool("create_metadata", {
        "projectName": name, "fqn": TEMPLATE_FQN_TMPL.format(name=name, template=tpl),
        "properties": [{"name": "templateType", "value": "SpreadsheetDocument"}]})
    if bad:
        return fail("create_metadata (макет)", text)
    steps.append("✓ create_metadata (макет)")

    text, bad = await UP.tool("modify_metadata", {
        "projectName": name, "fqn": TEMPLATE_FQN_TMPL.format(name=name, template=tpl),
        "template": build_template(a)})
    if bad:
        return fail("modify_metadata (ячейки макета)", text)
    steps.append("✓ modify_metadata (ячейки макета)")

    snip_ids = a.get("snippet_ids") or []
    if snip_ids:
        injected_code, snip_warnings = inject_snippets_into_code(snip_ids, a.get("data_code", ""))
        a["data_code"] = injected_code
        for sw in snip_warnings:
            steps.append(f"! Библиотека: {sw}")

    module_path = MODULE_PATH_TMPL.format(name=name)
    text, bad = await UP.tool("write_module_source", {
        "projectName": name, "modulePath": module_path, "source": build_object_module(a),
        "mode": "replace", "overwrite": True})
    if bad:
        return fail("write_module_source", text,
                    f"Проверь формат modulePath в get_tool_guide(write_module_source). Сейчас: {module_path}")
    steps.append("✓ write_module_source")

    steps.append(f"Модуль: {module_path}. Дальше: build_and_check(projectName='{name}').")
    return tool_result("\n".join(steps))


async def macro_build_and_check(a: dict) -> dict:
    project = a.get("projectName", "")
    if not project:
        return tool_result("build_and_check: нужен projectName.", True)
    obj = a.get("objectName") or project
    if a.get("dry_run"):
        out_dir = a.get("outputDir") or str(Path.home() / "Desktop" / "1C_Builds")
        return tool_result(f"[DRY RUN] build_and_check: будет выполнена проверка {project} и сборка в {out_dir}")

    text, bad = await UP.tool("get_project_errors", {"projectName": project, **ERRORS_ARGS})
    if bad:
        return tool_result("get_project_errors завершился ошибкой:\n" + text[:700], True)
    has_errors = bool(re.search(r"(?i)\b(error|ошиб)", text)) and not re.search(
        r"(?i)(no\s+(?:validation\s+)?errors|ошибок\s+нет|0\s+errors|не\s+найден)", text)
    if has_errors and not a.get("force_build"):
        return tool_result("Есть ошибки, сборка не выполнялась. Исправь и повтори.\n" + text[:2500], True)

    out_dir = a.get("outputDir") or str(Path.home() / "Desktop" / "1C_Builds")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    build_args: dict[str, Any] = {"projectName": project, "objectName": obj, BUILD_OUTPUT_ARG: out_dir}
    btext, bbad = await UP.tool("build_external_objects", build_args)
    head = "Проверка: ошибок не найдено.\n" if not has_errors else "Проверка: ошибки проигнорированы (force_build).\n"
    return tool_result(head + "Сборка:\n" + btext[:1500], bbad)


async def macro_library_list(a: dict) -> dict:
    tags = a.get("tags")
    stype = a.get("snippet_type")
    res = list_snippets(tags=tags, snippet_type=stype)
    return tool_result(json.dumps(res, ensure_ascii=False, indent=2))


async def macro_library_get(a: dict) -> dict:
    sid = a.get("snippet_id", "")
    content, meta, err = get_snippet(sid)
    if err:
        return tool_result(err, True)
    assert meta is not None
    info = (
        f"// ID: {meta['id']}\n"
        f"// Название: {meta.get('title', '')}\n"
        f"// Статус: {meta.get('status', '')}\n"
        f"// БСП: {meta.get('bsp_version', '')}\n"
        f"// Заметки: {meta.get('notes', '')}\n\n"
        f"{content}"
    )
    return tool_result(info)


async def wrap_bootstrap_gold(a: dict) -> dict:
    return macro_bootstrap_gold(a)


async def wrap_scaffold_report(a: dict) -> dict:
    return await macro_scaffold_report(a, UP)


async def wrap_scaffold_processor(a: dict) -> dict:
    return await macro_scaffold_processor(a, UP)


async def wrap_get_critical_errors(a: dict) -> dict:
    return await macro_get_critical_errors(a, UP)


async def wrap_patch_method(a: dict) -> dict:
    return await macro_patch_method(a, UP)


async def wrap_complete_order(a: dict) -> dict:
    return await macro_complete_order(a, UP)


async def wrap_read_bsl_method(a: dict) -> dict:
    return await macro_read_bsl_method(a, UP)


async def wrap_bsl_syntax_help(a: dict) -> dict:
    return macro_bsl_syntax_help(a)


async def wrap_bsl_lint_module(a: dict) -> dict:
    return macro_bsl_lint_module(a)


async def wrap_get_ai_rules(a: dict) -> dict:
    return macro_get_ai_rules(a)


async def wrap_compact_schema(a: dict) -> dict:
    return macro_compact_schema(a)


async def wrap_get_order_context(a: dict) -> dict:
    return macro_get_order_context(a)


MACRO_IMPL = {
    "scaffold_vpf": macro_scaffold_vpf,
    "build_and_check": macro_build_and_check,
    "library_list": macro_library_list,
    "library_get": macro_library_get,
    "bootstrap_gold": wrap_bootstrap_gold,
    "scaffold_report": wrap_scaffold_report,
    "scaffold_processor": wrap_scaffold_processor,
    "get_critical_errors": wrap_get_critical_errors,
    "patch_method": wrap_patch_method,
    "read_bsl_method": wrap_read_bsl_method,
    "complete_order": wrap_complete_order,
    "bsl_syntax_help": wrap_bsl_syntax_help,
    "bsl_lint_module": wrap_bsl_lint_module,
    "get_ai_rules": wrap_get_ai_rules,
    "compact_schema": wrap_compact_schema,
    "get_order_context": wrap_get_order_context,
}

app = FastAPI(title="edt-guard")


def rpc_ok(mid: Any, result: dict, headers: Optional[dict[str, str]] = None) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": mid, "result": result}, headers=headers or {})


def rpc_err(mid: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "edt": EDT_URL, "profiles": list(PROFILES)}


@app.post("/admin/reset")
async def admin_reset() -> dict:
    for g in GUARDS.values():
        g.same.clear()
        g.consec = 0
    return {"reset": True}


@app.get("/mcp")
@app.get("/mcp/{profile}")
@app.get("/mcp/{profile}/{order_id}")
async def mcp_get(profile: str = "full", order_id: Optional[str] = None) -> Response:
    return Response(status_code=405)


@app.delete("/mcp")
@app.delete("/mcp/{profile}")
@app.delete("/mcp/{profile}/{order_id}")
async def mcp_delete(profile: str = "full", order_id: Optional[str] = None) -> Response:
    return Response(status_code=200)


@app.get("/admin/orders")
async def admin_list_orders(req: Request) -> Response:
    if not is_loopback(req):
        return JSONResponse({"error": "Forbidden: Loopback only (127.0.0.1)"}, status_code=403)
    return JSONResponse(await ORDER_MGR.list_orders())


@app.post("/admin/orders/{order_id}/reset")
async def admin_reset_order(order_id: str, req: Request) -> Response:
    if not is_loopback(req):
        return JSONResponse({"error": "Forbidden: Loopback only (127.0.0.1)"}, status_code=403)
    return JSONResponse(await ORDER_MGR.reset_order(order_id))


@app.post("/admin/orders/{order_id}/extend")
async def admin_extend_order(order_id: str, req: Request, calls: int = 10) -> Response:
    if not is_loopback(req):
        return JSONResponse({"error": "Forbidden: Loopback only (127.0.0.1)"}, status_code=403)
    return JSONResponse(await ORDER_MGR.extend_order(order_id, calls))


@app.post("/admin/active-workspace")
async def admin_set_active_workspace(req: Request) -> Response:
    if not is_loopback(req):
        return JSONResponse({"error": "Forbidden: Loopback only (127.0.0.1)"}, status_code=403)
    body = await req.json()
    oid = str(body.get("order_id") or "").strip() or "default"
    odir_raw = str(body.get("order_dir") or "").strip()
    ttype = str(body.get("task_type") or "").strip()
    if not odir_raw or not Path(odir_raw).is_dir():
        return JSONResponse({"ok": False, "error": "order_dir not found"}, status_code=400)
    odir = Path(odir_raw).resolve()
    ORDER_MGR.register_order_dir(oid, odir, ttype)
    os.environ["KWORK_ORDER_DIR"] = str(odir)
    if ttype:
        os.environ["KWORK_TASK_TYPE"] = ttype
    return JSONResponse({"ok": True, "order_id": oid, "order_dir": str(odir), "task_type": ttype})


@app.get("/admin/provider")
async def admin_get_provider(req: Request) -> Response:
    if not is_loopback(req):
        return JSONResponse({"error": "Forbidden: Loopback only (127.0.0.1)"}, status_code=403)
    cfg = load_config()
    active = get_active_provider(cfg)
    agy_ok, agy_url = DUAL_CLIENT.check_antigravity_health(timeout=0.6)
    return JSONResponse({
        "active_provider": active,
        "antigravity_online": agy_ok,
        "antigravity_url": agy_url,
        "providers": cfg.get("providers", {}),
        "model_routing": cfg.get("model_routing", {}),
    })


@app.post("/admin/provider")
async def admin_set_provider(req: Request) -> Response:
    if not is_loopback(req):
        return JSONResponse({"error": "Forbidden: Loopback only (127.0.0.1)"}, status_code=403)
    body = await req.json()
    target = str(body.get("provider", ""))
    ok, msg = set_active_provider(target)
    if not ok:
        return JSONResponse({"ok": False, "error": msg}, status_code=400)
    return JSONResponse({"ok": True, "active_provider": msg})


@app.post("/v1/chat/completions")
async def llm_chat_completions(req: Request) -> Response:
    if not is_loopback(req):
        return JSONResponse({"error": "Forbidden: Loopback only (127.0.0.1)"}, status_code=403)
    payload = await req.json()
    headers = dict(req.headers)
    order_id = ORDER_MGR.resolve_order_id(headers, {}, headers.get("x-order-id"))
    task_type = headers.get("x-task-type") or str(payload.get("task_type") or "bsl")
    provider_override = headers.get("x-provider") or None

    raw_messages = payload.get("messages") or []
    pruned_messages = prune_verified_bsl_history(raw_messages, VERIFIED_MODULES)
    payload["messages"] = pruned_messages

    last_text = ""
    for msg in reversed(pruned_messages):
        if msg.get("role") in ("user", "tool"):
            last_text = str(msg.get("content") or "")
            break

    lib_ctx = build_context_attachment(last_text, max_snippets=2)
    plat_ctx = PLATFORM_CTX.build_tool_context_hint(last_text, max_items=3)
    dynamic_ctx = "\n".join(part for part in (lib_ctx, plat_ctx) if part)

    try:
        resp_data, used_provider = await DUAL_CLIENT.chat_completion_async(
            payload=payload,
            task_type=task_type,
            order_id=order_id,
            dynamic_context=dynamic_ctx,
            provider_override=provider_override,
        )
        return JSONResponse(resp_data, headers={"X-Active-Provider": used_provider})
    except Exception as exc:
        LOGGER.error("Dual backend completion failed: %s", exc)
        return JSONResponse({"error": {"message": str(exc), "type": "proxy_error"}}, status_code=502)


def is_loopback(req: Request) -> bool:
    host = req.client.host if req.client else "unknown"
    return host in ("127.0.0.1", "::1", "localhost", "testclient")


@app.post("/mcp")
@app.post("/mcp/{profile}")
@app.post("/mcp/{profile}/{order_id}")
async def mcp_post(req: Request, profile: str = "full", order_id: Optional[str] = None) -> Response:
    if profile not in PROFILES:
        return rpc_err(None, -32602, f"Неизвестный профиль {profile}. Доступны: {', '.join(PROFILES)}")
    msg = await req.json()
    method, params, mid = msg.get("method"), msg.get("params") or {}, msg.get("id")

    if mid is None:
        return Response(status_code=202)

    if method == "initialize":
        sid = f"guard-{profile}-{order_id or 'default'}"
        return rpc_ok(
            mid,
            {
                "protocolVersion": params.get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": f"edt-guard/{profile}", "version": "2.0"},
            },
            headers={"Mcp-Session-Id": sid},
        )
    if method == "ping":
        return rpc_ok(mid, {})
    if method == "tools/list":
        return await handle_list(profile, mid)
    if method == "tools/call":
        return await handle_call(profile, mid, params, req, order_id)
    if method in ("resources/list", "prompts/list"):
        return rpc_ok(mid, {method.split("/")[0]: []})
    return rpc_err(mid, -32601, f"Метод не поддерживается: {method}")


async def handle_list(profile: str, mid: Any) -> JSONResponse:
    try:
        upstream_tools = await UP.list_tools()
    except httpx.HTTPError as e:
        return rpc_err(mid, -32603, f"EDT-MCP ошибка: {e}")
    allow = PROFILES[profile]
    tools = []
    for t in upstream_tools:
        if allow is not None and t["name"] not in allow:
            continue
        t = dict(t)
        d = t.get("description", "")
        if len(d) > MAX_DESC_CHARS:
            t["description"] = d[:MAX_DESC_CHARS].rstrip() + "…"
        tools.append(t)
    for m in PROFILE_MACROS.get(profile, []):
        tools.append(MACRO_TOOLS[m])
    return rpc_ok(mid, {"tools": tools})


async def handle_call(profile: str, mid: Any, params: dict, req: Request, path_order_id: Optional[str] = None) -> JSONResponse:
    name = params.get("name", "")
    raw_args = params.get("arguments")
    if isinstance(raw_args, str):
        args = parse_clean_json_arguments(raw_args)
    else:
        args = raw_args or {}
    t0 = time.time()
    allow = PROFILES[profile]
    guard = GUARDS[profile]

    req_headers = dict(req.headers)
    order_id = ORDER_MGR.resolve_order_id(req_headers, args, path_order_id)
    order_dir = ORDER_MGR.resolve_order_dir(order_id, req_headers)
    if name == "get_order_context":
        args.setdefault("order_id", order_id)
        if order_dir is not None and not args.get("order_dir"):
            args["order_dir"] = str(order_dir)
    project_name = args.get("projectName") or "common"

    def done(result: dict, note: str = "", cache_hit: bool = False, warn: Optional[str] = None) -> JSONResponse:
        result = cap_text(result, name)
        if warn and not result.get("isError"):
            for item in result.get("content", []):
                if item.get("type") == "text":
                    item["text"] = f"{warn}\n\n" + str(item.get("text", ""))
                    break
            else:
                result.setdefault("content", []).insert(0, {"type": "text", "text": warn})

        err_text = result_text(result) if result.get("isError") else ""
        log_call(profile, name, not result.get("isError", False), int((time.time() - t0) * 1000),
                 len(result_text(result)), note, order_id=order_id, cache_hit=cache_hit,
                 err_text=err_text)
        return rpc_ok(mid, result)

    is_macro = name in MACRO_IMPL and name in PROFILE_MACROS.get(profile, [])
    if not is_macro and allow is not None and name not in allow:
        return done(tool_result(f"Инструмент {name} недоступен в профиле {profile}.", True), "not_in_profile")

    if name == "write_module_source":
        src_code = str(args.get("source") or "")
        m_path = str(args.get("modulePath") or "")
        is_obj = "ObjectModule.bsl" in m_path or "МодульОбъекта" in m_path
        issues = lint_bsl_code(src_code, profile=profile, is_object_module=is_obj)
        compact_err = format_diagnostics_for_llm(issues, include_warnings=False)
        if compact_err:
            err_msg = "BSL Линтер обнаружил критические ошибки (запись заблокирована без списания бюджета):\n" + compact_err
            await ORDER_MGR.record_call(order_id, name, False, 0, blocked_reason="linter_blocked")
            return done(tool_result(err_msg, True), "linter_error")

    if name == "patch_method":
        src_code = str(args.get("new_source") or "")
        issues = lint_bsl_code(src_code, profile=profile, is_object_module=False)
        compact_err = format_diagnostics_for_llm(issues, include_warnings=False)
        if compact_err:
            err_msg = "BSL Линтер обнаружил критические ошибки в новом методе (замена заблокирована без списания бюджета):\n" + compact_err
            await ORDER_MGR.record_call(order_id, name, False, 0, blocked_reason="linter_blocked")
            return done(tool_result(err_msg, True), "linter_error")

    blocked, budget_msg = await ORDER_MGR.check_budget(order_id, name)
    if blocked:
        await ORDER_MGR.record_call(order_id, name, False, 0, blocked_reason="budget_exceeded")
        return done(tool_result(budget_msg or "Бюджет заказа исчерпан.", True), "budget_blocked")

    cached_result = CACHE.get(project_name, name, args, order_dir=order_dir)
    if cached_result is not None:
        return done(cached_result, note="cache_hit", cache_hit=True)

    if (stop := guard.check(name, args)):
        await ORDER_MGR.record_call(order_id, name, False, 0, blocked_reason="loop_block")
        return done(tool_result(stop, True), "loop_block")

    if (err := prevalidate(name, args)):
        guard.record(name, args, True)
        await ORDER_MGR.record_call(order_id, name, False, len(err), blocked_reason="prevalidate")
        return done(tool_result(err, True), "prevalidate")

    if get_active_provider() == "antigravity" and isinstance(args, dict):
        cleaned_args = strip_empty_schema_fields(args)
        if isinstance(cleaned_args, dict):
            args = cleaned_args

    if is_macro:
        result = await MACRO_IMPL[name](args)
        failed = bool(result.get("isError"))
        guard.record(name, args, failed)
        resp_chars = len(result_text(result))
        await ORDER_MGR.record_call(order_id, name, not failed, resp_chars)
        if not failed and is_write_tool(name):
            hint = args.get("objectName") or args.get("name") or args.get("modulePath") or args.get("methodName")
            CACHE.invalidate_object(project_name, hint, order_dir=order_dir)
            if name == "patch_method" and args.get("modulePath"):
                VERIFIED_MODULES.add(str(args.get("modulePath")))
        return done(result, "macro", warn=budget_msg)

    try:
        resp = await UP.rpc("tools/call", {"name": name, "arguments": args})
    except httpx.HTTPError as e:
        await ORDER_MGR.record_call(order_id, name, False, 0, blocked_reason="upstream_error")
        return done(tool_result(f"EDT-MCP недоступен: {e}. Проверь, что EDT запущен и сервер включён.", True), "upstream")

    failed = is_error(resp)
    guard.record(name, args, failed)
    if resp.get("error"):
        err_msg = str(resp["error"].get("message", resp["error"]))
        await ORDER_MGR.record_call(order_id, name, False, len(err_msg))
        return done(tool_result(err_msg, True), "rpc_error")

    res = resp.get("result", {})
    resp_chars = len(result_text(res))
    await ORDER_MGR.record_call(order_id, name, not failed, resp_chars)

    if not failed:
        if is_write_tool(name):
            hint = args.get("objectName") or args.get("name") or args.get("modulePath") or args.get("methodName")
            CACHE.invalidate_object(project_name, hint, order_dir=order_dir)
            if name in ("write_module_source", "patch_method") and args.get("modulePath"):
                VERIFIED_MODULES.add(str(args.get("modulePath")))
        elif is_cacheable_read(name):
            CACHE.put(project_name, name, args, res, order_dir=order_dir)

    return done(res, warn=budget_msg)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)
