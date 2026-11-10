from __future__ import annotations

import json
import logging
from typing import Optional
import urllib.error
import urllib.request

from guard.state import OrderState

logger = logging.getLogger("kwork-guard.billing")

MODEL_PRICING_PER_1M: dict[str, tuple[float, float]] = {
    "anthropic/claude-3-7-sonnet": (3.0, 15.0),
    "anthropic/claude-sonnet-4": (3.0, 15.0),
    "claude-3-7-sonnet": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "google/gemini-2.5-flash": (0.15, 0.60),
    "google/gemini-3-flash": (0.15, 0.60),
    "gemini-2.5-flash": (0.15, 0.60),
    "gemini-3-flash": (0.15, 0.60),
}


def fetch_openrouter_usage(api_key: str, timeout: float = 5.0) -> dict[str, Optional[float]]:
    fallback: dict[str, Optional[float]] = {
        "usage_usd": None,
        "limit_usd": None,
        "remaining_usd": None,
    }
    if not api_key:
        return fallback

    request_url = "https://openrouter.ai/api/v1/key"
    req = urllib.request.Request(
        request_url,
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "User-Agent": "kwork-guard",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            data = payload.get("data", {})
            usage = data.get("usage")
            limit = data.get("limit")
            usage_val = float(usage) if usage is not None else None
            limit_val = float(limit) if limit is not None else None
            remaining_val = (limit_val - usage_val) if (limit_val is not None and usage_val is not None) else None

            return {
                "usage_usd": usage_val,
                "limit_usd": limit_val,
                "remaining_usd": remaining_val,
            }
    except Exception as exc:
        logger.warning("OpenRouter billing request failure: %s", exc)
        return fallback


def estimate_virtual_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    normalized = model.strip().lower()
    in_price, out_price = MODEL_PRICING_PER_1M.get(normalized, (3.0, 15.0))
    for known_model, pricing in MODEL_PRICING_PER_1M.items():
        if known_model in normalized or normalized in known_model:
            in_price, out_price = pricing
            break
    cost_usd = (max(0, input_tokens) / 1_000_000.0) * in_price + (max(0, output_tokens) / 1_000_000.0) * out_price
    return round(cost_usd, 6)


def record_order_cost_delta(
    state: OrderState,
    current_usage_usd: float,
    usd_to_rub: float,
) -> None:
    if current_usage_usd < 0:
        return
    state.real_cost_usd = round(current_usage_usd, 4)
    state.real_cost_rub = round(current_usage_usd * usd_to_rub, 2)
    state.save()


def record_llm_token_usage(
    state: OrderState,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    usd_to_rub: float = 95.0,
) -> dict[str, float | int | str]:
    safe_in = max(0, int(input_tokens))
    safe_out = max(0, int(output_tokens))
    virtual_usd = estimate_virtual_cost(model, safe_in, safe_out)
    virtual_rub = round(virtual_usd * usd_to_rub, 4)

    state.input_tokens += safe_in
    state.output_tokens += safe_out
    state.virtual_cost_usd = round(state.virtual_cost_usd + virtual_usd, 6)
    state.virtual_cost_rub = round(state.virtual_cost_rub + virtual_rub, 2)

    is_subscription = provider.strip().lower() == "antigravity"
    if not is_subscription:
        state.real_cost_usd = round(state.real_cost_usd + virtual_usd, 6)
        state.real_cost_rub = round(state.real_cost_rub + virtual_rub, 2)

    state.save()
    return {
        "mode": "virtual_cost" if is_subscription else "real_cost",
        "virtual_usd": state.virtual_cost_usd,
        "virtual_rub": state.virtual_cost_rub,
        "real_usd": state.real_cost_usd,
        "real_rub": state.real_cost_rub,
        "input_tokens": state.input_tokens,
        "output_tokens": state.output_tokens,
    }
