from __future__ import annotations

from guard.bsl_linter import format_diagnostics_for_llm, lint_bsl_code
from guard.fs_utils import write_bsl_with_bom
from guard.compact_schema import compact_metadata_schema, format_schema_summary
from guard.platform_context import PlatformContextService

import datetime
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("kwork-guard.macros")

GOLD_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "gold_templates"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
IDENT = re.compile(r"^[A-Za-zА-Яа-я_][A-Za-zА-Яа-я0-9_]*$")
DEFAULT_BSP_VERSION = "2.2.2.1"


def bsl_escape(s: str) -> str:
    return str(s).replace('"', '""').replace("'", "’")


def tool_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def macro_bootstrap_gold(a: dict) -> dict:
    tpl_name = a.get("template_name", "")
    target_dir_str = a.get("target_dir", "")
    new_name = a.get("new_name", "")
    dry_run = bool(a.get("dry_run", False))

    if not tpl_name or not target_dir_str or not new_name:
        return tool_result("bootstrap_gold: требуются template_name, target_dir и new_name.", True)

    if not IDENT.match(new_name):
        return tool_result(f"bootstrap_gold: new_name '{new_name}' должен быть идентификатором 1С.", True)

    src_dir = GOLD_TEMPLATES_DIR / tpl_name
    if not src_dir.is_dir():
        available = [d.name for d in GOLD_TEMPLATES_DIR.iterdir() if d.is_dir()]
        return tool_result(f"bootstrap_gold: шаблон '{tpl_name}' не найден. Доступные: {available}", True)

    target_dir = Path(target_dir_str)

    if dry_run:
        return tool_result(
            f"[DRY RUN] bootstrap_gold:\n"
            f"- Источник: {src_dir}\n"
            f"- Назначение: {target_dir}\n"
            f"- Новое имя: {new_name}\n"
            f"Будет скопировано без расхода токенов вызовов MCP."
        )

    if target_dir.exists() and any(target_dir.iterdir()):
        return tool_result(f"bootstrap_gold: целевой каталог '{target_dir}' не пуст.", True)

    target_dir.mkdir(parents=True, exist_ok=True)

    if "vpf" in tpl_name:
        old_name = "ШаблонВПФ"
    elif "ext" in tpl_name:
        old_name = "ШаблонРасширения"
    else:
        old_name = "ШаблонОтчета"

    copied_files = 0
    for item in src_dir.rglob("*"):
        rel = item.relative_to(src_dir)
        parts = [part.replace(old_name, new_name) for part in rel.parts]
        dst_path = target_dir.joinpath(*parts)

        if item.is_dir():
            dst_path.mkdir(parents=True, exist_ok=True)
        else:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                text_val = item.read_text(encoding="utf-8-sig")
                text_val = text_val.replace(old_name, new_name)
                if dst_path.suffix.lower() == ".bsl":
                    write_bsl_with_bom(dst_path, text_val)
                else:
                    dst_path.write_text(text_val, encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copy2(item, dst_path)
            copied_files += 1

    return tool_result(
        f"✓ Проект '{new_name}' успешно создан из эталона '{tpl_name}' в {target_dir}.\n"
        f"Скопировано файлов: {copied_files}. Готов к импорту в EDT через import_project_from_file."
    )


def build_report_module(a: dict) -> str:
    name = a["name"]
    title = bsl_escape(a.get("title") or name)
    bsp_ver = bsl_escape(a.get("bsp_version") or DEFAULT_BSP_VERSION)

    return f"""#Если Сервер Или ТолстыйКлиентОбычноеПриложение Или ВнешнееСоединение Тогда

#Область ПрограммныйИнтерфейс

Функция СведенияОВнешнейОбработке() Экспорт

	ПараметрыРегистрации = Новый Структура;
	ПараметрыРегистрации.Вставить("Вид", "ДополнительныйОтчет");
	ПараметрыРегистрации.Вставить("Версия", "{bsp_ver}");
	ПараметрыРегистрации.Вставить("БезопасныйРежим", Ложь);
	ПараметрыРегистрации.Вставить("Информация", НСтр("ru = '{title}'"));

	Команды = Новый ТаблицаЗначений;
	Команды.Колонки.Добавить("Идентификатор", Новый ОписаниеТипов("Строка"));
	Команды.Колонки.Добавить("Представление", Новый ОписаниеТипов("Строка"));
	Команды.Колонки.Добавить("Использование", Новый ОписаниеТипов("Строка"));
	Команды.Колонки.Добавить("ПоказыватьОповещение", Новый ОписаниеТипов("Булево"));
	Команды.Колонки.Добавить("Модификатор", Новый ОписаниеТипов("Строка"));

	СтрокаКоманды = Команды.Добавить();
	СтрокаКоманды.Идентификатор = "{name}";
	СтрокаКоманды.Представление = НСтр("ru = '{title}'");
	СтрокаКоманды.Использование = "ОткрытиеФормы";
	СтрокаКоманды.ПоказыватьОповещение = Ложь;

	ПараметрыРегистрации.Вставить("Команды", Команды);
	Возврат ПараметрыРегистрации;

КонецФункции

#КонецОбласти

#КонецЕсли
"""


async def macro_scaffold_report(a: dict, upstream) -> dict:
    name = a.get("name", "")
    query = a.get("query", "")
    dry_run = bool(a.get("dry_run", False))

    if not IDENT.match(name):
        return tool_result("scaffold_report: name должен быть идентификатором 1С.", True)
    if not query.strip():
        return tool_result("scaffold_report: query не может быть пустым.", True)

    if dry_run:
        mod_preview = build_report_module(a)
        return tool_result(
            f"[DRY RUN] scaffold_report:\n"
            f"- Имя отчета: {name}\n"
            f"- Запрос: {query[:120]}...\n"
            f"- Будет выполнен validate_query, create_project, create_metadata, dcs, write_module_source.\n\n"
            f"Превью модуля:\n{mod_preview[:400]}..."
        )

    steps: list[str] = []

    def fail(label: str, text: str) -> dict:
        steps.append(f"✗ {label}")
        return tool_result(f"scaffold_report остановлен.\n" + "\n".join(steps) + "\n" + text[:700], True)

    text, bad = await upstream.tool("validate_query", {"query": query})
    if bad:
        return fail("validate_query", text)
    steps.append("✓ validate_query")

    if not a.get("skip_create_project"):
        text, bad = await upstream.tool("create_project", {
            "projectKind": "externalObjects",
            "name": name,
            "externalObject": f"ExternalReport.{name}"
        })
        if bad:
            return fail("create_project", text)
        steps.append("✓ create_project")

    text, bad = await upstream.tool("create_metadata", {
        "projectName": name,
        "fqn": f"ExternalReport.{name}.Template.ОсновнаяСхемаКомпоновкиДанных",
        "properties": [{"name": "templateType", "value": "DataCompositionSchema"}]
    })
    if bad:
        return fail("create_metadata (СКД)", text)
    steps.append("✓ create_metadata (СКД)")

    text, bad = await upstream.tool("dcs", {
        "projectName": name,
        "schema": f"ExternalReport.{name}.Template.ОсновнаяСхемаКомпоновкиДанных",
        "query": query
    })
    if bad:
        return fail("dcs (настройка схемы)", text)
    steps.append("✓ dcs (настройка схемы)")

    module_path = f"ExternalReports/{name}/ObjectModule.bsl"
    text, bad = await upstream.tool("write_module_source", {
        "projectName": name,
        "modulePath": module_path,
        "source": build_report_module(a),
        "mode": "replace",
        "overwrite": True
    })
    if bad:
        return fail("write_module_source", text)
    steps.append("✓ write_module_source")

    steps.append(f"Отчет {name} готов. Дальше: build_and_check(projectName='{name}').")
    return tool_result("\n".join(steps))


def build_processor_module(a: dict) -> str:
    name = a["name"]
    title = bsl_escape(a.get("command_title") or name)
    action_code = a.get("action_code") or "    // Действие обработки"
    bsp_ver = bsl_escape(a.get("bsp_version") or DEFAULT_BSP_VERSION)

    return f"""#Если Сервер Или ТолстыйКлиентОбычноеПриложение Или ВнешнееСоединение Тогда

#Область ПрограммныйИнтерфейс

Функция СведенияОВнешнейОбработке() Экспорт

	ПараметрыРегистрации = Новый Структура;
	ПараметрыРегистрации.Вставить("Вид", "ДополнительнаяОбработка");
	ПараметрыРегистрации.Вставить("Версия", "{bsp_ver}");
	ПараметрыРегистрации.Вставить("БезопасныйРежим", Ложь);
	ПараметрыРегистрации.Вставить("Информация", НСтр("ru = '{title}'"));

	Команды = Новый ТаблицаЗначений;
	Команды.Колонки.Добавить("Идентификатор", Новый ОписаниеТипов("Строка"));
	Команды.Колонки.Добавить("Представление", Новый ОписаниеТипов("Строка"));
	Команды.Колонки.Добавить("Использование", Новый ОписаниеТипов("Строка"));
	Команды.Колонки.Добавить("ПоказыватьОповещение", Новый ОписаниеТипов("Булево"));
	Команды.Колонки.Добавить("Модификатор", Новый ОписаниеТипов("Строка"));

	СтрокаКоманды = Команды.Добавить();
	СтрокаКоманды.Идентификатор = "{name}";
	СтрокаКоманды.Представление = НСтр("ru = '{title}'");
	СтрокаКоманды.Использование = "ВызовСерверногоМетода";
	СтрокаКоманды.ПоказыватьОповещение = Истина;

	ПараметрыРегистрации.Вставить("Команды", Команды);
	Возврат ПараметрыРегистрации;

КонецФункции

Процедура ВыполнитьКоманду(ИдентификаторКоманды, ПараметрыВыполнения) Экспорт

	Если ИдентификаторКоманды = "{name}" Тогда
{action_code}
	КонецЕсли;

КонецПроцедуры

#КонецОбласти

#КонецЕсли
"""


async def macro_scaffold_processor(a: dict, upstream) -> dict:
    name = a.get("name", "")
    dry_run = bool(a.get("dry_run", False))

    if not IDENT.match(name):
        return tool_result("scaffold_processor: name должен быть идентификатором 1С.", True)

    if dry_run:
        mod_preview = build_processor_module(a)
        return tool_result(
            f"[DRY RUN] scaffold_processor:\n"
            f"- Имя обработки: {name}\n"
            f"- Будет выполнен create_project и write_module_source.\n\n"
            f"Превью модуля:\n{mod_preview[:400]}..."
        )

    steps: list[str] = []

    def fail(label: str, text: str) -> dict:
        steps.append(f"✗ {label}")
        return tool_result(f"scaffold_processor остановлен.\n" + "\n".join(steps) + "\n" + text[:700], True)

    if not a.get("skip_create_project"):
        text, bad = await upstream.tool("create_project", {
            "projectKind": "externalObjects",
            "name": name,
            "externalObject": f"ExternalDataProcessor.{name}"
        })
        if bad:
            return fail("create_project", text)
        steps.append("✓ create_project")

    module_path = f"ExternalDataProcessors/{name}/ObjectModule.bsl"
    text, bad = await upstream.tool("write_module_source", {
        "projectName": name,
        "modulePath": module_path,
        "source": build_processor_module(a),
        "mode": "replace",
        "overwrite": True
    })
    if bad:
        return fail("write_module_source", text)
    steps.append("✓ write_module_source")

    steps.append(f"Обработка {name} готова. Дальше: build_and_check(projectName='{name}').")
    return tool_result("\n".join(steps))


async def macro_get_critical_errors(a: dict, upstream) -> dict:
    project = a.get("projectName", "")
    limit = int(a.get("limit", 10))
    if not project:
        return tool_result("get_critical_errors: требуется projectName.", True)

    text, bad = await upstream.tool("get_project_errors", {"projectName": project, "severity": "ERRORS", "limit": 50})
    if bad:
        return tool_result(f"get_critical_errors: ошибка получения списка:\n{text[:500]}", True)

    if re.search(r"(?i)(no\s+(?:validation\s+)?errors|ошибок\s+нет|0\s+errors|не\s+найден)", text):
        return tool_result(f"✓ В проекте '{project}' критических ошибок не обнаружено.")

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    critical_lines = []
    for line in lines:
        if re.search(r"(?i)(warning|info|предупреждение|замечание)", line):
            continue
        if re.search(r"(?i)(error|ошиб|syntax|неизвестн|не определен)", line):
            critical_lines.append(line)

    if not critical_lines:
        critical_lines = lines[:limit]
    else:
        critical_lines = critical_lines[:limit]

    total_count = len(critical_lines)
    summary = (
        f"Обнаружено критических ошибок ({total_count}):\n" +
        "\n".join(f"• {l}" for l in critical_lines)
    )
    return tool_result(summary, is_error=True)


METHOD_REGEX = re.compile(
    r"(?P<prefix>(?:Экспортный\s+)?(?:Процедура|Функция)\s+(?P<name>[A-Za-zА-Яа-я0-9_]+)\s*\([^)]*\).*?Конец(?:Процедуры|Функции))",
    re.DOTALL | re.IGNORECASE
)


async def macro_patch_method(a: dict, upstream) -> dict:
    project = a.get("projectName", "")
    module_path = a.get("modulePath", "")
    method_name = a.get("methodName", "")
    new_source = a.get("new_source", "")
    wrap_mode = a.get("wrap_mode", "replace")
    dry_run = bool(a.get("dry_run", False))

    if not project or not module_path or not method_name or not new_source:
        return tool_result("patch_method: требуются projectName, modulePath, methodName и new_source.", True)

    text, bad = await upstream.tool("read_module_source", {"projectName": project, "modulePath": module_path})
    if bad:
        return tool_result(f"patch_method: не удалось прочитать модуль {module_path}:\n{text[:500]}", True)

    pattern = re.compile(
        rf"((?:Экспортный\s+)?(?:Процедура|Функция)\s+{re.escape(method_name)}\s*\([^)]*\).*?Конец(?:Процедуры|Функции))",
        re.DOTALL | re.IGNORECASE
    )
    match = pattern.search(text)
    if not match:
        found_methods = [m.group("name") for m in METHOD_REGEX.finditer(text)]
        return tool_result(
            f"patch_method: метод '{method_name}' не найден в {module_path}.\n"
            f"Найденные методы: {found_methods}", True
        )

    old_method_code = match.group(1)

    if wrap_mode == "replace":
        updated_code = text[:match.start()] + new_source.strip() + text[match.end():]
    elif wrap_mode == "before":
        updated_code = text[:match.start()] + new_source.strip() + "\n\n" + old_method_code + text[match.end():]
    elif wrap_mode == "after":
        updated_code = text[:match.start()] + old_method_code + "\n\n" + new_source.strip() + text[match.end():]
    else:
        return tool_result(f"patch_method: неизвестный wrap_mode '{wrap_mode}'. Допустимы: replace, before, after.", True)

    if dry_run:
        return tool_result(
            f"[DRY RUN] patch_method:\n"
            f"- Модуль: {module_path}\n"
            f"- Метод: {method_name} ({wrap_mode})\n"
            f"- Старый размер: {len(old_method_code)} симв. Новый размер: {len(new_source)} симв.\n\n"
            f"Превью замены:\n{new_source[:300]}..."
        )

    wtext, wbad = await upstream.tool("write_module_source", {
        "projectName": project,
        "modulePath": module_path,
        "source": updated_code,
        "mode": "replace",
        "overwrite": True
    })
    if wbad:
        return tool_result(f"patch_method: ошибка записи модуля:\n{wtext[:500]}", True)

    return tool_result(f"✓ Метод '{method_name}' в {module_path} успешно обновлен ({wrap_mode}).")


async def macro_read_bsl_method(a: dict, upstream) -> dict:
    project = a.get("projectName", "")
    module_path = a.get("modulePath", "")
    method_name = a.get("methodName", "")

    if not project or not module_path or not method_name:
        return tool_result("read_bsl_method: требуются projectName, modulePath и methodName.", True)

    text, bad = await upstream.tool("read_module_source", {"projectName": project, "modulePath": module_path})
    if bad:
        return tool_result(f"read_bsl_method: ошибка чтения модуля {module_path}:\n{text[:500]}", True)

    pattern = re.compile(
        rf"(?ims)^([\t ]*(?:&[А-Яа-яA-Za-z0-9_]+\s*)*^[\t ]*(?:Процедура|Функция)\s+{re.escape(method_name)}\s*\(.*?^[\t ]*Конец(?:Процедуры|Функции))",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return tool_result(f"read_bsl_method: метод '{method_name}' не найден в модуле '{module_path}'.", True)

    method_code = match.group(1)
    start_char = match.start(1)
    start_line = text[:start_char].count("\n") + 1
    end_line = start_line + method_code.count("\n")

    header = f"// Метод: {method_name} ({module_path}, строки {start_line}-{end_line})\n"
    return tool_result(header + method_code)


async def macro_complete_order(a: dict, upstream) -> dict:
    project = a.get("projectName", "")
    if not project:
        return tool_result("complete_order: требуется projectName.", True)

    obj_name = a.get("object_name") or project
    kind = a.get("kind", "vpf")
    client_name = a.get("client_name") or "Уважаемый заказчик"
    order_id = a.get("order_id") or "order"
    out_dir_str = a.get("output_dir") or str(Path.home() / "Desktop" / "1C_Delivery" / order_id)
    out_dir = Path(out_dir_str)
    git_commit = bool(a.get("git_commit", True))
    dry_run = bool(a.get("dry_run", False))

    if dry_run:
        return tool_result(
            f"[DRY RUN] complete_order:\n"
            f"- Проект: {project} (объект: {obj_name})\n"
            f"- Заказчик: {client_name} (Заказ №{order_id})\n"
            f"- Папка сдачи: {out_dir}\n"
            f"- Будет проверен на ошибки через get_project_errors\n"
            f"- Будет скомпилирован через build_external_objects\n"
            f"- Будут созданы: ИНСТРУКЦИЯ_ДЛЯ_КЛИЕНТА.md, СООБЩЕНИЕ_ДЛЯ_KWORK.txt, ЧЕК_ЛИСТ_ПРОВЕРКИ.md\n"
            f"- Git commit: {'Да' if git_commit else 'Нет'}"
        )

    steps: list[str] = []

    text, bad = await upstream.tool("get_project_errors", {"projectName": project, "severity": "ERRORS", "limit": 20})
    if bad:
        return tool_result(f"complete_order: ошибка при вызове get_project_errors:\n{text[:500]}", True)
    has_errors = bool(re.search(r"(?i)\b(error|ошиб)", text)) and not re.search(
        r"(?i)(no\s+(?:validation\s+)?errors|ошибок\s+нет|0\s+errors|не\s+найден)", text
    )
    if has_errors:
        return tool_result(f"complete_order: в проекте обнаружены ошибки компиляции, сдача отменена:\n{text[:1500]}", True)
    steps.append("✓ Проверка: ошибок нет")

    out_dir.mkdir(parents=True, exist_ok=True)
    btext, bbad = await upstream.tool("build_external_objects", {
        "projectName": project,
        "objectName": obj_name,
        "outputDir": str(out_dir)
    })
    if bbad:
        return tool_result(f"complete_order: сборка не удалась:\n{btext[:500]}", True)
    steps.append(f"✓ Сборка: файлы выгружены в {out_dir}")

    context = {
        "object_name": obj_name,
        "client_name": client_name,
        "order_id": order_id,
        "kind": kind,
    }

    doc_files = [
        ("instructions_client.md", "ИНСТРУКЦИЯ_ДЛЯ_КЛИЕНТА.md"),
        ("message_kwork.md", "СООБЩЕНИЕ_ДЛЯ_KWORK.txt"),
        ("checklist_manual.md", "ЧЕК_ЛИСТ_ПРОВЕРКИ.md"),
    ]

    for tmpl_name, target_name in doc_files:
        tmpl_file = TEMPLATES_DIR / tmpl_name
        if not tmpl_file.is_file():
            continue
        content = tmpl_file.read_text(encoding="utf-8")
        for k, v in context.items():
            content = content.replace(f"{{{{{k}}}}}", str(v))
        unresolved = re.findall(r"\{\{([^}]+)\}\}", content)
        if unresolved:
            return tool_result(f"complete_order: в шаблоне {tmpl_name} остались незаполненные плейсхолдеры: {unresolved}", True)
        (out_dir / target_name).write_text(content, encoding="utf-8")
        steps.append(f"✓ Документ: {target_name}")

    if git_commit:
        try:
            res_stat = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5
            )
            if res_stat.returncode == 0:
                subprocess.run(["git", "add", "."], capture_output=True, text=True, timeout=5)
                msg = f"feat(delivery): заказ {order_id} ({obj_name}) подготовлен к сдаче"
                res_c = subprocess.run(["git", "commit", "-m", msg], capture_output=True, text=True, timeout=5)
                if res_c.returncode == 0:
                    today_str = datetime.date.today().isoformat()
                    tag_name = f"delivered-{today_str}"
                    subprocess.run(["git", "tag", "-a", tag_name, "-m", f"Релиз {tag_name}"], capture_output=True, text=True, timeout=5)
                    steps.append(f"✓ Git: закоммичено и установлен тег {tag_name}")
                else:
                    steps.append("• Git: нет изменений для коммита")
            else:
                steps.append("• Git: пропущено (каталог не является репозиторием Git)")
        except Exception as e:
            steps.append(f"• Git: пропущено ({e})")

    for check_dir in [out_dir, out_dir.parent, Path.cwd()]:
        oj = check_dir / "order.json"
        if oj.is_file():
            try:
                data = json.loads(oj.read_text(encoding="utf-8"))
                data["status"] = "delivered"
                data["delivered_at"] = datetime.date.today().isoformat()
                oj.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                steps.append("✓ order.json: статус обновлен на 'delivered'")
                break
            except Exception as update_exc:
                logger.warning("Order json update failed during completion: %s", update_exc)

    summary = (
        f"★ Пакет заказа {order_id} ({obj_name}) успешно сформирован!\n" +
        "\n".join(steps) + "\n\n" +
        f"Готовые файлы находятся в: {out_dir}"
    )
    return tool_result(summary)

def macro_bsl_syntax_help(a: dict) -> dict:
    svc = PlatformContextService()
    query = a.get("query", "")
    text = svc.format_syntax_help(query)
    return tool_result(text)


def macro_bsl_lint_module(a: dict) -> dict:
    code = a.get("code", "")
    profile = a.get("profile", "common")
    is_obj = bool(a.get("is_object_module", False))
    issues = lint_bsl_code(code, profile=profile, is_object_module=is_obj)
    formatted = format_diagnostics_for_llm(issues, include_warnings=True)
    if not formatted:
        return tool_result("Статический анализ BSL пройден: 0 ошибок и замечаний.")
    return tool_result(formatted, is_error=any(i.severity == "ERROR" for i in issues))


def macro_get_ai_rules(a: dict) -> dict:
    rules_file = TEMPLATES_DIR / "bsl_ai_rules.md"
    if rules_file.is_file():
        return tool_result(rules_file.read_text(encoding="utf-8"))
    return tool_result("Файл bsl_ai_rules.md не найден.", True)


def macro_compact_schema(a: dict) -> dict:
    raw = a.get("metadata", {})
    if not isinstance(raw, dict):
        return tool_result("compact_schema: параметр metadata должен быть словарем (JSON).", True)
    compacted = compact_metadata_schema(raw)
    summary = format_schema_summary(compacted)
    return tool_result(summary)


def macro_get_order_context(a: dict) -> dict:
    raw_dir = str(a.get("order_dir") or os.getenv("KWORK_ORDER_DIR", "") or "").strip()
    order_dir = Path(raw_dir) if raw_dir else None

    if order_dir is None or not order_dir.is_dir():
        ws_file = Path(__file__).resolve().parent.parent / "state" / "active_workspace.json"
        if ws_file.is_file():
            try:
                ws_data = json.loads(ws_file.read_text(encoding="utf-8"))
                oid = str(a.get("order_id") or "").strip()
                mapped = (ws_data.get("orders") or {}).get(oid) if oid else ws_data.get("active_order_dir")
                if mapped and Path(str(mapped)).is_dir():
                    order_dir = Path(str(mapped))
            except Exception as exc:
                logger.debug("Workspace fallback read warning: %s", exc)

    if order_dir is None or not order_dir.is_dir():
        return tool_result(
            "get_order_context: папка активного заказа не определена. Передай order_dir или задай KWORK_ORDER_DIR.",
            True,
        )

    context_file = order_dir / "ORDER_CONTEXT.md"
    context_text = context_file.read_text(encoding="utf-8", errors="replace") if context_file.is_file() else ""
    if not context_text:
        order_file = order_dir / "order.json"
        brief_file = order_dir / "brief.md"
        parts = []
        if order_file.is_file():
            parts.append(order_file.read_text(encoding="utf-8", errors="replace"))
        if brief_file.is_file():
            parts.append(brief_file.read_text(encoding="utf-8", errors="replace"))
        context_text = "\n\n".join(parts)

    manifest_data: dict[str, Any] = {}
    manifest_file = order_dir / "attachments_manifest.json"
    if manifest_file.is_file():
        try:
            manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("Manifest parse warning in get_order_context: %s", exc)

    grouped_files: dict[str, list[str]] = {"screenshots": [], "1c_files": [], "documents": []}
    att_root = order_dir / "attachments"
    if att_root.is_dir():
        for cat in ("screenshots", "1c_files", "documents"):
            sub = att_root / cat
            if sub.is_dir():
                grouped_files[cat] = [f"attachments/{cat}/{p.name}" for p in sorted(sub.iterdir()) if p.is_file()]

    payload = {
        "order_dir": str(order_dir),
        "task_type": os.getenv("KWORK_TASK_TYPE", ""),
        "attachments": grouped_files,
        "manifest_files_count": len(manifest_data.get("files") or []),
    }
    response_text = (
        f"{context_text.strip()}\n\n"
        f"## Метаданные вложений (JSON)\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )
    return tool_result(response_text)


