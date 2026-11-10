from __future__ import annotations

import copy
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Optional
import urllib.error
import urllib.parse
import urllib.request

import httpx

from guard.config import (
    get_active_provider,
    get_provider_settings,
    load_config,
    select_model_for_task,
)
from guard.openrouter_billing import record_llm_token_usage
from guard.state import OrderManager

logger = logging.getLogger("kwork-guard.dual-backend")

THINKING_TAG_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL | re.IGNORECASE)
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_thinking_and_clean_text(content: Any) -> tuple[list[str], str]:
    thoughts: list[str] = []
    if isinstance(content, list):
        text_parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                text_parts.append(str(block))
                continue
            btype = str(block.get("type", "")).lower()
            if btype == "thinking":
                t_val = str(block.get("thinking") or block.get("text") or "").strip()
                if t_val:
                    thoughts.append(t_val)
            elif btype == "text":
                raw_txt = str(block.get("text") or "")
                found_tags = THINKING_TAG_RE.findall(raw_txt)
                thoughts.extend(t.strip() for t in found_tags if t.strip())
                cleaned = THINKING_TAG_RE.sub("", raw_txt).strip()
                if cleaned:
                    text_parts.append(cleaned)
        return thoughts, "\n".join(text_parts)

    raw_str = str(content or "")
    for match in THINKING_TAG_RE.finditer(raw_str):
        thoughts.append(match.group(0).strip())
    cleaned_str = THINKING_TAG_RE.sub("", raw_str).strip()
    return thoughts, cleaned_str


def strip_empty_schema_fields(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for k, v in value.items():
            sub = strip_empty_schema_fields(v)
            if sub is None:
                continue
            if sub == "" or sub == {} or sub == []:
                continue
            cleaned[k] = sub
        return cleaned
    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            sub = strip_empty_schema_fields(item)
            if sub is None or sub == {} or sub == "" or sub == []:
                continue
            cleaned_list.append(sub)
        return cleaned_list
    return value


def parse_clean_json_arguments(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        cleaned = strip_empty_schema_fields(raw_args)
        return cleaned if isinstance(cleaned, dict) else {}

    _, clean_str = extract_thinking_and_clean_text(raw_args)
    fence_match = JSON_FENCE_RE.search(clean_str)
    candidate = fence_match.group(1).strip() if fence_match else clean_str

    if not candidate:
        return {}

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            cleaned = strip_empty_schema_fields(parsed)
            return cleaned if isinstance(cleaned, dict) else {}
    except json.JSONDecodeError:
        brace_start = candidate.find("{")
        brace_end = candidate.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            try:
                parsed = json.loads(candidate[brace_start : brace_end + 1])
                if isinstance(parsed, dict):
                    cleaned = strip_empty_schema_fields(parsed)
                    return cleaned if isinstance(cleaned, dict) else {}
            except json.JSONDecodeError as err:
                logger.warning("Failed to decode tool JSON arguments: %s", err)
    return {}


def normalize_tool_calls(raw_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for idx, call in enumerate(raw_calls):
        if not isinstance(call, dict):
            continue
        call_id = str(call.get("id") or f"call_{idx}")
        fn = call.get("function")
        if isinstance(fn, dict):
            fn_name = str(fn.get("name") or "")
            parsed_args = parse_clean_json_arguments(fn.get("arguments"))
        else:
            fn_name = str(call.get("name") or "")
            parsed_args = parse_clean_json_arguments(call.get("input") or call.get("arguments"))

        if not fn_name:
            continue

        normalized.append({
            "id": call_id,
            "type": "function",
            "function": {
                "name": fn_name,
                "arguments": json.dumps(parsed_args, ensure_ascii=False),
            },
        })
    return normalized


def normalize_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        cloned = copy.deepcopy(msg)
        role = cloned.get("role")

        if role == "assistant" and isinstance(cloned.get("content"), (list, str)):
            _, cleaned_txt = extract_thinking_and_clean_text(cloned["content"])
            if cloned.get("tool_calls"):
                cloned["tool_calls"] = normalize_tool_calls(cloned["tool_calls"])
                if not cleaned_txt:
                    cloned["content"] = None
                else:
                    cloned["content"] = cleaned_txt
            elif isinstance(cloned.get("content"), list):
                cloned["content"] = cleaned_txt

        if role == "tool":
            _, cleaned_txt = extract_thinking_and_clean_text(cloned.get("content"))
            cloned["content"] = cleaned_txt or "{}"
            cleaned_msg = {k: v for k, v in cloned.items() if v is not None and v != ""}
            out.append(cleaned_msg)
            continue

        out.append(cloned)
    return out


def apply_prompt_cache_control(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for msg in messages:
        cloned = copy.deepcopy(msg)
        if cloned.get("role") == "system":
            content = cloned.get("content")
            if isinstance(content, str):
                cloned["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            elif isinstance(content, list) and content:
                last_block = content[-1]
                if isinstance(last_block, dict) and "cache_control" not in last_block:
                    last_block["cache_control"] = {"type": "ephemeral"}
        result.append(cloned)
    return result


def build_cached_system_block(static_rules_text: str) -> dict[str, Any]:
    return {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": static_rules_text,
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }


def inject_dynamic_context(
    messages: list[dict[str, Any]],
    dynamic_context: str,
) -> list[dict[str, Any]]:
    if not dynamic_context or not dynamic_context.strip():
        return copy.deepcopy(messages)

    cloned = copy.deepcopy(messages)
    if not cloned:
        cloned.append({"role": "user", "content": dynamic_context.strip()})
        return cloned

    target_idx: Optional[int] = None
    for idx in range(len(cloned) - 1, -1, -1):
        role = cloned[idx].get("role")
        if role in ("tool", "user"):
            target_idx = idx
            break

    if target_idx is None:
        cloned.append({"role": "user", "content": dynamic_context.strip()})
        return cloned

    target_msg = cloned[target_idx]
    existing = target_msg.get("content")
    addition = f"\n\n[Динамический контекст]\n{dynamic_context.strip()}"

    if isinstance(existing, str):
        target_msg["content"] = f"{existing}{addition}".lstrip("\n")
    elif isinstance(existing, list):
        existing.append({"type": "text", "text": addition})
    else:
        target_msg["content"] = dynamic_context.strip()

    return cloned


def read_env_api_key(env_name: str, root_dir: Optional[Path] = None) -> str:
    env_val = os.getenv(env_name, "").strip()
    if env_val:
        return env_val
    base_dir = root_dir if root_dir else Path(__file__).resolve().parent.parent
    env_file = base_dir / ".env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{env_name}="):
                    return stripped.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception as exc:
            logger.warning("Failed to read %s from .env: %s", env_name, exc)
    return ""


class DualBackendClient:
    def __init__(
        self,
        order_manager: Optional[OrderManager] = None,
        root_dir: Optional[Path] = None,
    ) -> None:
        self.root_dir = root_dir if root_dir else Path(__file__).resolve().parent.parent
        self.order_manager = order_manager if order_manager else OrderManager(self.root_dir / "state")

    def _build_antigravity_urls(self, cfg: dict[str, Any]) -> list[str]:
        agy = get_provider_settings("antigravity", cfg)
        primary_url = str(agy.get("base_url") or "http://127.0.0.1:3000/v1").rstrip("/")
        urls = [primary_url]
        parsed = urllib.parse.urlparse(primary_url)
        host = parsed.hostname or "127.0.0.1"
        scheme = parsed.scheme or "http"
        path = parsed.path or "/v1"

        for port in agy.get("fallback_ports", []):
            candidate = f"{scheme}://{host}:{int(port)}{path}".rstrip("/")
            if candidate not in urls:
                urls.append(candidate)
        return urls

    def _prepare_request(
        self,
        payload: dict[str, Any],
        provider: str,
        task_type: str,
        order_id: str,
        dynamic_context: str = "",
    ) -> tuple[dict[str, Any], str, float]:
        cfg = load_config()
        state = self.order_manager.get_order_sync(order_id)
        economy_mode = bool(state.economy_mode)
        usd_to_rub = float(cfg.get("openrouter", {}).get("usd_to_rub", 95.0))

        routed_model = payload.get("model") or select_model_for_task(task_type, cfg, economy_mode=economy_mode)
        if economy_mode and task_type in ("bsl", "skd", "dcs", "edt_mcp"):
            routed_model = select_model_for_task(task_type, cfg, economy_mode=True)

        messages = copy.deepcopy(payload.get("messages", []))
        if provider == "antigravity":
            messages = normalize_tool_results(messages)

        messages = apply_prompt_cache_control(messages)
        if dynamic_context:
            messages = inject_dynamic_context(messages, dynamic_context)

        prepared = copy.deepcopy(payload)
        prepared["model"] = routed_model
        prepared["messages"] = messages
        if provider == "antigravity" and prepared.get("tools"):
            cleaned_tools = []
            for tool_def in prepared["tools"]:
                if isinstance(tool_def, dict):
                    cleaned_tools.append(strip_empty_schema_fields(tool_def))
            prepared["tools"] = cleaned_tools

        return prepared, str(routed_model), usd_to_rub

    def _normalize_response_payload(self, provider: str, resp_json: dict[str, Any]) -> dict[str, Any]:
        if provider != "antigravity":
            return resp_json
        cloned = copy.deepcopy(resp_json)
        choices = cloned.get("choices")
        if isinstance(choices, list):
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                message = choice.get("message")
                if not isinstance(message, dict):
                    continue
                if "content" in message:
                    _, clean_txt = extract_thinking_and_clean_text(message.get("content"))
                    message["content"] = clean_txt if clean_txt else None
                if isinstance(message.get("tool_calls"), list):
                    message["tool_calls"] = normalize_tool_calls(message["tool_calls"])
        return cloned

    def _record_usage_from_response(
        self,
        order_id: str,
        provider: str,
        model: str,
        resp_json: dict[str, Any],
        usd_to_rub: float,
    ) -> dict[str, Any]:
        usage = resp_json.get("usage") or {}
        in_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        out_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        state = self.order_manager.get_order_sync(order_id)
        return record_llm_token_usage(
            state=state,
            provider=provider,
            model=model,
            input_tokens=in_tokens,
            output_tokens=out_tokens,
            usd_to_rub=usd_to_rub,
        )

    def _attempt_candidates(self, cfg: dict[str, Any], explicit_provider: Optional[str]) -> list[tuple[str, str, str]]:
        active = explicit_provider or get_active_provider(cfg)
        candidates: list[tuple[str, str, str]] = []

        if active == "antigravity":
            agy_cfg = get_provider_settings("antigravity", cfg)
            agy_key = read_env_api_key(str(agy_cfg.get("api_key_env", "ANTIGRAVITY_API_KEY")), self.root_dir)
            for base_url in self._build_antigravity_urls(cfg):
                candidates.append(("antigravity", base_url, agy_key))
            if agy_cfg.get("fallback_to_openrouter", False):
                or_cfg = get_provider_settings("openrouter", cfg)
                or_url = str(or_cfg.get("base_url") or "https://openrouter.ai/api/v1").rstrip("/")
                or_key = read_env_api_key(str(or_cfg.get("api_key_env", "OPENROUTER_API_KEY")), self.root_dir)
                if or_key:
                    candidates.append(("openrouter", or_url, or_key))
        else:
            or_cfg = get_provider_settings("openrouter", cfg)
            or_url = str(or_cfg.get("base_url") or "https://openrouter.ai/api/v1").rstrip("/")
            or_key = read_env_api_key(str(or_cfg.get("api_key_env", "OPENROUTER_API_KEY")), self.root_dir)
            candidates.append(("openrouter", or_url, or_key))

        return candidates

    def chat_completion_sync(
        self,
        payload: dict[str, Any],
        task_type: str = "intake",
        order_id: str = "default",
        dynamic_context: str = "",
        api_key_override: str = "",
        provider_override: Optional[str] = None,
        timeout: float = 15.0,
    ) -> tuple[dict[str, Any], str]:
        cfg = load_config()
        candidates = self._attempt_candidates(cfg, provider_override)
        last_error: Exception | None = None

        for provider, base_url, env_key in candidates:
            prepared, model_name, usd_to_rub = self._prepare_request(
                payload=payload,
                provider=provider,
                task_type=task_type,
                order_id=order_id,
                dynamic_context=dynamic_context,
            )
            effective_key = api_key_override.strip() if api_key_override.strip() and provider == "openrouter" else env_key
            if not effective_key and provider == "openrouter" and api_key_override.strip():
                effective_key = api_key_override.strip()

            headers = {
                "Content-Type": "application/json",
                "User-Agent": "kwork-guard-dual-backend",
            }
            if effective_key:
                headers["Authorization"] = f"Bearer {effective_key}"

            endpoint = f"{base_url}/chat/completions"
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(prepared).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw_body = json.loads(resp.read().decode("utf-8"))
                    normalized = self._normalize_response_payload(provider, raw_body)
                    self._record_usage_from_response(order_id, provider, model_name, normalized, usd_to_rub)
                    return normalized, provider
            except urllib.error.HTTPError as http_err:
                last_error = http_err
                logger.warning("Sync backend %s (%s) HTTP %s: %s", provider, endpoint, http_err.code, http_err)
                if http_err.code == 429 or http_err.code >= 500:
                    continue
                if provider == "antigravity":
                    continue
                raise
            except Exception as exc:
                last_error = exc
                logger.warning("Sync backend %s (%s) failed: %s", provider, endpoint, exc)
                continue

        if last_error:
            raise last_error
        raise RuntimeError("No available LLM backend candidates")

    async def chat_completion_async(
        self,
        payload: dict[str, Any],
        task_type: str = "bsl",
        order_id: str = "default",
        dynamic_context: str = "",
        api_key_override: str = "",
        provider_override: Optional[str] = None,
        timeout: float = 60.0,
    ) -> tuple[dict[str, Any], str]:
        cfg = load_config()
        candidates = self._attempt_candidates(cfg, provider_override)
        last_error: Exception | None = None

        async with httpx.AsyncClient(timeout=timeout) as client:
            for provider, base_url, env_key in candidates:
                prepared, model_name, usd_to_rub = self._prepare_request(
                    payload=payload,
                    provider=provider,
                    task_type=task_type,
                    order_id=order_id,
                    dynamic_context=dynamic_context,
                )
                effective_key = api_key_override.strip() if api_key_override.strip() else env_key
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "kwork-guard-dual-backend",
                }
                if effective_key:
                    headers["Authorization"] = f"Bearer {effective_key}"

                endpoint = f"{base_url}/chat/completions"
                try:
                    resp = await client.post(endpoint, json=prepared, headers=headers)
                    if resp.status_code == 429 or resp.status_code >= 500:
                        logger.warning("Async backend %s (%s) returned HTTP %s, falling back", provider, endpoint, resp.status_code)
                        last_error = httpx.HTTPStatusError(
                            f"HTTP {resp.status_code}",
                            request=resp.request,
                            response=resp,
                        )
                        continue
                    resp.raise_for_status()
                    raw_body = resp.json()
                    normalized = self._normalize_response_payload(provider, raw_body)
                    self._record_usage_from_response(order_id, provider, model_name, normalized, usd_to_rub)
                    return normalized, provider
                except httpx.HTTPError as exc:
                    last_error = exc
                    logger.warning("Async backend %s (%s) error: %s", provider, endpoint, exc)
                    continue

        if last_error:
            raise last_error
        raise RuntimeError("No available LLM backend candidates")

    def check_antigravity_health(self, timeout: float = 1.0) -> tuple[bool, str]:
        cfg = load_config()
        urls = self._build_antigravity_urls(cfg)
        for base_url in urls:
            probe_url = f"{base_url}/models"
            req = urllib.request.Request(probe_url, headers={"User-Agent": "kwork-guard"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if 200 <= resp.status < 500:
                        return True, base_url
            except urllib.error.HTTPError as http_err:
                if http_err.code in (200, 401, 403, 404, 405):
                    return True, base_url
            except Exception as exc:
                logger.debug("Antigravity probe failed for %s: %s", probe_url, exc)
        return False, urls[0] if urls else "http://127.0.0.1:3000/v1"
