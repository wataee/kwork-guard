from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Optional

from guard.state import OrderState

logger = logging.getLogger("kwork-guard.billing")


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
