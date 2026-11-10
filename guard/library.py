from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

LIBRARY_DIR = Path(__file__).resolve().parent.parent / "library"
INDEX_PATH = LIBRARY_DIR / "index.json"


def load_index() -> dict[str, Any]:
    if not INDEX_PATH.is_file():
        return {"version": "1.0", "snippets": []}
    with INDEX_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def list_snippets(tags: Optional[list[str]] = None, snippet_type: Optional[str] = None) -> list[dict[str, Any]]:
    data = load_index()
    snippets = data.get("snippets", [])
    result = []
    for s in snippets:
        if snippet_type and s.get("type") != snippet_type:
            continue
        if tags:
            s_tags = set(s.get("tags", []))
            if not any(t in s_tags for t in tags):
                continue
        result.append({
            "id": s["id"],
            "title": s["title"],
            "type": s.get("type"),
            "tags": s.get("tags", []),
            "status": s.get("status", "draft"),
            "bsp_version": s.get("bsp_version"),
        })
    return result


def get_snippet(snippet_id: str) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str]]:
    data = load_index()
    found = None
    for s in data.get("snippets", []):
        if s["id"] == snippet_id:
            found = s
            break
    if not found:
        return None, None, f"Сниппет с ID '{snippet_id}' не найден в библиотеке."

    src_rel = found.get("source_file")
    if not src_rel:
        return None, None, f"У сниппета '{snippet_id}' не указан source_file."

    src_path = LIBRARY_DIR / src_rel
    if not src_path.is_file():
        return None, None, f"Файл сниппета не найден на диске: {src_rel}"

    with src_path.open("r", encoding="utf-8") as f:
        content = f.read()

    return content, found, None


def inject_snippets_into_code(snippet_ids: list[str], base_code: str) -> tuple[str, list[str]]:
    injected_blocks = []
    warnings = []
    for sid in snippet_ids:
        content, meta, err = get_snippet(sid)
        if err:
            warnings.append(err)
            continue
        if meta and meta.get("status") == "draft":
            warnings.append(f"Внимание: сниппет '{sid}' имеет статус 'draft' (требует проверки на базе).")
        header = f"\n// --- library:{sid} ({meta.get('title', '')}) ---"
        footer = f"// --- конец library:{sid} ---\n"
        injected_blocks.append(f"{header}\n{content.strip()}\n{footer}")

    combined = base_code.rstrip() + "\n\n" + "\n".join(injected_blocks) if injected_blocks else base_code
    return combined, warnings
