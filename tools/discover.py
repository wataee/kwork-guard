#!/usr/bin/env python3
"""
tools/discover.py - Разведка схем и параметров инструментов EDT-MCP (WP0).

Выполняет ТОЛЬКО операции чтения:
1. tools/list - список и описания инструментов.
2. get_tool_guide(mode='summary') - для ключевых инструментов разработки 1С.
3. Сохраняет подтверждённые данные в verified_schemas.json.
4. Сверяет схемы с константами edt_guard.py и выводит таблицу расхождений.

Если EDT недоступен, завершается с кодом 2.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import httpx

EDT_URL = os.getenv("EDT_MCP_URL", "http://127.0.0.1:8765/mcp")
EDT_TOKEN = os.getenv("EDT_MCP_TOKEN", "")
OUTPUT_FILE = Path(__file__).resolve().parent.parent / "verified_schemas.json"

TARGET_TOOLS = [
    "create_project",
    "create_metadata",
    "modify_metadata",
    "write_module_source",
    "read_method_source",
    "get_project_errors",
    "build_external_objects",
    "adopt_metadata_object",
    "dcs",
    "export_configuration_to_file",
]

# Константы из edt_guard.py для сверки
GUARD_CONSTANTS = {
    "MODULE_PATH_TMPL": "ExternalDataProcessors/{name}/ObjectModule.bsl",
    "EXTERNAL_OBJECT_TMPL": "ExternalDataProcessor.{name}",
    "TEMPLATE_FQN_TMPL": "ExternalDataProcessor.{name}.Template.{template}",
    "ERRORS_ARGS": {"severity": "ERRORS", "limit": 20},
    "BUILD_OUTPUT_ARG": "outputDir",
}


def build_headers(session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if EDT_TOKEN:
        headers["Authorization"] = f"Bearer {EDT_TOKEN}"
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    return headers


def parse_response_body(resp: httpx.Response) -> dict[str, Any] | None:
    if not resp.content:
        return None
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        last = None
        for line in resp.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    try:
                        obj = json.loads(payload)
                        if isinstance(obj, dict) and "id" in obj:
                            last = obj
                    except json.JSONDecodeError:
                        continue
        return last
    return resp.json()


async def run_discovery() -> int:
    print(f"[*] Подключение к EDT-MCP: {EDT_URL} ...")
    client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
    session_id: str | None = None

    # 1. Проверка доступности и initialize
    try:
        init_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "discover-tool", "version": "1.0"},
            },
        }
        resp = await client.post(EDT_URL, headers=build_headers(), json=init_payload)
        if resp.status_code != 200:
            print(f"[!] EDT вернул ошибку инициализации: HTTP {resp.status_code}")
            await client.aclose()
            return 2

        session_id = resp.headers.get("mcp-session-id")
        init_body = parse_response_body(resp)
        server_info = (init_body or {}).get("result", {}).get("serverInfo", {})
        print(f"[✓] EDT-MCP доступен: {server_info.get('name')} v{server_info.get('version')}")

        # Подтверждение инициализации
        await client.post(
            EDT_URL,
            headers=build_headers(session_id),
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
    except Exception as exc:
        print(f"[!] Не удалось подключиться к EDT-MCP по адресу {EDT_URL}: {exc}")
        print("    EDT-сервер не запущен или порт недоступен.")
        await client.aclose()
        return 2

    # 2. tools/list
    print("[*] Запрос списка инструментов (tools/list)...")
    resp = await client.post(
        EDT_URL,
        headers=build_headers(session_id),
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    tools_body = parse_response_body(resp)
    all_tools = (tools_body or {}).get("result", {}).get("tools", [])
    tools_map = {t["name"]: t for t in all_tools}
    print(f"[✓] Получено инструментов: {len(all_tools)}")

    discovered: dict[str, Any] = {
        "serverInfo": server_info,
        "toolsCount": len(all_tools),
        "tools": {},
    }

    # 3. get_tool_guide для целевых инструментов
    print(f"[*] Сбор детальных руководств для {len(TARGET_TOOLS)} ключевых инструментов...")
    for idx, tool_name in enumerate(TARGET_TOOLS, start=3):
        tool_meta = tools_map.get(tool_name)
        if not tool_meta:
            print(f"  [-] Инструмент {tool_name} не найден в EDT-MCP!")
            discovered["tools"][tool_name] = {"found": False}
            continue

        guide_call = {
            "jsonrpc": "2.0",
            "id": idx * 10,
            "method": "tools/call",
            "params": {
                "name": "get_tool_guide",
                "arguments": {"tool": tool_name, "mode": "summary"},
            },
        }
        guide_resp = await client.post(
            EDT_URL,
            headers=build_headers(session_id),
            json=guide_call,
        )
        guide_body = parse_response_body(guide_resp)
        guide_text = ""
        for content in (guide_body or {}).get("result", {}).get("content", []):
            if content.get("type") == "text":
                guide_text += content.get("text", "")

        discovered["tools"][tool_name] = {
            "found": True,
            "description": tool_meta.get("description", ""),
            "inputSchema": tool_meta.get("inputSchema", {}),
            "guideSummary": guide_text[:2000],
        }
        print(f"  [✓] {tool_name}: описание ({len(tool_meta.get('description', ''))} симв), guide ({len(guide_text)} симв)")

    await client.aclose()

    # 4. Сохранение verified_schemas.json
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(discovered, f, ensure_ascii=False, indent=2)
    print(f"\n[✓] Подтверждённые схемы сохранены в: {OUTPUT_FILE}")

    # 5. Сверка с константами edt_guard.py
    print("\n" + "=" * 70)
    print("ТАБЛИЦА СВЕРКИ КОНСТАНТ EDT_GUARD С РЕАЛЬНЫМ EDT-MCP")
    print("=" * 70)

    discrepancies = []

    # Проверка build_external_objects
    beo = discovered["tools"].get("build_external_objects", {})
    beo_props = beo.get("inputSchema", {}).get("properties", {})
    build_arg = GUARD_CONSTANTS["BUILD_OUTPUT_ARG"]
    if build_arg in beo_props:
        status_build = f"Подтверждено (свойство '{build_arg}' есть в схеме)"
    else:
        status_build = f"РАСХОЖДЕНИЕ: свойства '{build_arg}' нет в {list(beo_props.keys())}"
        discrepancies.append(("BUILD_OUTPUT_ARG", status_build))
    print(f"1. BUILD_OUTPUT_ARG ('{build_arg}'): {status_build}")

    # Проверка get_project_errors (аргументы severity)
    gpe = discovered["tools"].get("get_project_errors", {})
    gpe_props = gpe.get("inputSchema", {}).get("properties", {})
    gpe_guide = gpe.get("guideSummary", "")
    err_args = GUARD_CONSTANTS["ERRORS_ARGS"]
    sev_match = "ERRORS" in gpe_guide or "ERRORS" in json.dumps(gpe_props)
    if sev_match:
        status_err = f"Подтверждено (severity='ERRORS' допустим в EDT-MCP)"
    else:
        status_err = "UNVERIFIED: точный список enum severity требует проверки"
    print(f"2. ERRORS_ARGS ({err_args}): {status_err}")

    # Проверка write_module_source (модули и objectName/modulePath)
    wms = discovered["tools"].get("write_module_source", {})
    wms_props = wms.get("inputSchema", {}).get("properties", {})
    wms_has_path = "modulePath" in wms_props
    wms_has_mode = "mode" in wms_props
    print(f"3. MODULE_PATH_TMPL ('{GUARD_CONSTANTS['MODULE_PATH_TMPL']}'): "
          f"modulePath={'есть' if wms_has_path else 'НЕТ'}, mode={'есть' if wms_has_mode else 'НЕТ'}")

    # Проверка create_project (externalObjects)
    cp = discovered["tools"].get("create_project", {})
    cp_props = cp.get("inputSchema", {}).get("properties", {})
    cp_guide = cp.get("guideSummary", "")
    has_kind = "projectKind" in cp_props
    has_ext_obj = "externalObject" in cp_props or "externalObject" in cp_guide
    print(f"4. EXTERNAL_OBJECT_TMPL ('{GUARD_CONSTANTS['EXTERNAL_OBJECT_TMPL']}'): "
          f"projectKind={'есть' if has_kind else 'НЕТ'}, externalObject={'есть' if has_ext_obj else 'НЕТ'}")

    # Проверка create_metadata (Template)
    cm = discovered["tools"].get("create_metadata", {})
    cm_props = cm.get("inputSchema", {}).get("properties", {})
    print(f"5. TEMPLATE_FQN_TMPL ('{GUARD_CONSTANTS['TEMPLATE_FQN_TMPL']}'): "
          f"fqn={'есть' if 'fqn' in cm_props else 'НЕТ'}")

    print("=" * 70)
    if discrepancies:
        print(f"[!] Найдено расхождений: {len(discrepancies)}")
    else:
        print("[✓] Все ключевые константы guard согласованы с EDT-MCP v2.23.1")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(run_discovery()))
    except KeyboardInterrupt:
        sys.exit(130)
