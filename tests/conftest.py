import os
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import edt_guard


@pytest.fixture(autouse=True)
def clean_test_state():
    # Очищаем файлы состояния заказов на диске
    state_dir = Path(__file__).resolve().parent.parent / "state"
    if state_dir.is_dir():
        for f in state_dir.glob("*.json"):
            if f.name not in ("events.json", "events.jsonl"):
                try:
                    f.unlink()
                except Exception:
                    pass
    edt_guard.ORDER_MGR.orders.clear()
    edt_guard.CACHE.clear()
    for g in edt_guard.GUARDS.values():
        g.same.clear()
        g.consec = 0
