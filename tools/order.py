from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Optional

logger = logging.getLogger("kwork-guard.order")

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception as stream_exc:
        logger.warning("Stream reconfigure failed: %s", stream_exc)

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.json"
TEMPLATES_DIR = ROOT_DIR / "templates"
MCP_CONFIGS_DIR = ROOT_DIR / "mcp_configs"
STATE_DIR = ROOT_DIR / "state"

VALID_KINDS = {"vpf", "report", "processor", "ext", "intake", "project"}
VALID_STATUSES = {"lead", "in_progress", "review", "delivered", "warranty", "cancelled"}

STATUS_EMOJI = {
    "lead": "🟡 Этап согласования",
    "in_progress": "🟢 В работе",
    "review": "🔵 На проверке у клиента",
    "delivered": "✅ Сдан / Завершён",
    "warranty": "🔧 Гарантийная доработка",
    "cancelled": "⚪ Отменён",
}


def build_mcp_config(
    profile: str,
    order_id: str,
    guard_host: str = "127.0.0.1",
    guard_port: int = 8766,
) -> dict[str, Any]:
    if profile == "intake":
        return {"mcpServers": {}}
    mapped_profile = profile if profile in ("vpf", "report", "ext", "processor") else "ext"
    return {
        "mcpServers": {
            "edt": {
                "type": "streamable-http",
                "url": f"http://{guard_host}:{guard_port}/mcp/{mapped_profile}/{order_id}",
                "headers": {"X-Order-Id": order_id},
                "timeout": 600,
            }
        }
    }


def repair_order_mcp_config(
    order_dir: Path,
    guard_host: str = "127.0.0.1",
    guard_port: int = 8766,
) -> bool:
    try:
        if not order_dir.is_dir():
            return False
        data = load_order_json(order_dir) or {}
        kind = data.get("kind", "ext")
        order_id = data.get("order_id", order_dir.name)
        mcp_profile = kind if kind in ("vpf", "report", "ext", "intake") else "ext"
        cfg = build_mcp_config(mcp_profile, order_id, guard_host, guard_port)
        roo_dir = order_dir / ".roo"
        roo_dir.mkdir(parents=True, exist_ok=True)
        (roo_dir / "mcp.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as exc:
        logger.warning("Failed to repair MCP config for %s: %s", order_dir, exc)
        return False


def get_orders_dir(override: Optional[str] = None) -> Path:
    if override:
        return Path(override)
    env_dir = os.getenv("ORDERS_DIR")
    if env_dir:
        return Path(env_dir)
    if CONFIG_PATH.is_file():
        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as file_handle:
                cfg = json.load(file_handle)
                configured = cfg.get("workspace", {}).get("orders_dir")
                if configured:
                    return Path(configured)
        except Exception as exc:
            logger.warning("Config read orders_dir error: %s", exc)
    return Path(r"C:\Orders")


def load_order_json(order_dir: Path) -> Optional[dict[str, Any]]:
    p = order_dir / "order.json"
    if not p.is_file():
        return None
    try:
        with p.open("r", encoding="utf-8") as file_handle:
            return json.load(file_handle)
    except Exception as exc:
        logger.warning("Failed to load order.json in %s: %s", order_dir, exc)
        return None


def save_order_json(order_dir: Path, data: dict[str, Any]) -> None:
    p = order_dir / "order.json"
    with p.open("w", encoding="utf-8") as file_handle:
        json.dump(data, file_handle, ensure_ascii=False, indent=2)


def get_token_stats(order_id: str) -> dict[str, Any]:
    state_file = STATE_DIR / f"{order_id}.json"
    calls = 0
    chars = 0
    errors = 0
    if state_file.is_file():
        try:
            with state_file.open("r", encoding="utf-8") as file_handle:
                sd = json.load(file_handle)
                calls = sd.get("calls_count", 0)
                chars = sd.get("total_response_chars", 0)
                errors = sd.get("errors_count", 0)
        except Exception as exc:
            logger.warning("Token stats read error for %s: %s", order_id, exc)

    tokens = int(chars / 3.5)
    cost_usd = (tokens / 1_000_000) * 3.00
    cost_rub = cost_usd * 95.0

    return {
        "calls": calls,
        "chars": chars,
        "errors": errors,
        "tokens": tokens,
        "cost_usd": round(cost_usd, 4),
        "cost_rub": round(cost_rub, 2),
    }


def git_cmd(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.name=kwork-guard", "-c", "user.email=guard@kwork.local", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def cmd_new(args: argparse.Namespace) -> int:
    orders_dir = get_orders_dir(args.orders_dir)
    orders_dir.mkdir(parents=True, exist_ok=True)

    order_slug = re.sub(r"[^A-Za-z0-9_-]", "_", args.slug.strip()).strip("_")
    if not order_slug:
        print("[!] Ошибка: некорректный идентификатор заказа (slug).")
        return 1

    kind = args.kind.lower()
    if kind not in VALID_KINDS:
        print(f"[!] Ошибка: недопустимый тип '{kind}'. Допустимые: {sorted(VALID_KINDS)}")
        return 1

    order_dir = orders_dir / order_slug
    if order_dir.exists() and any(order_dir.iterdir()):
        print(f"[!] Ошибка: папка '{order_dir}' уже существует и не пуста.")
        return 1

    order_dir.mkdir(parents=True, exist_ok=True)
    (order_dir / "src").mkdir(exist_ok=True)
    (order_dir / "delivery").mkdir(exist_ok=True)

    roo_dir = order_dir / ".roo"
    roo_dir.mkdir(exist_ok=True)

    mcp_profile = kind if kind in ("vpf", "report", "ext", "intake") else "ext"
    mcp_cfg = build_mcp_config(mcp_profile, order_slug, "127.0.0.1", 8766)
    (roo_dir / "mcp.json").write_text(json.dumps(mcp_cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    roomodes_src = ROOT_DIR / ".roomodes"
    if roomodes_src.is_file():
        shutil.copy2(roomodes_src, order_dir / ".roomodes")

    rules_src_dir = ROOT_DIR / ".roo" / f"rules-kwork-{mcp_profile}"
    if rules_src_dir.is_dir():
        dst_rules = roo_dir / f"rules-kwork-{mcp_profile}"
        if dst_rules.exists():
            shutil.rmtree(dst_rules)
        shutil.copytree(rules_src_dir, dst_rules)

    now_iso = datetime.datetime.now().isoformat()
    now_str = datetime.date.today().isoformat()
    deadline_str = ""
    if args.deadline:
        if args.deadline.isdigit():
            dl = datetime.date.today() + datetime.timedelta(days=int(args.deadline))
            deadline_str = dl.isoformat()
        else:
            deadline_str = args.deadline

    order_data = {
        "order_id": order_slug,
        "client_name": args.client or "Заказчик",
        "title": args.title or order_slug,
        "kwork_url": args.url or "",
        "kind": kind,
        "status": args.status if getattr(args, "status", None) else ("lead" if kind == "intake" else "in_progress"),
        "price_rub": float(args.price or 0.0),
        "prepaid_rub": float(args.prepaid or 0.0),
        "hours_spent": 0.0,
        "created_at": now_str,
        "deadline": deadline_str,
        "delivered_at": None,
        "config_info": {
            "name": args.config or "Типовая 1С",
            "version": "",
            "platform": args.platform or "8.3.24",
        },
        "tags": [kind],
        "history": [
            {"ts": now_iso, "event": "created", "note": "Инициализация заказа в системе"}
        ],
    }
    save_order_json(order_dir, order_data)

    brief_tmpl = TEMPLATES_DIR / "brief.md"
    if brief_tmpl.is_file():
        brief_text = brief_tmpl.read_text(encoding="utf-8")
        brief_text = brief_text.replace("{{order_id}}", order_slug)
        brief_text = brief_text.replace("{{client_name}}", args.client or "Заказчик")
        brief_text = brief_text.replace("{{kind}}", kind)
        brief_text = brief_text.replace("{{object_name}}", order_slug)
        brief_text = brief_text.replace("{{configuration}}", args.config or "1С:Предприятие")
        brief_text = brief_text.replace("{{target_version}}", args.platform or "8.3")
        brief_text = brief_text.replace("{{kwork_url}}", args.url or "https://kwork.ru")
        (order_dir / "brief.md").write_text(brief_text, encoding="utf-8")

    dec_tmpl = TEMPLATES_DIR / "DECISIONS.md"
    if dec_tmpl.is_file():
        dec_text = dec_tmpl.read_text(encoding="utf-8")
        dec_text = dec_text.replace("{{order_id}}", order_slug)
        dec_text = dec_text.replace("{{client_name}}", args.client or "Заказчик")
        dec_text = dec_text.replace("{{config_name}}", args.config or "1С")
        dec_text = dec_text.replace("{{platform_ver}}", args.platform or "8.3.24")
        dec_text = dec_text.replace("{{created_at}}", now_str)
        dec_text = dec_text.replace("{{objective}}", args.title or order_slug)
        (order_dir / "DECISIONS.md").write_text(dec_text, encoding="utf-8")

    git_cmd(order_dir, "init")
    git_cmd(order_dir, "config", "user.name", "kwork-guard")
    git_cmd(order_dir, "config", "user.email", "guard@kwork.local")
    git_cmd(order_dir, "config", "core.quotepath", "false")
    git_cmd(order_dir, "add", ".")
    git_cmd(order_dir, "commit", "-m", f"chore: initial commit for order {order_slug}")

    sync_dashboard(orders_dir)

    print("\n" + "=" * 65)
    print(f"✓ Заказ '{order_slug}' успешно создан!")
    print(f"  Клиент:    {order_data['client_name']}")
    print(f"  Тип:       {kind} (профиль MCP: {mcp_profile})")
    print(f"  Сумма:     {order_data['price_rub']} ₽")
    print(f"  Папка:     {order_dir}")
    print(f"  Git:       Инициализирован репозиторий с initial commit")
    print("=" * 65)
    print(f"\nКоманда для старта в VS Code:")
    print(f'  code "{order_dir}"\n')
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    orders_dir = get_orders_dir(args.orders_dir)
    order_dir = orders_dir / args.slug
    data = load_order_json(order_dir)
    if not data:
        print(f"[!] Ошибка: заказ '{args.slug}' не найден в {orders_dir}.")
        return 1

    changed = []
    if args.status:
        if args.status not in VALID_STATUSES:
            print(f"[!] Ошибка: недопустимый статус '{args.status}'. Допустимые: {sorted(VALID_STATUSES)}")
            return 1
        data["status"] = args.status
        changed.append(f"status -> {args.status}")

    if args.price is not None:
        data["price_rub"] = float(args.price)
        changed.append(f"price_rub -> {args.price}")

    if args.hours is not None:
        data["hours_spent"] = round(data.get("hours_spent", 0.0) + float(args.hours), 2)
        changed.append(f"hours_spent -> {data['hours_spent']}")

    if args.deadline:
        data["deadline"] = args.deadline
        changed.append(f"deadline -> {args.deadline}")

    if not changed and not args.note:
        print("[!] Не указаны параметры для обновления (--status, --price, --hours, --deadline, --note).")
        return 1

    note = args.note or ", ".join(changed)
    data.setdefault("history", []).append({
        "ts": datetime.datetime.now().isoformat(),
        "event": "update",
        "note": note,
    })
    save_order_json(order_dir, data)

    git_cmd(order_dir, "add", "order.json")
    git_cmd(order_dir, "commit", "-m", f"chore(order): {note}")

    sync_dashboard(orders_dir)
    print(f"[✓] Заказ '{args.slug}' обновлен: {note}")
    return 0


def cmd_deliver(args: argparse.Namespace) -> int:
    orders_dir = get_orders_dir(args.orders_dir)
    order_dir = orders_dir / args.slug
    data = load_order_json(order_dir)
    if not data:
        print(f"[!] Ошибка: заказ '{args.slug}' не найден.")
        return 1

    today_str = datetime.date.today().isoformat()
    data["status"] = "delivered"
    data["delivered_at"] = today_str
    data.setdefault("history", []).append({
        "ts": datetime.datetime.now().isoformat(),
        "event": "delivered",
        "note": "Сдача заказа клиенту",
    })
    save_order_json(order_dir, data)

    tag_name = f"delivered-{today_str}"
    git_cmd(order_dir, "config", "user.name", "kwork-guard")
    git_cmd(order_dir, "config", "user.email", "guard@kwork.local")
    git_cmd(order_dir, "add", ".")
    git_cmd(order_dir, "commit", "-m", f"feat(release): сдача заказа ({tag_name})")
    git_cmd(order_dir, "tag", "-f", tag_name)

    sync_dashboard(orders_dir)
    print(f"[✓] Заказ '{args.slug}' успешно переведён в статус 'delivered'!")
    print(f"    Установлен Git-тег: {tag_name}")
    return 0


def cmd_warranty(args: argparse.Namespace) -> int:
    orders_dir = get_orders_dir(args.orders_dir)
    order_dir = orders_dir / args.slug
    data = load_order_json(order_dir)
    if not data:
        print(f"[!] Ошибка: заказ '{args.slug}' не найден.")
        return 1

    issue_text = args.issue or "Обращение клиента по гарантии / доработка"
    today_str = datetime.date.today().isoformat()
    branch_name = f"warranty/{today_str}"

    data["status"] = "warranty"
    data.setdefault("history", []).append({
        "ts": datetime.datetime.now().isoformat(),
        "event": "warranty",
        "note": issue_text,
    })
    save_order_json(order_dir, data)

    git_cmd(order_dir, "checkout", "-b", branch_name)

    dec_file = order_dir / "DECISIONS.md"
    if dec_file.is_file():
        content = dec_file.read_text(encoding="utf-8")
        row = f"| {today_str} | {issue_text} | Требует диагностики | Ветка {branch_name} | `{branch_name}` |\n"
        if "| Дата | Инцидент" in content:
            parts = content.split("| Дата | Инцидент")
            header_end = parts[1].find("\n\n")
            if header_end != -1:
                content = parts[0] + "| Дата | Инцидент" + parts[1][:header_end] + "\n" + row + parts[1][header_end:]
            else:
                content += "\n" + row
            dec_file.write_text(content, encoding="utf-8")

    git_cmd(order_dir, "add", ".")
    git_cmd(order_dir, "commit", "-m", f"fix(warranty): старт гарантийной ветки {branch_name}: {issue_text}")

    sync_dashboard(orders_dir)

    print("\n" + "=" * 65)
    print(f"🔧 Активирован гарантийный контур для заказа: {args.slug}")
    print(f"   Клиент:       {data.get('client_name')}")
    print(f"   Конфигурация: {data.get('config_info', {}).get('name')}")
    print(f"   Git-ветка:    {branch_name}")
    print(f"   Инцидент:     {issue_text}")
    print(f"   Журнал ADR:   {dec_file}")
    print("=" * 65)
    print(f"\nОткройте заказ в VS Code для исправления:")
    print(f'  code "{order_dir}"\n')
    return 0


def collect_all_orders(orders_dir: Path) -> list[dict[str, Any]]:
    orders = []
    if not orders_dir.is_dir():
        return orders

    for item in sorted(orders_dir.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            data = load_order_json(item)
            if data:
                data["_dir"] = item
                data["_stats"] = get_token_stats(data["order_id"])
                orders.append(data)
    return orders


def sync_dashboard(orders_dir: Path) -> str:
    orders = collect_all_orders(orders_dir)
    today = datetime.date.today()

    total_price = sum(o.get("price_rub", 0.0) for o in orders)
    leads = [o for o in orders if o.get("status") == "lead"]
    in_dev = [o for o in orders if o.get("status") in ("in_progress", "warranty")]
    review = [o for o in orders if o.get("status") == "review"]
    delivered = [o for o in orders if o.get("status") == "delivered"]

    leads_price = sum(o.get("price_rub", 0.0) for o in leads)
    in_dev_price = sum(o.get("price_rub", 0.0) for o in in_dev)
    delivered_price = sum(o.get("price_rub", 0.0) for o in delivered)

    total_token_cost_rub = sum(o["_stats"]["cost_rub"] for o in orders)
    total_hours = sum(o.get("hours_spent", 0.0) for o in orders)

    alerts = []
    for o in orders:
        oid = o["order_id"]
        status = o.get("status", "")
        dl_str = o.get("deadline", "")
        if dl_str and status in ("in_progress", "warranty"):
            try:
                dl_date = datetime.date.fromisoformat(dl_str)
                delta = (dl_date - today).days
                if delta < 0:
                    alerts.append(f"⏰ **ПРОСРОЧЕН ДЕДЛАЙН ({delta} дн):** `{oid}` ({o.get('client_name')}) — дедлайн был {dl_str}")
                elif delta <= 2:
                    alerts.append(f"⏰ **СКОРО ДЕДЛАЙН ({delta} дн):** `{oid}` ({o.get('client_name')}) — дедлайн {dl_str}")
            except Exception as exc:
                logger.warning("Deadline parse error for %s: %s", oid, exc)

        if o["_stats"]["calls"] >= 20:
            alerts.append(f"⚠️ **ВЫСОКИЙ РАСХОД ТОКЕНОВ:** `{oid}` — {o['_stats']['calls']}/25 вызовов (${o['_stats']['cost_usd']})")

    lines = [
        "# Реестр заказов и клиентов 1С (Kwork Workspace)",
        "",
        f"*Автоматически сформировано:* {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 📊 Сводка эффективности",
        "",
        f"- **Всего заказов в пайплайне:** {len(orders)}",
        f"- **🟡 На этапе согласования (лиды):** {len(leads)} (потенциал **{leads_price:,.0f} ₽**)",
        f"- **🟢 В активной разработке:** {len(in_dev)} (на сумму **{in_dev_price:,.0f} ₽**)",
    ]
    if review:
        lines.append(f"- **🔵 На проверке у клиента:** {len(review)}")
    lines.extend([
        f"- **✅ Завершено и сдано:** {len(delivered)} (выручка **{delivered_price:,.0f} ₽**)",
        f"- **Затраты на токены AI:** {total_token_cost_rub:,.2f} ₽",
        f"- **Чистая маржинальность:** {((total_price - total_token_cost_rub) / total_price * 100) if total_price > 0 else 100:.1f}%",
        f"- **Затрачено часов разработчика:** {total_hours:.1f} ч",
        "",
    ])

    if alerts:
        lines.append("## 🚨 ЧТО ТРЕБУЕТ ВНИМАНИЯ (АЛЕРТЫ)")
        lines.append("")
        for a in alerts:
            lines.append(f"- {a}")
        lines.append("")

    lines.append("## 📁 Список заказов")
    lines.append("")
    lines.append("| Заказ (ID) | Клиент | Тип | Статус | Сумма | Токены | Часы | Дедлайн | Обновлен |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    for o in orders:
        oid = o["order_id"]
        client = o.get("client_name", "—")
        kind = o.get("kind", "—")
        status_disp = STATUS_EMOJI.get(o.get("status", ""), o.get("status", ""))
        price = f"{o.get('price_rub', 0):,.0f} ₽"
        cost = f"{o['_stats']['cost_rub']:.1f} ₽ ({o['_stats']['calls']} поз)"
        hours = f"{o.get('hours_spent', 0):.1f} ч"
        dl = o.get("deadline") or "—"
        last_event_ts = o.get("history", [{}])[-1].get("ts", "")[:10] if o.get("history") else "—"

        lines.append(f"| [`{oid}`]({oid}/order.json) | **{client}** | `{kind}` | {status_disp} | {price} | {cost} | {hours} | {dl} | {last_event_ts} |")

    lines.append("")
    content = "\n".join(lines)
    dashboard_path = orders_dir / "CLIENTS.md"
    dashboard_path.write_text(content, encoding="utf-8")
    return content


def cmd_list(args: argparse.Namespace) -> int:
    orders_dir = get_orders_dir(args.orders_dir)
    orders = collect_all_orders(orders_dir)
    sync_dashboard(orders_dir)

    if args.status:
        orders = [o for o in orders if o.get("status") == args.status]

    if not orders:
        print(f"[*] В каталоге {orders_dir} заказов не найдено.")
        return 0

    print("\n" + "=" * 90)
    print(f"РЕЕСТР ЗАКАЗОВ KWORK ({orders_dir})")
    print("=" * 90)
    print(f"{'Заказ (Slug)':<24} {'Клиент':<16} {'Тип':<8} {'Статус':<24} {'Сумма':<10} {'Токены':<8}")
    print("-" * 90)

    for o in orders:
        oid = o["order_id"][:23]
        client = (o.get("client_name") or "—")[:15]
        kind = o.get("kind", "—")[:7]
        status = STATUS_EMOJI.get(o.get("status", ""), o.get("status", ""))[:23]
        price = f"{o.get('price_rub', 0):,.0f} ₽"
        cost = f"{o['_stats']['cost_rub']:.1f} ₽"
        print(f"{oid:<24} {client:<16} {kind:<8} {status:<24} {price:<10} {cost:<8}")

    print("=" * 90)
    print(f"Подробный дашборд обновлен: {orders_dir / 'CLIENTS.md'}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Менеджер заказов kwork-guard")
    parser.add_argument("--orders-dir", help="Каталог заказов (по умолчанию C:\\Orders)")

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    p_new = subparsers.add_parser("new", help="Создать новый заказ")
    p_new.add_argument("kind", help="Тип: vpf | report | processor | ext | intake | project")
    p_new.add_argument("slug", help="Идентификатор папки заказа (например komplekto_vpf)")
    p_new.add_argument("--client", help="Имя заказчика на Kwork")
    p_new.add_argument("--title", help="Краткое описание задачи")
    p_new.add_argument("--status", choices=list(VALID_STATUSES), help="Начальный статус (lead | in_progress и др.)")
    p_new.add_argument("--price", type=float, default=0.0, help="Сумма заказа в рублях")
    p_new.add_argument("--prepaid", type=float, default=0.0, help="Предоплата")
    p_new.add_argument("--deadline", help="Дедлайн: количество дней (например 3) или дата YYYY-MM-DD")
    p_new.add_argument("--config", help="Конфигурация 1С (например 1С:УТ 11)")
    p_new.add_argument("--platform", help="Версия платформы 1С (например 8.3.24)")
    p_new.add_argument("--url", help="Ссылка на трек Kwork")

    p_list = subparsers.add_parser("list", help="Список заказов и дашборд")
    p_list.add_argument("--status", choices=list(VALID_STATUSES), help="Фильтр по статусу")

    p_upd = subparsers.add_parser("update", help="Обновить статус, цену или часы заказа")
    p_upd.add_argument("slug", help="Идентификатор заказа")
    p_upd.add_argument("--status", choices=list(VALID_STATUSES), help="Новый статус")
    p_upd.add_argument("--price", type=float, help="Новая сумма в рублях")
    p_upd.add_argument("--hours", type=float, help="Добавить затраченные часы")
    p_upd.add_argument("--deadline", help="Новый дедлайн (YYYY-MM-DD)")
    p_upd.add_argument("--note", help="Текст заметки в историю")

    p_del = subparsers.add_parser("deliver", help="Сдать заказ (статус delivered + Git-тег)")
    p_del.add_argument("slug", help="Идентификатор заказа")

    p_war = subparsers.add_parser("warranty", help="Открыть гарантийную ветку и зафиксировать инцидент")
    p_war.add_argument("slug", help="Идентификатор заказа")
    p_war.add_argument("--issue", help="Описание проблемы или запроса клиента")

    subparsers.add_parser("sync", help="Принудительно обновить CLIENTS.md")

    args = parser.parse_args()

    commands = {
        "new": cmd_new,
        "list": cmd_list,
        "update": cmd_update,
        "deliver": cmd_deliver,
        "warranty": cmd_warranty,
        "sync": lambda a: (sync_dashboard(get_orders_dir(a.orders_dir)), print("[✓] CLIENTS.md обновлен."))[1] or 0,
    }

    return commands[args.subcommand](args)


if __name__ == "__main__":
    sys.exit(main())
