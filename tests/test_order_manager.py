"""
tests/test_order_manager.py - Тестирование менеджера заказов и гарантийного контура (tools/order.py).
"""
import datetime
import json
import subprocess
import sys
from pathlib import Path
import pytest

ORDER_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "order.py"


def run_order_cli(args: list[str], orders_dir: Path) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(ORDER_SCRIPT), "--orders-dir", str(orders_dir), *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )


def test_order_lifecycle(tmp_path):
    orders_dir = tmp_path / "Orders"

    # 1. Создание нового заказа
    p_new = run_order_cli([
        "new", "vpf", "komplekto_vpf",
        "--client", "komplekto",
        "--title", "Печатная форма Счета",
        "--price", "4000",
        "--deadline", "2",
        "--config", "1С:УТ 11",
        "--platform", "8.3.24"
    ], orders_dir)
    assert p_new.returncode == 0
    assert "успешно создан" in p_new.stdout

    order_folder = orders_dir / "komplekto_vpf"
    assert order_folder.is_dir()
    assert (order_folder / "order.json").is_file()
    assert (order_folder / "brief.md").is_file()
    assert (order_folder / "DECISIONS.md").is_file()
    assert (order_folder / ".roo" / "mcp.json").is_file()
    assert (order_folder / "src").is_dir()
    assert (order_folder / "delivery").is_dir()

    # Проверка данных в order.json
    oj = json.loads((order_folder / "order.json").read_text(encoding="utf-8"))
    assert oj["client_name"] == "komplekto"
    assert oj["price_rub"] == 4000.0
    assert oj["status"] == "in_progress"
    assert oj["config_info"]["name"] == "1С:УТ 11"

    # Проверка дашборда CLIENTS.md
    dashboard = orders_dir / "CLIENTS.md"
    assert dashboard.is_file()
    dash_text = dashboard.read_text(encoding="utf-8")
    assert "komplekto" in dash_text
    assert "4,000 ₽" in dash_text

    # 2. Обновление заказа (учет часов и правка)
    p_upd = run_order_cli([
        "update", "komplekto_vpf",
        "--hours", "1.5",
        "--price", "4500",
        "--note", "Согласовано добавление автоуслуги"
    ], orders_dir)
    assert p_upd.returncode == 0
    oj_updated = json.loads((order_folder / "order.json").read_text(encoding="utf-8"))
    assert oj_updated["hours_spent"] == 1.5
    assert oj_updated["price_rub"] == 4500.0
    assert any("автоуслуги" in h.get("note", "") for h in oj_updated.get("history", []))

    # 3. Сдача заказа (deliver)
    p_del = run_order_cli(["deliver", "komplekto_vpf"], orders_dir)
    assert p_del.returncode == 0
    assert "успешно переведён в статус 'delivered'" in p_del.stdout
    oj_delivered = json.loads((order_folder / "order.json").read_text(encoding="utf-8"))
    assert oj_delivered["status"] == "delivered"
    assert oj_delivered["delivered_at"] == datetime.date.today().isoformat()

    # Проверка Git-тега сдачи
    proc_tags = subprocess.run(["git", "tag"], cwd=str(order_folder), capture_output=True, text=True)
    assert "delivered-" in proc_tags.stdout

    # 4. Гарантийный инцидент через месяц (warranty)
    p_war = run_order_cli([
        "warranty", "komplekto_vpf",
        "--issue", "Ошибка при делении на 0 в строке автоуслуги"
    ], orders_dir)
    assert p_war.returncode == 0
    assert "Активирован гарантийный контур" in p_war.stdout

    oj_war = json.loads((order_folder / "order.json").read_text(encoding="utf-8"))
    assert oj_war["status"] == "warranty"

    # Проверка Git-ветки warranty
    proc_branch = subprocess.run(["git", "branch", "--show-current"], cwd=str(order_folder), capture_output=True, text=True)
    assert "warranty/" in proc_branch.stdout.strip()

    # Проверка записи в DECISIONS.md
    dec_text = (order_folder / "DECISIONS.md").read_text(encoding="utf-8")
    assert "Ошибка при делении на 0" in dec_text


def test_order_list_and_alerts(tmp_path):
    orders_dir = tmp_path / "AlertOrders"

    # Создаем просроченный заказ
    yesterday = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    run_order_cli([
        "new", "report", "expired_report",
        "--client", "Иван",
        "--deadline", yesterday
    ], orders_dir)

    p_list = run_order_cli(["list"], orders_dir)
    assert p_list.returncode == 0
    assert "expired_report" in p_list.stdout

    dash_text = (orders_dir / "CLIENTS.md").read_text(encoding="utf-8")
    assert "ПРОСРОЧЕН ДЕДЛАЙН" in dash_text
    assert "expired_report" in dash_text
