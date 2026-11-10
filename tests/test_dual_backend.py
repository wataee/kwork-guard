from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import urllib.error

import httpx
import pytest

import edt_guard
from guard.config import (
    DEFAULT_CONFIG,
    get_active_provider,
    load_config,
    select_model_for_task,
    set_active_provider,
)
from guard.dual_backend import (
    DualBackendClient,
    apply_prompt_cache_control,
    build_cached_system_block,
    extract_thinking_and_clean_text,
    inject_dynamic_context,
    normalize_tool_calls,
    normalize_tool_results,
    parse_clean_json_arguments,
    read_env_api_key,
)
from guard.openrouter_billing import estimate_virtual_cost, record_llm_token_usage
from guard.state import OrderState


def test_config_dual_backend_routing_and_switching(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.json"
    with patch("guard.config.CONFIG_PATH", cfg_file):
        cfg = load_config()
        assert get_active_provider(cfg) in ("antigravity", "openrouter")
        assert "antigravity" in cfg["providers"]
        assert "openrouter" in cfg["providers"]

        assert select_model_for_task("intake", cfg) == "google/gemini-2.5-flash"
        assert select_model_for_task("compress", cfg) == "google/gemini-2.5-flash"
        assert select_model_for_task("bsl", cfg) == "anthropic/claude-3-7-sonnet"
        assert select_model_for_task("edt_mcp", cfg) == "anthropic/claude-3-7-sonnet"
        assert select_model_for_task("bsl", cfg, economy_mode=True) == "google/gemini-2.5-flash"

        ok_agy, msg_agy = set_active_provider("antigravity")
        assert ok_agy is True and msg_agy == "antigravity"
        assert get_active_provider() == "antigravity"

        ok_or, msg_or = set_active_provider("openrouter")
        assert ok_or is True and msg_or == "openrouter"
        assert get_active_provider() == "openrouter"

        ok_bad, _ = set_active_provider("invalid_provider")
        assert ok_bad is False


def test_thinking_and_tool_normalization() -> None:
    raw_content = [
        {"type": "thinking", "thinking": "Step 1 analyze metadata"},
        {"type": "text", "text": "<thinking>internal thought</thinking>\n{\"projectName\": \"Proj1\", \"unused\": \"\"}"},
    ]
    thoughts, clean_txt = extract_thinking_and_clean_text(raw_content)
    assert len(thoughts) == 2
    assert "Proj1" in clean_txt
    assert "internal thought" not in clean_txt

    parsed = parse_clean_json_arguments("<thinking>think</thinking>\n```json\n{\"name\": \"Тест\", \"empty\": \"\", \"none\": null, \"nested\": {}}\n```")
    assert parsed == {"name": "Тест"}

    calls = normalize_tool_calls([
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "create_metadata", "arguments": "<thinking>x</thinking>{\"projectName\": \"P\", \"extra\": \"\"}"},
        },
        {
            "id": "c2",
            "name": "list_projects",
            "input": {"limit": 10, "emptyList": []},
        },
    ])
    assert len(calls) == 2
    assert json.loads(calls[0]["function"]["arguments"]) == {"projectName": "P"}
    assert json.loads(calls[1]["function"]["arguments"]) == {"limit": 10}

    norm_msgs = normalize_tool_results([
        {"role": "assistant", "content": [{"type": "thinking", "thinking": "reasoning"}], "tool_calls": calls},
        {"role": "tool", "tool_call_id": "c1", "content": "<thinking>noise</thinking>{\"ok\": true}"},
    ])
    assert norm_msgs[0]["content"] is None
    assert norm_msgs[1]["content"] == '{"ok": true}'


def test_prompt_caching_protection_and_dynamic_injection() -> None:
    base_messages = [
        {"role": "system", "content": "Статические правила 1С БСП"},
        {"role": "user", "content": "Сделай ВПФ счета"},
        {"role": "assistant", "content": "Проверяю"},
        {"role": "tool", "tool_call_id": "t1", "content": "Результат чтения"},
    ]

    cached = apply_prompt_cache_control(base_messages)
    assert cached[0]["content"][0]["text"] == "Статические правила 1С БСП"
    assert cached[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    sys_block = build_cached_system_block("Правила")
    assert sys_block["content"][0]["cache_control"] == {"type": "ephemeral"}

    injected = inject_dynamic_context(cached, "ТаблицаЗначений: Добавить()")
    assert injected[0]["content"] == cached[0]["content"]
    assert injected[1]["content"] == "Сделай ВПФ счета"
    assert "ТаблицаЗначений: Добавить()" in injected[3]["content"]


def test_virtual_cost_billing_for_antigravity_and_real_for_openrouter(tmp_path: Path) -> None:
    est = estimate_virtual_cost("anthropic/claude-3-7-sonnet", 100000, 10000)
    assert est > 0.4

    st_agy = OrderState("order_agy", tmp_path)
    res_agy = record_llm_token_usage(st_agy, "antigravity", "claude-3-7-sonnet", 50000, 5000, usd_to_rub=100.0)
    assert res_agy["mode"] == "virtual_cost"
    assert st_agy.real_cost_usd == 0.0
    assert st_agy.real_cost_rub == 0.0
    assert st_agy.virtual_cost_usd > 0.0
    assert st_agy.virtual_cost_rub > 0.0
    assert st_agy.input_tokens == 50000

    st_or = OrderState("order_or", tmp_path)
    res_or = record_llm_token_usage(st_or, "openrouter", "google/gemini-2.5-flash", 50000, 5000, usd_to_rub=100.0)
    assert res_or["mode"] == "real_cost"
    assert st_or.real_cost_usd > 0.0
    assert st_or.real_cost_rub > 0.0


def test_dual_backend_sync_fallback_on_429(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_data = {
        **DEFAULT_CONFIG,
        "active_provider": "antigravity",
        "providers": {
            "antigravity": {
                "base_url": "http://127.0.0.1:3000/v1",
                "api_key_env": "ANTIGRAVITY_API_KEY",
                "fallback_to_openrouter": True,
                "fallback_ports": [3000],
            },
            "openrouter": {
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY",
            },
        },
    }
    cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")

    client = DualBackendClient(root_dir=tmp_path)
    ok_payload = json.dumps({
        "choices": [{"message": {"role": "assistant", "content": "OK fallback"}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
    }).encode("utf-8")

    mock_ok_resp = MagicMock()
    mock_ok_resp.read.return_value = ok_payload
    mock_ok_resp.__enter__.return_value = mock_ok_resp

    calls: list[str] = []

    def fake_urlopen(req, timeout=15.0):
        calls.append(req.full_url)
        if "127.0.0.1:3000" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 429, "Rate Limit", {}, None)
        return mock_ok_resp

    with patch("guard.config.CONFIG_PATH", cfg_file), \
         patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-test-or-key"}), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen):
        resp, used_provider = client.chat_completion_sync(
            payload={"messages": [{"role": "user", "content": "test"}]},
            task_type="intake",
            order_id="fallback_ord",
        )
        assert used_provider == "openrouter"
        assert resp["choices"][0]["message"]["content"] == "OK fallback"
        assert any("127.0.0.1:3000" in u for u in calls)
        assert any("openrouter.ai" in u for u in calls)


@pytest.mark.asyncio
async def test_edt_guard_admin_provider_and_chat_completions(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(DEFAULT_CONFIG), encoding="utf-8")

    transport = httpx.ASGITransport(app=edt_guard.app)
    with patch("guard.config.CONFIG_PATH", cfg_file):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r_get = await client.get("/admin/provider")
            assert r_get.status_code == 200
            assert "active_provider" in r_get.json()

            r_post = await client.post("/admin/provider", json={"provider": "openrouter"})
            assert r_post.status_code == 200
            assert r_post.json()["active_provider"] == "openrouter"

            r_bad = await client.post("/admin/provider", json={"provider": "unknown"})
            assert r_bad.status_code == 400

            mock_completion = {
                "choices": [{"message": {"role": "assistant", "content": "Готово"}}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 100},
            }
            with patch.object(
                edt_guard.DUAL_CLIENT,
                "chat_completion_async",
                new=AsyncMock(return_value=(mock_completion, "antigravity")),
            ):
                r_chat = await client.post(
                    "/v1/chat/completions",
                    headers={"X-Order-Id": "chat_ord", "X-Task-Type": "bsl"},
                    json={"messages": [{"role": "user", "content": "Используй ТаблицаЗначений"}]},
                )
                assert r_chat.status_code == 200
                assert r_chat.headers.get("X-Active-Provider") == "antigravity"
                assert r_chat.json()["choices"][0]["message"]["content"] == "Готово"


@pytest.mark.asyncio
async def test_dual_backend_async_real_fallback_and_health(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('OPENROUTER_API_KEY="env-or-key"\nANTIGRAVITY_API_KEY="env-agy-key"\n', encoding="utf-8")
    assert read_env_api_key("OPENROUTER_API_KEY", tmp_path) == "env-or-key"
    assert read_env_api_key("MISSING_KEY", tmp_path) == ""

    cfg_file = tmp_path / "config.json"
    cfg_data = {
        **DEFAULT_CONFIG,
        "active_provider": "antigravity",
        "providers": {
            "antigravity": {
                "base_url": "http://127.0.0.1:3000/v1",
                "api_key_env": "ANTIGRAVITY_API_KEY",
                "fallback_to_openrouter": True,
                "fallback_ports": [3000],
            },
            "openrouter": {
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY",
            },
        },
    }
    cfg_file.write_text(json.dumps(cfg_data), encoding="utf-8")

    client = DualBackendClient(root_dir=tmp_path)

    async def fake_post(self_client, url, json=None, headers=None):
        req = httpx.Request("POST", url)
        if "127.0.0.1:3000" in url:
            return httpx.Response(429, request=req, json={"error": "rate limit"})
        return httpx.Response(
            200,
            request=req,
            json={
                "choices": [{"message": {"role": "assistant", "content": "OK async"}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 30},
            },
        )

    with patch("guard.config.CONFIG_PATH", cfg_file), \
         patch.object(httpx.AsyncClient, "post", new=fake_post):
        resp_data, used = await client.chat_completion_async(
            payload={
                "messages": [{"role": "user", "content": "test"}],
                "tools": [{"type": "function", "function": {"name": "tool1", "description": ""}}],
            },
            task_type="bsl",
            order_id="async_fb_ord",
            dynamic_context="Контекст",
        )
        assert used == "openrouter"
        assert resp_data["choices"][0]["message"]["content"] == "OK async"

    mock_http_err = urllib.error.HTTPError("http://127.0.0.1:3000/v1/models", 401, "Unauth", {}, None)
    with patch("guard.config.CONFIG_PATH", cfg_file), \
         patch("urllib.request.urlopen", side_effect=mock_http_err):
        online, url = client.check_antigravity_health(timeout=0.5)
        assert online is True
        assert "3000" in url

    with patch("guard.config.CONFIG_PATH", cfg_file), \
         patch("urllib.request.urlopen", side_effect=OSError("conn refused")):
        offline, _ = client.check_antigravity_health(timeout=0.2)
        assert offline is False


@pytest.mark.asyncio
async def test_dual_backend_edge_branches_and_errors(tmp_path: Path) -> None:
    assert parse_clean_json_arguments("") == {}
    assert parse_clean_json_arguments("text {bad json} end") == {}
    assert parse_clean_json_arguments('prefix {"a": 1, "b": ""} suffix') == {"a": 1}

    raw_calls = [
        "bad-entry",
        {"id": "1", "function": {"name": "", "arguments": "{}"}},
        {"id": "2", "function": {"name": "valid_fn", "arguments": '{"x": 2}'}},
    ]
    norm_calls = normalize_tool_calls(raw_calls)
    assert len(norm_calls) == 1
    assert norm_calls[0]["function"]["name"] == "valid_fn"

    norm_msgs = normalize_tool_results([
        "not-a-dict",
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Выполняю вызов"}],
            "tool_calls": [{"id": "c1", "function": {"name": "fn1", "arguments": "{}"}}],
        },
        {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": "hmm"}, {"type": "text", "text": "Ответ"}],
        },
    ])
    assert len(norm_msgs) == 2
    assert norm_msgs[0]["content"] == "Выполняю вызов"
    assert norm_msgs[1]["content"] == "Ответ"

    cached_list = apply_prompt_cache_control([
        {"role": "system", "content": [{"type": "text", "text": "sys"}]}
    ])
    assert cached_list[0]["content"][-1]["cache_control"] == {"type": "ephemeral"}

    assert inject_dynamic_context([{"role": "user", "content": "hi"}], "  ") == [{"role": "user", "content": "hi"}]
    assert inject_dynamic_context([], "ctx")[0]["content"] == "ctx"
    only_sys = inject_dynamic_context([{"role": "system", "content": "s"}], "ctx")
    assert only_sys[-1] == {"role": "user", "content": "ctx"}
    list_user = inject_dynamic_context([{"role": "user", "content": [{"type": "text", "text": "u"}]}], "ctx")
    assert len(list_user[0]["content"]) == 2
    none_user = inject_dynamic_context([{"role": "user", "content": None}], "ctx")
    assert none_user[0]["content"] == "ctx"

    with patch.object(Path, "read_text", side_effect=OSError("locked")):
        (tmp_path / ".env").write_text("X=1\n", encoding="utf-8")
        assert read_env_api_key("X", tmp_path) == ""

    cfg_file = tmp_path / "config.json"
    cfg_or = {
        **DEFAULT_CONFIG,
        "active_provider": "openrouter",
        "providers": {
            "antigravity": {"base_url": "http://127.0.0.1:3000/v1", "api_key_env": "ANTIGRAVITY_API_KEY", "fallback_to_openrouter": False, "fallback_ports": []},
            "openrouter": {"base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
        },
    }
    cfg_file.write_text(json.dumps(cfg_or), encoding="utf-8")
    client = DualBackendClient(root_dir=tmp_path)
    state_mgr = client.order_manager
    st = state_mgr.get_order_sync("econ_ord")
    st.economy_mode = True
    st.save()

    class _FakeResp:
        def __init__(self, payload: dict, status: int = 200) -> None:
            self._payload = payload
            self.status = status

        def read(self) -> bytes:
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

    with patch("guard.config.CONFIG_PATH", cfg_file):
        with patch("urllib.request.urlopen", return_value=_FakeResp({"choices": [{"message": {"role": "assistant", "content": "or"}}]})):
            res_sync, prov_sync = client.chat_completion_sync(
                payload={"messages": [{"role": "user", "content": "code"}]},
                task_type="bsl",
                order_id="econ_ord",
                api_key_override="override-k",
            )
            assert prov_sync == "openrouter"
            assert res_sync["choices"][0]["message"]["content"] == "or"

        norm_resp = client._normalize_response_payload(
            "antigravity",
            {
                "choices": [
                    "bad",
                    {"message": "bad"},
                    {"message": {"role": "assistant", "content": "hi", "tool_calls": [{"id": "1", "function": {"name": "f", "arguments": "{}"}}]}},
                ]
            },
        )
        assert norm_resp["choices"][2]["message"]["tool_calls"][0]["function"]["name"] == "f"

        err_400_or = urllib.error.HTTPError("https://openrouter.ai/api/v1/chat/completions", 400, "Bad Req", {}, None)
        with patch("urllib.request.urlopen", side_effect=err_400_or):
            with pytest.raises(urllib.error.HTTPError):
                client.chat_completion_sync(payload={"messages": []}, provider_override="openrouter")

        err_400_agy = urllib.error.HTTPError("http://127.0.0.1:3000/v1/chat/completions", 400, "Bad AGY", {}, None)
        cfg_agy_only = {**cfg_or, "active_provider": "antigravity"}
        cfg_file.write_text(json.dumps(cfg_agy_only), encoding="utf-8")
        with patch("urllib.request.urlopen", side_effect=err_400_agy):
            with pytest.raises(urllib.error.HTTPError):
                client.chat_completion_sync(payload={"messages": []}, provider_override="antigravity")

        with patch.object(client, "_attempt_candidates", return_value=[]):
            with pytest.raises(RuntimeError):
                client.chat_completion_sync(payload={"messages": []})
            with pytest.raises(RuntimeError):
                await client.chat_completion_async(payload={"messages": []})

        async def _fail_post(self_c, url, json=None, headers=None):
            raise httpx.ConnectError("down", request=httpx.Request("POST", url))

        with patch.object(httpx.AsyncClient, "post", new=_fail_post):
            with pytest.raises(httpx.HTTPError):
                await client.chat_completion_async(payload={"messages": []})

        with patch("urllib.request.urlopen", return_value=_FakeResp({"data": []}, status=200)):
            ok_200, url_200 = client.check_antigravity_health(timeout=0.3)
            assert ok_200 is True
            assert "3000" in url_200

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-test-isolated"}):
            isolated_cands = client._attempt_candidates(DEFAULT_CONFIG, "antigravity")
            assert isolated_cands
            assert all(prov == "antigravity" for prov, _, _ in isolated_cands)


