from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
import xml.etree.ElementTree as ET

logger = logging.getLogger("kwork-guard.compact-schema")

IDENT_PATTERN = re.compile(r"^[A-Za-zА-Яа-я_][A-Za-zА-Яа-я0-9_]*$")

FORM_NOISE_TAGS = {
    "id", "font", "textcolor", "backcolor", "bordercolor", "titletextcolor",
    "titlebackcolor", "titlefont", "x", "y", "width", "height",
    "horizontalalign", "verticalalign", "horizontalstretch", "verticalstretch",
    "extendedtooltip", "contextmenu", "autocommandbar", "titlelocation",
    "border", "appearance", "uservisible", "representation", "format",
    "editformat", "tooltiprepresentation", "skiponinput",
}

DCS_NOISE_TAGS = {
    "conditionalappearance", "appearance", "font", "textcolor", "backcolor",
    "bordercolor", "appearanceitem", "usealways", "presentation",
    "uservisible", "viewmode",
}


def sanitize_ident(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-zА-Яа-я0-9_]", "", name.strip())
    if not cleaned or not IDENT_PATTERN.match(cleaned):
        return f"Элемент_{abs(hash(name)) % 10000}"
    return cleaned


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    if ":" in tag:
        return tag.split(":", 1)[1]
    return tag


def _extract_ru_text(elem: ET.Element) -> str:
    direct = (elem.text or "").strip()
    children = list(elem)
    if not children:
        return direct
    for ch in children:
        tag = _local_tag(ch.tag).lower()
        if tag in ("value", "content"):
            txt = (ch.text or "").strip()
            if txt:
                return txt
        if tag == "item":
            val = _extract_ru_text(ch)
            if val:
                return val
    return direct


def _element_to_compact_dict(elem: ET.Element, noise_tags: set[str]) -> Any:
    children = list(elem)
    text_val = (elem.text or "").strip()
    if not children:
        return text_val if text_val else None

    result: dict[str, Any] = {}
    for attr_k, attr_v in elem.attrib.items():
        local_k = _local_tag(attr_k)
        if local_k.lower() in ("uuid", "id"):
            continue
        if attr_v:
            result[local_k] = attr_v

    tag_lower = _local_tag(elem.tag).lower()
    if tag_lower in ("synonym", "title", "presentation"):
        ru_txt = _extract_ru_text(elem)
        return ru_txt if ru_txt else None

    list_buckets: dict[str, list[Any]] = {}
    for ch in children:
        c_tag = _local_tag(ch.tag)
        c_low = c_tag.lower()
        if c_low in noise_tags:
            continue
        c_val = _element_to_compact_dict(ch, noise_tags)
        if c_val is None or c_val == {} or c_val == []:
            continue
        if c_tag in result or c_tag in list_buckets:
            if c_tag not in list_buckets:
                list_buckets[c_tag] = [result.pop(c_tag)]
            list_buckets[c_tag].append(c_val)
        else:
            result[c_tag] = c_val

    for b_key, b_val in list_buckets.items():
        result[b_key] = b_val

    return result if result else None


def _compact_form_item(elem: ET.Element) -> Optional[dict[str, Any]]:
    item_out: dict[str, Any] = {}
    xsi_type = ""
    for k, v in elem.attrib.items():
        if _local_tag(k).lower() == "type":
            xsi_type = v.split(":")[-1]
            break
    if xsi_type:
        item_out["type"] = xsi_type
    else:
        item_out["type"] = _local_tag(elem.tag)

    nested_children: list[dict[str, Any]] = []
    events_list: list[dict[str, str]] = []

    for ch in elem:
        ch_tag = _local_tag(ch.tag)
        ch_low = ch_tag.lower()
        if ch_low in FORM_NOISE_TAGS:
            continue
        if ch_low == "name" and ch.text:
            item_out["name"] = ch.text.strip()
        elif ch_low in ("datapath", "data_path"):
            dp_val = (ch.text or "").strip()
            if not dp_val:
                for sub in ch:
                    if _local_tag(sub.tag).lower() == "segments" and sub.text:
                        dp_val = sub.text.strip()
                        break
            if dp_val:
                item_out["dataPath"] = dp_val
        elif ch_low == "events":
            ev_name = ""
            ev_call = ""
            if "event" in ch.attrib:
                ev_name = ch.attrib["event"]
            for sub in ch:
                sub_low = _local_tag(sub.tag).lower()
                if sub_low == "name" and sub.text:
                    ev_name = sub.text.strip()
                elif sub_low in ("call", "handler") and sub.text:
                    ev_call = sub.text.strip()
            if not ev_call and ch.text and ch.text.strip():
                ev_call = ch.text.strip()
            if ev_name or ev_call:
                events_list.append({"event": ev_name, "handler": ev_call})
        elif ch_low in ("items", "childitems"):
            compacted_child = _compact_form_item(ch)
            if compacted_child:
                nested_children.append(compacted_child)

    if events_list:
        item_out["events"] = events_list
    if nested_children:
        item_out["items"] = nested_children

    return item_out if item_out.get("name") or item_out.get("dataPath") or nested_children else None


def compact_form_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text.lstrip("\ufeff"))
    items_tree: list[dict[str, Any]] = []
    attributes: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []

    for ch in root:
        tag_low = _local_tag(ch.tag).lower()
        if tag_low in ("items", "childitems"):
            comp = _compact_form_item(ch)
            if comp:
                items_tree.append(comp)
        elif tag_low == "attributes":
            attr_comp = _element_to_compact_dict(ch, FORM_NOISE_TAGS)
            if isinstance(attr_comp, dict) and attr_comp.get("name"):
                attributes.append(attr_comp)
        elif tag_low == "commands":
            cmd_comp = _element_to_compact_dict(ch, FORM_NOISE_TAGS)
            if isinstance(cmd_comp, dict) and cmd_comp.get("name"):
                commands.append(cmd_comp)

    return {
        "kind": "Form",
        "items": items_tree,
        "attributes": attributes,
        "commands": commands,
    }


def compact_dcs_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text.lstrip("\ufeff"))
    keep_sections = {
        "datasets": "dataSets",
        "dataset": "dataSets",
        "datasetlinks": "dataSetLinks",
        "datasetlink": "dataSetLinks",
        "calculatedfields": "calculatedFields",
        "calculatedfield": "calculatedFields",
        "totalfields": "totalFields",
        "totalfield": "totalFields",
        "parameters": "parameters",
        "parameter": "parameters",
        "settingsvariants": "settingsVariants",
        "settingsvariant": "settingsVariants",
        "settings": "settings",
    }

    output: dict[str, Any] = {
        "kind": "DataCompositionSchema",
        "dataSets": [],
        "dataSetLinks": [],
        "calculatedFields": [],
        "totalFields": [],
        "parameters": [],
        "settings": [],
    }

    for ch in root:
        tag_low = _local_tag(ch.tag).lower()
        if tag_low in DCS_NOISE_TAGS:
            continue
        bucket = keep_sections.get(tag_low)
        if not bucket:
            continue
        compacted = _element_to_compact_dict(ch, DCS_NOISE_TAGS)
        if compacted is None:
            continue
        if bucket == "settingsVariants":
            output["settings"].append(compacted)
        else:
            output[bucket].append(compacted)

    return {k: v for k, v in output.items() if v}


def compact_mdo_xml(xml_text: str) -> dict[str, Any]:
    root = ET.fromstring(xml_text.lstrip("\ufeff"))
    compacted = _element_to_compact_dict(root, FORM_NOISE_TAGS)
    if isinstance(compacted, dict):
        compacted["kind"] = _local_tag(root.tag)
        return compacted
    return {"kind": _local_tag(root.tag)}


def compact_1c_xml(xml_text: str, hint_path: str = "") -> str:
    stripped = xml_text.lstrip("\ufeff").strip()
    if not stripped.startswith("<"):
        return xml_text

    hint_low = hint_path.lower()
    head_sample = stripped[:400].lower()
    try:
        if "form.form" in hint_low or "<form:form" in head_sample or "<managedform" in head_sample:
            data = compact_form_xml(stripped)
        elif ".dcs" in hint_low or "datacompositionschema" in head_sample:
            data = compact_dcs_xml(stripped)
        else:
            data = compact_mdo_xml(stripped)
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    except Exception as exc:
        logger.debug("1C XML compact skipped (%s): %s", hint_path, exc)
        return xml_text


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
