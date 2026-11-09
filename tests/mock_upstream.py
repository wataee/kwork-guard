"""
tests/mock_upstream.py - Имитация сервера EDT-MCP для тестирования прокси без живого EDT.
Поддерживает:
- initialize и генерацию mcp-session-id
- notifications/initialized
- tools/list (возвращает тестовый набор инструментов)
- tools/call в формате JSON
- tools/call в формате SSE (text/event-stream)
- потерю сессии (404) по флагу или спец-заголовку
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

mock_app = FastAPI(title="mock-edt-mcp")

# Состояние мок-сервера для управления сценариями тестов
mock_state = {
    "session_counter": 0,
    "current_session": "mock-sess-1",
    "simulate_session_loss": False,
    "force_sse": False,
    "call_history": [],
}

MOCK_TOOLS = [
    {"name": "create_project", "description": "Start a new EDT configuration, extension, or external objects project.", "inputSchema": {}},
    {"name": "create_metadata", "description": "Add a new metadata object or member to a configuration.", "inputSchema": {}},
    {"name": "modify_metadata", "description": "Set properties of any metadata node.", "inputSchema": {}},
    {"name": "read_module_source", "description": "Inspect the source of a complete BSL module.", "inputSchema": {}},
    {"name": "write_module_source", "description": "Create or edit BSL source in a metadata module.", "inputSchema": {}},
    {"name": "get_project_errors", "description": "Find detailed validation errors and warnings in an EDT project.", "inputSchema": {}},
    {"name": "build_external_objects", "description": "Compile external 1C data processors and reports into .epf/.erf.", "inputSchema": {}},
    {"name": "list_projects", "description": "Discover projects available in the EDT workspace.", "inputSchema": {}},
    {"name": "get_tool_guide", "description": "Retrieve detailed instructions for an MCP tool.", "inputSchema": {}},
    {"name": "dcs", "description": "Data composition schema tool.", "inputSchema": {}},
    {"name": "validate_query", "description": "Validate 1C query syntax.", "inputSchema": {}},
    {"name": "adopt_metadata_object", "description": "Adopt metadata object to extension.", "inputSchema": {}},
    {"name": "export_configuration_to_file", "description": "Export configuration to .cf/.cfe.", "inputSchema": {}},
    {"name": "unsupported_debug_tool", "description": "Debug tool not in whitelist.", "inputSchema": {}},
]


@mock_app.post("/mcp")
async def mcp_handler(request: Request, mcp_session_id: str | None = Header(None)) -> Response:
    body = await request.json()
    mid = body.get("id")
    method = body.get("method")
    params = body.get("params", {})
    mock_state["call_history"].append({"method": method, "params": params, "session": mcp_session_id})

    # Проверка симуляции потери сессии
    if mock_state["simulate_session_loss"] and method != "initialize":
        mock_state["simulate_session_loss"] = False
        return JSONResponse({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "Session expired"}}, status_code=404)

    if method == "initialize":
        mock_state["session_counter"] += 1
        new_sid = f"mock-sess-{mock_state['session_counter']}"
        mock_state["current_session"] = new_sid
        resp_data = {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mock-edt-server", "version": "2.23.1"},
            },
        }
        return JSONResponse(resp_data, headers={"mcp-session-id": new_sid})

    if method == "notifications/initialized":
        return Response(status_code=200)

    if method == "tools/list":
        return JSONResponse({"jsonrpc": "2.0", "id": mid, "result": {"tools": MOCK_TOOLS}})

    if method == "tools/call":
        tname = params.get("name")
        targs = params.get("arguments", {})

        # Логика ответа для вызовов
        if tname == "get_project_errors":
            if targs.get("projectName") == "error_project":
                content_text = "ERROR: ExternalDataProcessors/Тест/ObjectModule.bsl: [12, 5]: Синтаксическая ошибка\nWARNING: Style warning"
            else:
                content_text = "No validation errors found in project."
            is_error = False
        elif tname == "read_module_source":
            content_text = """Процедура ЗаполнитьДанные(Ссылка, ПараметрыШапки)
    // Старый код заполнения
КонецПроцедуры

Функция СведенияОВнешнейОбработке() Экспорт
    Возврат Новый Структура;
КонецФункции"""
            is_error = False
        elif tname == "build_external_objects":
            content_text = json.dumps({"success": True, "built": [targs.get("objectName", "obj")]})
            is_error = False
        elif tname == "mock_error_tool":
            content_text = "Mock simulated tool failure"
            is_error = True
        else:
            content_text = f"Tool {tname} executed successfully with args: {targs}"
            is_error = False

        result_payload = {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "content": [{"type": "text", "text": content_text}],
                "isError": is_error,
            },
        }

        # Режим ответа через SSE
        if mock_state["force_sse"] or "text/event-stream" in request.headers.get("accept", ""):
            async def event_generator():
                yield f"data: {json.dumps(result_payload, ensure_ascii=False)}\n\n"
            return StreamingResponse(event_generator(), media_type="text/event-stream")

        return JSONResponse(result_payload)

    return JSONResponse({"jsonrpc": "2.0", "id": mid, "result": {}})


class MockServerThread:
    def __init__(self, port: int = 18765):
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        config = uvicorn.Config(mock_app, host="127.0.0.1", port=self.port, log_level="error")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        # Ожидание старта
        for _ in range(50):
            if self.server.started:
                break
            time.sleep(0.05)

    def stop(self):
        if self.server:
            self.server.should_exit = True
            if self.thread:
                self.thread.join(timeout=3.0)
