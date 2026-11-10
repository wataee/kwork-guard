from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("kwork-guard.compact-schema")

IDENT_PATTERN = re.compile(r"^[A-Za-zА-Яа-я_][A-Za-zА-Яа-я0-9_]*$")


def sanitize_ident(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-zА-Яа-я0-9_]", "", name.strip())
    if not cleaned or not IDENT_PATTERN.match(cleaned):
        return f"Элемент_{abs(hash(name)) % 10000}"
    return cleaned


def compact_metadata_schema(raw_meta: dict[str, Any]) -> dict[str, Any]:
    name = raw_meta.get("name") or raw_meta.get("Name") or "НовыйОбъект"
    synonym = raw_meta.get("synonym") or raw_meta.get("Synonym") or name
    kind = raw_meta.get("kind") or raw_meta.get("Kind") or "ExternalDataProcessor"

    attributes: list[dict[str, str]] = []
    raw_attrs = raw_meta.get("attributes") or raw_meta.get("Attributes") or []
    for item in raw_attrs:
        if isinstance(item, dict):
            attr_name = item.get("name") or item.get("Name") or ""
            if attr_name:
                attributes.append({
                    "name": sanitize_ident(attr_name),
                    "synonym": str(item.get("synonym") or item.get("Synonym") or attr_name),
                    "type": str(item.get("type") or item.get("Type") or "Строка"),
                })
        elif isinstance(item, str) and item.strip():
            attributes.append({
                "name": sanitize_ident(item),
                "synonym": item.strip(),
                "type": "Строка",
            })

    tabular_sections: list[dict[str, Any]] = []
    raw_ts = raw_meta.get("tabular_sections") or raw_meta.get("TabularSections") or []
    for ts in raw_ts:
        if isinstance(ts, dict):
            ts_name = ts.get("name") or ts.get("Name") or ""
            if ts_name:
                ts_attrs: list[dict[str, str]] = []
                for sub_attr in ts.get("attributes", []):
                    sub_name = sub_attr.get("name") or sub_attr.get("Name") or ""
                    if sub_name:
                        ts_attrs.append({
                            "name": sanitize_ident(sub_name),
                            "synonym": str(sub_attr.get("synonym") or sub_name),
                            "type": str(sub_attr.get("type") or "Строка"),
                        })
                tabular_sections.append({
                    "name": sanitize_ident(ts_name),
                    "synonym": str(ts.get("synonym") or ts_name),
                    "attributes": ts_attrs,
                })

    forms: list[str] = []
    raw_forms = raw_meta.get("forms") or raw_meta.get("Forms") or []
    for f in raw_forms:
        f_name = f.get("name") if isinstance(f, dict) else str(f)
        if f_name:
            forms.append(sanitize_ident(f_name))

    return {
        "name": sanitize_ident(str(name)),
        "synonym": str(synonym),
        "kind": str(kind),
        "attributes": attributes,
        "tabular_sections": tabular_sections,
        "forms": forms,
    }


def validate_compact_schema(schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    name = schema.get("name", "")
    if not name or not IDENT_PATTERN.match(name):
        errors.append(f"Некорректное имя объекта '{name}'. Должно быть идентификатором 1С.")

    for attr in schema.get("attributes", []):
        aname = attr.get("name", "")
        if not aname or not IDENT_PATTERN.match(aname):
            errors.append(f"Некорректное имя реквизита '{aname}'.")

    for ts in schema.get("tabular_sections", []):
        ts_name = ts.get("name", "")
        if not ts_name or not IDENT_PATTERN.match(ts_name):
            errors.append(f"Некорректное имя табличной части '{ts_name}'.")
        for sub_attr in ts.get("attributes", []):
            saname = sub_attr.get("name", "")
            if not saname or not IDENT_PATTERN.match(saname):
                errors.append(f"Некорректное имя реквизита ТЧ '{ts_name}.{saname}'.")

    return errors


def format_schema_summary(schema: dict[str, Any]) -> str:
    lines = [
        f"# Компактная схема: {schema.get('name')} ({schema.get('synonym')})",
        f"**Вид объекта:** `{schema.get('kind')}`",
        "",
        "## Реквизиты:",
    ]
    attrs = schema.get("attributes", [])
    if attrs:
        for a in attrs:
            lines.append(f"- `{a['name']}` ({a['synonym']}): {a['type']}")
    else:
        lines.append("- (нет дополнительных реквизитов)")

    lines.append("")
    lines.append("## Табличные части:")
    ts_list = schema.get("tabular_sections", [])
    if ts_list:
        for ts in ts_list:
            lines.append(f"### ТЧ `{ts['name']}` ({ts['synonym']}):")
            for sub in ts.get("attributes", []):
                lines.append(f"  - `{sub['name']}`: {sub['type']}")
    else:
        lines.append("- (нет табличных частей)")

    if schema.get("forms"):
        lines.append("")
        lines.append("## Формы: " + ", ".join(schema["forms"]))

    return "\n".join(lines)
