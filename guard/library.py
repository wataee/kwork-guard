from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any, Optional

from guard.bsl_linter import filter_critical_issues, lint_bsl_code
from guard.fs_utils import atomic_write_json, atomic_write_text, write_bsl_with_bom

logger = logging.getLogger("kwork-guard.library")

LIBRARY_DIR = Path(__file__).resolve().parent.parent / "library"
INDEX_PATH = LIBRARY_DIR / "index.json"


def _resolve_paths(library_dir: Optional[Path] = None) -> tuple[Path, Path]:
    if library_dir is not None:
        return library_dir, library_dir / "index.json"
    return LIBRARY_DIR, INDEX_PATH


def load_index(library_dir: Optional[Path] = None) -> dict[str, Any]:
    _, idx_path = _resolve_paths(library_dir)
    if not idx_path.is_file():
        return {"version": "1.0", "snippets": []}
    with idx_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def list_snippets(
    tags: Optional[list[str]] = None,
    snippet_type: Optional[str] = None,
    library_dir: Optional[Path] = None,
) -> list[dict[str, Any]]:
    data = load_index(library_dir)
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
            "source_file": s.get("source_file", ""),
        })
    return result


def get_snippet(
    snippet_id: str,
    library_dir: Optional[Path] = None,
) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str]]:
    lib_dir, _ = _resolve_paths(library_dir)
    data = load_index(lib_dir)
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

    src_path = lib_dir / src_rel
    if not src_path.is_file():
        return None, None, f"Файл сниппета не найден на диске: {src_rel}"

    with src_path.open("r", encoding="utf-8-sig") as f:
        content = f.read()

    return content, found, None


def save_snippet(
    snippet_id: str,
    title: str,
    code: str,
    snippet_type: str = "bsl",
    tags: Optional[list[str]] = None,
    notes: str = "",
    bsp_version: str = "любая",
    library_dir: Optional[Path] = None,
) -> tuple[bool, str]:
    lib_dir, idx_path = _resolve_paths(library_dir)
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", snippet_id.strip()).strip("_")
    if not safe_id:
        return False, "Некорректный идентификатор сниппета"
    if not code or not code.strip():
        return False, "Код сниппета не может быть пустым"

    stype = "query" if snippet_type.strip().lower() == "query" else "bsl"
    rel_file = f"query/{safe_id}.txt" if stype == "query" else f"bsl/{safe_id}.bsl"
    target_file = lib_dir / rel_file

    try:
        if stype == "bsl":
            write_bsl_with_bom(target_file, code)
        else:
            atomic_write_text(target_file, code.lstrip("\ufeff"), encoding="utf-8")

        data = load_index(lib_dir)
        snippets = data.setdefault("snippets", [])
        entry = {
            "id": safe_id,
            "title": title.strip() or safe_id,
            "type": stype,
            "tags": [t.strip() for t in (tags or []) if t.strip()] or [stype],
            "status": "draft",
            "bsp_version": bsp_version,
            "source_file": rel_file,
            "notes": notes.strip() or "Добавлено через Kwork Cockpit",
        }

        updated = False
        for i, existing in enumerate(snippets):
            if existing.get("id") == safe_id:
                snippets[i] = entry
                updated = True
                break
        if not updated:
            snippets.append(entry)

        atomic_write_json(idx_path, data)
        return True, f"Сниппет '{safe_id}' сохранен в {rel_file}"
    except Exception as exc:
        logger.error("Failed to save snippet %s: %s", safe_id, exc)
        return False, str(exc)


def verify_library(base_dir: Optional[Path] = None) -> tuple[bool, str]:
    lib_dir = (base_dir / "library") if base_dir else LIBRARY_DIR
    data = load_index(lib_dir)
    snippets = data.get("snippets", [])
    if not snippets:
        return True, "Библиотека сниппетов пуста."

    lines = [f"Проверка библиотеки ({len(snippets)} сниппетов):"]
    all_ok = True
    for s in snippets:
        sid = s.get("id", "unknown")
        rel = s.get("source_file", "")
        stype = s.get("type", "bsl")
        if not rel or not (lib_dir / rel).is_file():
            all_ok = False
            lines.append(f"- [FAIL] {sid}: отсутствует файл '{rel}'")
            continue
        if stype == "bsl":
            code = (lib_dir / rel).read_text(encoding="utf-8-sig")
            issues = filter_critical_issues(lint_bsl_code(code), include_warnings=False)
            if issues:
                all_ok = False
                lines.append(f"- [FAIL] {sid}: ошибки BSL ({issues[0].code} стр. {issues[0].line})")
                continue
        lines.append(f"- [OK] {sid} ({stype}, {s.get('status', 'draft')})")

    return all_ok, "\n".join(lines)


def build_context_attachment(
    query_text: str,
    max_snippets: int = 2,
    library_dir: Optional[Path] = None,
) -> str:
    q_low = query_text.lower()
    if not q_low.strip():
        return ""
    snippets = list_snippets(library_dir=library_dir)
    matched: list[str] = []
    for s in snippets:
        keywords = [s.get("id", ""), s.get("title", "")] + list(s.get("tags", []))
        if any(len(kw) > 2 and kw.lower() in q_low for kw in keywords):
            content, _, err = get_snippet(s["id"], library_dir=library_dir)
            if content and not err:
                matched.append(f"[{s['id']}] {s['title']}:\n{content.strip()[:600]}")
                if len(matched) >= max_snippets:
                    break
    return "\n\n".join(matched)


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
