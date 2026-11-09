"""guard/macros.py - Пакет макросов экстремальной экономии токенов (WP2)."""
from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

GOLD_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "gold_templates"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
IDENT = re.compile(r"^[A-Za-zА-Яа-я_][A-Za-zА-Яа-я0-9_]*$")
DEFAULT_BSP_VERSION = "2.2.2.1"


def bsl_escape(s: str) -> str:
    return str(s).replace('"', '""').replace("'", "’")


def tool_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


# --------------------------------------------------------------------------- #
# 1. bootstrap_gold
# --------------------------------------------------------------------------- #
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

    # Старое базовое имя шаблона
    old_name = "ШаблонВПФ" if "vpf" in tpl_name else "ШаблонОтчета"

    # Копируем дерево файлов с подстановкой
    copied_files = 0
    for item in src_dir.rglob("*"):
        rel = item.relative_to(src_dir)
        # Подменяем имя в пути
        parts = [p.replace(old_name, new_name) for p in rel.parts]
        dst_path = target_dir.joinpath(*parts)

        if item.is_dir():
            dst_path.mkdir(parents=True, exist_ok=True)
        else:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                text = item.read_text(encoding="utf-8")
                # Подменяем имя в содержимом
                text = text.replace(old_name, new_name)
                dst_path.write_text(text, encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copy2(item, dst_path)
            copied_files += 1

    return tool_result(
        f"✓ Проект '{new_name}' успешно создан из эталона '{tpl_name}' в {target_dir}.\n"
        f"Скопировано файлов: {copied_files}. Готов к импорту в EDT через import_project_from_file."
    )


# --------------------------------------------------------------------------- #
# 2. scaffold_report
# --------------------------------------------------------------------------- #
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

    # 1. Валидация запроса
    text, bad = await upstream.tool("validate_query", {"query": query})
    if bad:
        return fail("validate_query", text)
    steps.append("✓ validate_query")

    # 2. Создание проекта
    if not a.get("skip_create_project"):
        text, bad = await upstream.tool("create_project", {
            "projectKind": "externalObjects",
            "name": name,
            "externalObject": f"ExternalReport.{name}"
        })
        if bad:
            return fail("create_project", text)
        steps.append("✓ create_project")

    # 3. Создание макета СКД
    text, bad = await upstream.tool("create_metadata", {
        "projectName": name,
        "fqn": f"ExternalReport.{name}.Template.ОсновнаяСхемаКомпоновкиДанных",
        "properties": [{"name": "templateType", "value": "DataCompositionSchema"}]
    })
    if bad:
        return fail("create_metadata (СКД)", text)
    steps.append("✓ create_metadata (СКД)")

    # 4. Настройка схемы СКД
    text, bad = await upstream.tool("dcs", {
        "projectName": name,
        "schema": f"ExternalReport.{name}.Template.ОсновнаяСхемаКомпоновкиДанных",
        "query": query
    })
    if bad:
        return fail("dcs (настройка схемы)", text)
    steps.append("✓ dcs (настройка схемы)")

    # 5. Запись модуля объекта
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


# --------------------------------------------------------------------------- #
# 3. scaffold_processor
# --------------------------------------------------------------------------- #
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

    # 1. Создание проекта
    if not a.get("skip_create_project"):
        text, bad = await upstream.tool("create_project", {
            "projectKind": "externalObjects",
            "name": name,
            "externalObject": f"ExternalDataProcessor.{name}"
        })
        if bad:
            return fail("create_project", text)
        steps.append("✓ create_project")

    # 2. Запись модуля объекта
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


# --------------------------------------------------------------------------- #
# 4. get_critical_errors
# --------------------------------------------------------------------------- #
async def macro_get_critical_errors(a: dict, upstream) -> dict:
    project = a.get("projectName", "")
    limit = int(a.get("limit", 10))
    if not project:
        return tool_result("get_critical_errors: требуется projectName.", True)

    text, bad = await upstream.tool("get_project_errors", {"projectName": project, "severity": "ERRORS", "limit": 50})
    if bad:
        return tool_result(f"get_critical_errors: ошибка получения списка:\n{text[:500]}", True)

    # Проверка отсутствия ошибок
    if re.search(r"(?i)(no\s+(?:validation\s+)?errors|ошибок\s+нет|0\s+errors|не\s+найден)", text):
        return tool_result(f"✓ В проекте '{project}' критических ошибок не обнаружено.")

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    critical_lines = []
    for line in lines:
        # Пропускаем информационный мусор и стиль
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


# --------------------------------------------------------------------------- #
# 5. patch_method
# --------------------------------------------------------------------------- #
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

    # Читаем исходный код модуля
    text, bad = await upstream.tool("read_module_source", {"projectName": project, "modulePath": module_path})
    if bad:
        return tool_result(f"patch_method: не удалось прочитать модуль {module_path}:\n{text[:500]}", True)

    # Ищем метод
    pattern = re.compile(
        rf"((?:Экспортный\s+)?(?:Процедура|Функция)\s+{re.escape(method_name)}\s*\([^)]*\).*?Конец(?:Процедуры|Функции))",
        re.DOTALL | re.IGNORECASE
    )
    match = pattern.search(text)
    if not match:
        # Найдем все методы для подсказки
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


# --------------------------------------------------------------------------- #
# 6. complete_order
# --------------------------------------------------------------------------- #
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

    # 1. Проверка ошибок
    text, bad = await upstream.tool("get_project_errors", {"projectName": project, "severity": "ERRORS", "limit": 20})
    if bad:
        return tool_result(f"complete_order: ошибка при вызове get_project_errors:\n{text[:500]}", True)
    has_errors = bool(re.search(r"(?i)\b(error|ошиб)", text)) and not re.search(
        r"(?i)(no\s+(?:validation\s+)?errors|ошибок\s+нет|0\s+errors|не\s+найден)", text
    )
    if has_errors:
        return tool_result(f"complete_order: в проекте обнаружены ошибки компиляции, сдача отменена:\n{text[:1500]}", True)
    steps.append("✓ Проверка: ошибок нет")

    # 2. Сборка объекта
    out_dir.mkdir(parents=True, exist_ok=True)
    btext, bbad = await upstream.tool("build_external_objects", {
        "projectName": project,
        "objectName": obj_name,
        "outputDir": str(out_dir)
    })
    if bbad:
        return tool_result(f"complete_order: сборка не удалась:\n{btext[:500]}", True)
    steps.append(f"✓ Сборка: файлы выгружены в {out_dir}")

    # 3. Формирование пакета документов
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

    # 4. Git commit
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

    # 5. Обновление order.json при наличии
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
            except Exception:
                pass

    summary = (
        f"★ Пакет заказа {order_id} ({obj_name}) успешно сформирован!\n" +
        "\n".join(steps) + "\n\n" +
        f"Готовые файлы находятся в: {out_dir}"
    )
    return tool_result(summary)
