from __future__ import annotations

from dataclasses import dataclass, field
import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import shutil
from typing import Any, Optional

from cockpit.services.intake_parser import (
    IntakeParserService,
    format_clarifying_questions_text,
    generate_clarifying_questions,
)
from cockpit.services.prompt_builder import PromptBuilderService
from guard.config import load_config, sync_antigravity_mcp_config
from guard.fs_utils import atomic_write_json, atomic_write_text, write_bsl_with_bom
from guard.state import OrderManager
from tools.order import (
    VALID_STATUSES,
    build_mcp_config,
    get_orders_dir,
    get_token_stats,
    git_cmd,
    load_order_json,
    save_order_json,
    sync_dashboard,
)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("cockpit.orders_facade")
if not logger.handlers:
    log_handler = RotatingFileHandler(
        LOGS_DIR / "cockpit.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    logger.addHandler(log_handler)
    logger.setLevel(logging.INFO)


@dataclass
class OrderSummary:
    order_id: str
    client_name: str
    kind: str
    status: str
    price_rub: float
    hours_spent: float
    deadline: str
    calls_count: int
    cost_usd: float
    cost_rub: float
    title: str = ""
    configuration: str = ""
    order_dir: Optional[Path] = None
    virtual_cost_rub: float = 0.0
    kwork_fee_rub: float = 0.0
    net_margin_rub: float = 0.0
    is_api_over_threshold: bool = False
    economy_mode: bool = False

    @property
    def client(self) -> str:
        return self.client_name

    @property
    def stage(self) -> str:
        return self.status

    @property
    def price(self) -> float:
        return self.price_rub

    @property
    def mcp_calls(self) -> int:
        return self.calls_count


@dataclass
class OrderDetail:
    order_id: str
    client_name: str
    title: str
    kind: str
    status: str
    price_rub: float
    prepaid_rub: float
    hours_spent: float
    created_at: str
    deadline: str
    delivered_at: Optional[str]
    config_name: str
    platform_ver: str
    kwork_url: str
    history: list[dict[str, Any]]
    path: Path
    checklist: dict[str, bool] = field(default_factory=dict)
    notes: str = ""

    @property
    def client(self) -> str:
        return self.client_name

    @property
    def stage(self) -> str:
        return self.status

    @property
    def price(self) -> float:
        return self.price_rub

    @property
    def configuration(self) -> str:
        return self.config_name

    @property
    def order_dir(self) -> Path:
        return self.path


@dataclass
class DashboardMetrics:
    total_count: int
    leads_count: int
    leads_price: float
    in_progress_count: int
    in_progress_price: float
    review_count: int
    delivered_count: int
    delivered_price: float
    total_cost_rub: float
    total_hours: float
    margin_pct: float
    total_kwork_fee_rub: float = 0.0
    total_net_margin_rub: float = 0.0
    total_virtual_cost_rub: float = 0.0

    @property
    def delivered_value(self) -> float:
        return self.delivered_price

    @property
    def in_progress_value(self) -> float:
        return self.in_progress_price

    @property
    def margin_percent(self) -> float:
        return self.margin_pct

    @property
    def total_real_ai_cost_rub(self) -> float:
        return self.total_cost_rub

    @property
    def total_hours_spent(self) -> float:
        return self.total_hours

    @property
    def hourly_rate(self) -> float:
        if self.total_hours > 0:
            return self.delivered_price / self.total_hours
        return 0.0


@dataclass
class BudgetStatus:
    order_id: str
    calls_count: int
    max_calls: int
    extra_allowed: int
    is_blocked: bool
    chars_count: int
    cost_usd: float
    cost_rub: float
    virtual_cost_usd: float = 0.0
    virtual_cost_rub: float = 0.0
    price_rub: float = 0.0
    kwork_fee_rub: float = 0.0
    net_margin_rub: float = 0.0
    is_api_over_threshold: bool = False
    economy_mode: bool = False

    @property
    def call_count(self) -> int:
        return self.calls_count

    @property
    def is_warning(self) -> bool:
        return self.calls_count >= max(1, self.max_calls - 5)

    @property
    def is_exceeded(self) -> bool:
        return self.is_blocked

    @property
    def real_cost_rub(self) -> float:
        return self.cost_rub

    @property
    def real_cost_usd(self) -> float:
        return self.cost_usd

    @property
    def total_tokens(self) -> int:
        return self.chars_count // 4


class OrdersFacade:
    def __init__(
        self,
        orders_dir: Optional[Path] = None,
        root_dir: Optional[Path] = None,
        base_dir: Optional[Path] = None,
    ) -> None:
        if base_dir and not root_dir:
            root_dir = base_dir
        if base_dir and not orders_dir:
            orders_dir = base_dir / "orders"
        self.root_dir = root_dir if root_dir else ROOT_DIR
        self.orders_dir = orders_dir if orders_dir else get_orders_dir()
        self.state_dir = self.root_dir / "state"
        self.templates_dir = self.root_dir / "templates"
        self.gold_templates_dir = self.root_dir / "gold_templates"
        self.mcp_configs_dir = self.root_dir / "mcp_configs"
        self.guard_log = self.root_dir / "guard_log.jsonl"
        self.order_mgr = OrderManager(self.state_dir)

    def _map_mcp_profile(self, kind: str) -> str:
        low = kind.strip().lower()
        if low in ("vpf", "report", "ext", "intake"):
            return low
        if low in ("extension",):
            return "ext"
        return "ext"

    def _write_claude_md(self, order_dir: Path, profile: str, order_data: dict[str, Any]) -> None:
        parts = [
            f"# Заказ 1С: {order_data.get('title', order_data.get('order_id', ''))}",
            f"- Заказчик: {order_data.get('client_name', 'Заказчик')}",
            f"- Профиль MCP: `{profile}` (`http://127.0.0.1:8766/mcp/{profile}/{order_data.get('order_id', '')}`)",
            "",
        ]
        rules_dir = self.root_dir / ".roo" / f"rules-kwork-{profile}"
        if rules_dir.is_dir():
            for md_file in sorted(rules_dir.glob("*.md")):
                try:
                    parts.append(md_file.read_text(encoding="utf-8"))
                    parts.append("")
                except Exception as exc:
                    logger.warning("Read rule file error %s: %s", md_file, exc)

        ai_rules = self.templates_dir / "bsl_ai_rules.md"
        if ai_rules.is_file():
            try:
                parts.append(ai_rules.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Read bsl_ai_rules.md error: %s", exc)

        rules_text = "\n".join(parts).strip() + "\n"
        atomic_write_text(order_dir / "CLAUDE.md", rules_text, encoding="utf-8")
        atomic_write_text(order_dir / "AGENTS.md", rules_text, encoding="utf-8")

    def _resolve_antigravity_mcp_target(self) -> Optional[Path]:
        if self.root_dir.resolve() == ROOT_DIR.resolve():
            return None
        return self.root_dir / "state" / "antigravity_mcp_config.json"

    def _write_mcp_configs(self, order_dir: Path, mcp_profile: str, order_id: str) -> None:
        cfg_file = self.mcp_configs_dir / f"mcp.{mcp_profile}.json"
        if cfg_file.is_file():
            try:
                mcp_cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
                servers = mcp_cfg.get("mcpServers", {})
                if "edt" in servers and isinstance(servers["edt"], dict):
                    servers["edt"]["url"] = f"http://127.0.0.1:8766/mcp/{mcp_profile}/{order_id}"
                    servers["edt"]["headers"] = {"X-Order-Id": order_id}
            except Exception as exc:
                logger.warning("Failed to adapt mcp config template %s: %s", cfg_file, exc)
                mcp_cfg = build_mcp_config(mcp_profile, order_id, "127.0.0.1", 8766)
        else:
            mcp_cfg = build_mcp_config(mcp_profile, order_id, "127.0.0.1", 8766)

        roo_dir = order_dir / ".roo"
        roo_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(roo_dir / "mcp.json", mcp_cfg)
        atomic_write_json(order_dir / ".mcp.json", mcp_cfg)
        sync_antigravity_mcp_config(
            profile=mcp_profile,
            order_id=order_id,
            target_path=self._resolve_antigravity_mcp_target(),
            overwrite_existing_url=True,
        )

    def list_orders(
        self,
        status_filter: Optional[str] = None,
        kind_filter: Optional[str] = None,
        search_query: Optional[str] = None,
        stage_filter: Optional[str] = None,
    ) -> list[OrderSummary]:
        results: list[OrderSummary] = []
        if not self.orders_dir.is_dir():
            return results

        cfg = load_config()
        fin_cfg = cfg.get("finance", {})
        commission_ratio = float(fin_cfg.get("kwork_commission_ratio", 0.2))
        api_warn_ratio = float(fin_cfg.get("api_budget_warn_ratio", 0.05))

        effective_status = status_filter or stage_filter
        normalized_query = search_query.strip().lower() if search_query else None

        for item in sorted(self.orders_dir.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                data = load_order_json(item)
                if not data:
                    continue

                order_id = data.get("order_id", item.name)
                client_name = data.get("client_name", "Заказчик")
                kind = data.get("kind", "ext")
                status = data.get("status", "lead")
                price = float(data.get("price_rub", 0.0))
                hours = float(data.get("hours_spent", 0.0))
                deadline = data.get("deadline", "—")
                title = data.get("title", order_id)
                config_name = data.get("config_info", {}).get("name", "")

                if effective_status:
                    if effective_status == "in_progress" and status not in ("lead", "in_progress", "review", "development", "testing"):
                        continue
                    elif effective_status != "in_progress" and status != effective_status:
                        continue
                if kind_filter and kind != kind_filter:
                    continue
                if normalized_query:
                    searchable = f"{order_id} {client_name} {title} {config_name}".lower()
                    if normalized_query not in searchable:
                        continue

                stats = get_token_stats(order_id, state_dir=self.state_dir)
                real_rub = float(stats.get("cost_rub", 0.0))
                virt_rub = float(stats.get("virtual_cost_rub", 0.0))
                kwork_fee = round(price * commission_ratio, 2)
                net_margin = round(price - kwork_fee - real_rub, 2)
                over_thresh = price > 0 and (real_rub / price) > api_warn_ratio

                results.append(
                    OrderSummary(
                        order_id=order_id,
                        client_name=client_name,
                        kind=kind,
                        status=status,
                        price_rub=price,
                        hours_spent=hours,
                        deadline=deadline,
                        calls_count=stats.get("calls", 0),
                        cost_usd=stats.get("cost_usd", 0.0),
                        cost_rub=real_rub,
                        title=title,
                        configuration=config_name,
                        order_dir=item,
                        virtual_cost_rub=virt_rub,
                        kwork_fee_rub=kwork_fee,
                        net_margin_rub=net_margin,
                        is_api_over_threshold=over_thresh,
                        economy_mode=bool(stats.get("economy_mode", False) or over_thresh),
                    )
                )

        return results

    def get_order(self, order_id: str) -> Optional[OrderDetail]:
        target_dir = self.orders_dir / order_id
        if not target_dir.is_dir():
            return None
        data = load_order_json(target_dir)
        if not data:
            return None

        chk_file = target_dir / "checklist.json"
        checklist: dict[str, bool] = {}
        if chk_file.is_file():
            try:
                checklist = json.loads(chk_file.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Checklist read error for %s: %s", order_id, exc)

        return OrderDetail(
            order_id=data.get("order_id", order_id),
            client_name=data.get("client_name", "Заказчик"),
            title=data.get("title", order_id),
            kind=data.get("kind", "ext"),
            status=data.get("status", "lead"),
            price_rub=float(data.get("price_rub", 0.0)),
            prepaid_rub=float(data.get("prepaid_rub", 0.0)),
            hours_spent=float(data.get("hours_spent", 0.0)),
            created_at=data.get("created_at", ""),
            deadline=data.get("deadline", ""),
            delivered_at=data.get("delivered_at"),
            config_name=data.get("config_info", {}).get("name", "1С:Предприятие"),
            platform_ver=data.get("config_info", {}).get("platform", "8.3"),
            kwork_url=data.get("kwork_url", ""),
            history=data.get("history", []),
            path=target_dir,
            checklist=checklist,
            notes=data.get("notes", ""),
        )

    def create_order(
        self,
        payload: Optional[dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Any:
        is_dict_call = isinstance(payload, dict)
        if not is_dict_call:
            payload = {
                "order_id": kwargs.get("order_slug", kwargs.get("order_id", "")),
                "client_name": kwargs.get("client", kwargs.get("client_name", "Заказчик")),
                "title": kwargs.get("title", ""),
                "kind": kwargs.get("kind", "ext"),
                "price_rub": kwargs.get("price", kwargs.get("price_rub", 0.0)),
                "deadline": kwargs.get("deadline", ""),
                "config_name": kwargs.get("configuration", kwargs.get("config_name", "1С:Предприятие")),
                "object_name": kwargs.get("object_name", ""),
                "notes": kwargs.get("notes", ""),
                "clarifying_questions_text": kwargs.get("clarifying_questions_text", ""),
            }

        slug = str(payload.get("order_id") or "").strip()
        if not slug:
            if is_dict_call:
                return False, "Идентификатор заказа обязателен"
            raise ValueError("Идентификатор заказа обязателен")

        order_dir = self.orders_dir / slug
        if order_dir.exists() and any(order_dir.iterdir()):
            if is_dict_call:
                return False, f"Каталог {order_dir} уже существует и не пуст"
            raise FileExistsError(f"Каталог {order_dir} уже существует и не пуст")

        order_dir.mkdir(parents=True, exist_ok=True)
        (order_dir / "src").mkdir(exist_ok=True)
        (order_dir / "delivery").mkdir(exist_ok=True)

        kind = str(payload.get("kind", "ext")).lower()
        mcp_profile = self._map_mcp_profile(kind)
        self._write_mcp_configs(order_dir, mcp_profile, slug)

        roomodes_src = self.root_dir / ".roomodes"
        if roomodes_src.is_file():
            shutil.copy2(roomodes_src, order_dir / ".roomodes")

        rules_src_dir = self.root_dir / ".roo" / f"rules-kwork-{mcp_profile}"
        if rules_src_dir.is_dir():
            dst_rules = order_dir / ".roo" / f"rules-kwork-{mcp_profile}"
            if dst_rules.exists():
                shutil.rmtree(dst_rules)
            shutil.copytree(rules_src_dir, dst_rules)

        now_str = datetime.date.today().isoformat()
        now_iso = datetime.datetime.now().isoformat()

        order_data = {
            "order_id": slug,
            "client_name": payload.get("client_name", "Заказчик"),
            "title": payload.get("title", slug),
            "kwork_url": payload.get("kwork_url", ""),
            "kind": kind,
            "status": payload.get("status", "lead"),
            "price_rub": float(payload.get("price_rub", 0.0)),
            "prepaid_rub": float(payload.get("prepaid_rub", 0.0)),
            "hours_spent": 0.0,
            "created_at": now_str,
            "deadline": str(payload.get("deadline", "")),
            "delivered_at": None,
            "config_info": {
                "name": payload.get("config_name", "1С:Предприятие"),
                "version": "",
                "platform": payload.get("platform_ver", "8.3.24"),
            },
            "tags": [kind],
            "history": [
                {"ts": now_iso, "event": "created", "note": "Инициализация через Kwork Cockpit"}
            ],
            "notes": payload.get("notes", ""),
        }
        save_order_json(order_dir, order_data)
        self._write_claude_md(order_dir, mcp_profile, order_data)

        brief_tmpl = self.templates_dir / "brief.md"
        questions_block = str(payload.get("clarifying_questions_text") or "").strip()
        if not questions_block and payload.get("notes"):
            q_list = generate_clarifying_questions(
                {"config_name": order_data["config_info"]["name"]},
                str(payload.get("notes")),
            )
            questions_block = format_clarifying_questions_text(q_list)

        if brief_tmpl.is_file():
            brief_text = brief_tmpl.read_text(encoding="utf-8")
            brief_text = brief_text.replace("{{order_id}}", slug)
            brief_text = brief_text.replace("{{client_name}}", order_data["client_name"])
            brief_text = brief_text.replace("{{kind}}", kind)
            brief_text = brief_text.replace("{{object_name}}", str(payload.get("object_name") or slug))
            brief_text = brief_text.replace("{{configuration}}", order_data["config_info"]["name"])
            brief_text = brief_text.replace("{{target_version}}", order_data["config_info"]["platform"])
            brief_text = brief_text.replace("{{kwork_url}}", order_data["kwork_url"])
            if questions_block:
                brief_text += f"\n\n## Уточняющие вопросы клиенту\n\n{questions_block}\n"
            atomic_write_text(order_dir / "brief.md", brief_text, encoding="utf-8")
        elif questions_block:
            atomic_write_text(
                order_dir / "brief.md",
                f"# {order_data['title']}\n\n## Уточняющие вопросы клиенту\n\n{questions_block}\n",
                encoding="utf-8",
            )

        dec_tmpl = self.templates_dir / "DECISIONS.md"
        if dec_tmpl.is_file():
            dec_text = dec_tmpl.read_text(encoding="utf-8")
            dec_text = dec_text.replace("{{order_id}}", slug)
            dec_text = dec_text.replace("{{client_name}}", order_data["client_name"])
            dec_text = dec_text.replace("{{config_name}}", order_data["config_info"]["name"])
            dec_text = dec_text.replace("{{platform_ver}}", order_data["config_info"]["platform"])
            dec_text = dec_text.replace("{{created_at}}", now_str)
            dec_text = dec_text.replace("{{objective}}", order_data["title"])
            atomic_write_text(order_dir / "DECISIONS.md", dec_text, encoding="utf-8")

        git_cmd(order_dir, "init")
        git_cmd(order_dir, "config", "user.name", "kwork-guard")
        git_cmd(order_dir, "config", "user.email", "guard@kwork.local")
        git_cmd(order_dir, "config", "core.quotepath", "false")
        git_cmd(order_dir, "add", ".")
        git_cmd(order_dir, "commit", "-m", f"chore: initial commit for order {slug}")

        sync_dashboard(self.orders_dir)
        if not is_dict_call:
            return self.get_order(slug)
        return True, slug

    def start_order_work(self, order_id: str) -> dict[str, Any]:
        order_dir = self.orders_dir / order_id
        data = load_order_json(order_dir)
        if not data:
            return {"ok": False, "error": f"Заказ {order_id} не найден"}

        kind = str(data.get("kind", "ext")).lower()
        mcp_profile = self._map_mcp_profile(kind)

        if kind == "report":
            tpl_name = "report_standard"
            old_name = "ШаблонОтчета"
        elif kind in ("vpf", "processor"):
            tpl_name = "vpf_standard"
            old_name = "ШаблонВПФ"
        else:
            tpl_name = "ext_standard"
            old_name = "ШаблонРасширения"

        tpl_dir = self.gold_templates_dir / tpl_name
        src_target = order_dir / "src"
        src_target.mkdir(parents=True, exist_ok=True)
        safe_obj_name = re.sub(r"[^A-Za-zА-Яа-я0-9_]", "_", order_id).strip("_")
        if not safe_obj_name or safe_obj_name[0].isdigit():
            safe_obj_name = f"Объект_{safe_obj_name}"

        copied_files = 0
        if tpl_dir.is_dir() and not any(src_target.iterdir()):
            for item in tpl_dir.rglob("*"):
                rel = item.relative_to(tpl_dir)
                parts = [part.replace(old_name, safe_obj_name) for part in rel.parts]
                dst_path = src_target.joinpath(*parts)
                if item.is_dir():
                    dst_path.mkdir(parents=True, exist_ok=True)
                else:
                    dst_path.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        txt = item.read_text(encoding="utf-8-sig").replace(old_name, safe_obj_name)
                        if dst_path.suffix.lower() == ".bsl":
                            write_bsl_with_bom(dst_path, txt)
                        else:
                            atomic_write_text(dst_path, txt, encoding="utf-8")
                    except UnicodeDecodeError:
                        shutil.copy2(item, dst_path)
                    copied_files += 1

        self._write_mcp_configs(order_dir, mcp_profile, order_id)
        self._write_claude_md(order_dir, mcp_profile, data)

        if data.get("status") in ("lead", "intake"):
            self.advance_stage(order_id, "in_progress")

        prompt = PromptBuilderService(self.root_dir).build_roo_starter_prompt(order_dir)
        return {
            "ok": True,
            "order_id": order_id,
            "order_dir": str(order_dir),
            "template": tpl_name,
            "copied_files": copied_files,
            "mcp_profile": mcp_profile,
            "prompt": prompt,
        }

    def update_order(self, order_id: str, **fields: Any) -> tuple[bool, str]:
        order_dir = self.orders_dir / order_id
        data = load_order_json(order_dir)
        if not data:
            return False, f"Заказ {order_id} не найден"

        changed: list[str] = []
        for key, value in fields.items():
            if key == "status" and value in VALID_STATUSES:
                data["status"] = value
                changed.append(f"status -> {value}")
            elif key in ("price_rub", "price") and value is not None:
                data["price_rub"] = float(value)
                changed.append(f"price -> {value}")
            elif key == "hours_spent" and value is not None:
                data["hours_spent"] = round(float(value), 2)
                changed.append(f"hours -> {value}")
            elif key == "deadline" and value is not None:
                data["deadline"] = str(value)
                changed.append(f"deadline -> {value}")
            elif key == "title" and value is not None:
                data["title"] = str(value)
                changed.append(f"title -> {value}")
            elif key == "notes" and value is not None:
                data["notes"] = str(value)
                changed.append("notes updated")
            elif key in ("configuration", "config_name") and value is not None:
                data.setdefault("config_info", {})["name"] = str(value)
                changed.append(f"configuration -> {value}")

        note = fields.get("note") or ", ".join(changed) or "Обновление параметров"
        data.setdefault("history", []).append({
            "ts": datetime.datetime.now().isoformat(),
            "event": "update",
            "note": note,
        })
        save_order_json(order_dir, data)

        git_cmd(order_dir, "add", "order.json")
        git_cmd(order_dir, "commit", "-m", f"chore(order): {note}")
        sync_dashboard(self.orders_dir)
        return True, "Обновлено успешно"

    def advance_stage(self, order_id: str, target_stage: Optional[str] = None) -> tuple[bool, str]:
        stage_flow = {
            "lead": "in_progress",
            "in_progress": "review",
            "review": "delivered",
        }
        order = self.get_order(order_id)
        if not order:
            return False, "Заказ не найден"
        if target_stage:
            next_stage = target_stage
        else:
            next_stage = stage_flow.get(order.status)
            if not next_stage:
                return False, f"Невозможно перевести из текущего статуса {order.status}"

        return self.update_order(order_id, status=next_stage, note=f"Переход на этап: {next_stage}")

    def add_hours(self, order_id: str, hours: float) -> tuple[bool, str]:
        order = self.get_order(order_id)
        if not order:
            return False, "Заказ не найден"
        new_total = round(order.hours_spent + hours, 2)
        return self.update_order(order_id, hours_spent=new_total, note=f"Добавлено часов: +{hours}")

    def save_checklist(self, order_id: str, checklist_dict: dict[str, bool]) -> bool:
        order_dir = self.orders_dir / order_id
        if not order_dir.is_dir():
            return False
        try:
            atomic_write_json(order_dir / "checklist.json", checklist_dict)
            return True
        except Exception as exc:
            logger.error("Checklist write failure: %s", exc)
            return False

    def get_budget(self, order_id: str) -> BudgetStatus:
        stats = get_token_stats(order_id, state_dir=self.state_dir)
        config = load_config()
        max_calls = config.get("limits", {}).get("max_calls_per_order", 25)
        fin_cfg = config.get("finance", {})
        commission_ratio = float(fin_cfg.get("kwork_commission_ratio", 0.2))
        api_warn_ratio = float(fin_cfg.get("api_budget_warn_ratio", 0.05))

        order_data = load_order_json(self.orders_dir / order_id) or {}
        price_rub = float(order_data.get("price_rub", 0.0))

        state = self.order_mgr.get_order_sync(order_id)
        extra = state.extra_calls_allowed
        effective_max = max_calls + extra
        calls = stats.get("calls", 0)
        blocked = calls >= effective_max

        real_rub = float(stats.get("cost_rub", 0.0))
        real_usd = float(stats.get("cost_usd", 0.0))
        virt_rub = float(stats.get("virtual_cost_rub", 0.0))
        virt_usd = float(stats.get("virtual_cost_usd", 0.0))

        over_thresh = price_rub > 0 and (real_rub / price_rub) > api_warn_ratio
        if over_thresh and not state.economy_mode:
            state.economy_mode = True
            state.save()

        kwork_fee = round(price_rub * commission_ratio, 2)
        net_margin = round(price_rub - kwork_fee - real_rub, 2)

        return BudgetStatus(
            order_id=order_id,
            calls_count=calls,
            max_calls=effective_max,
            extra_allowed=extra,
            is_blocked=blocked,
            chars_count=stats.get("chars", 0),
            cost_usd=real_usd,
            cost_rub=real_rub,
            virtual_cost_usd=virt_usd,
            virtual_cost_rub=virt_rub,
            price_rub=price_rub,
            kwork_fee_rub=kwork_fee,
            net_margin_rub=net_margin,
            is_api_over_threshold=over_thresh,
            economy_mode=bool(state.economy_mode),
        )

    def extend_budget(self, order_id: str, calls: int = 10) -> tuple[bool, str]:
        try:
            state = self.order_mgr.get_order_sync(order_id)
            state.extra_calls_allowed += max(1, calls)
            state.save()
            return True, f"Бюджет увеличен на +{calls} вызовов"
        except Exception as exc:
            logger.error("Extend budget write failure: %s", exc)
            return False, str(exc)

    def reset_budget(self, order_id: str) -> tuple[bool, str]:
        try:
            state = self.order_mgr.get_order_sync(order_id)
            state.calls_count = 0
            state.total_response_chars = 0
            state.errors_count = 0
            state.extra_calls_allowed = 0
            state.economy_mode = False
            state.blocked_calls.clear()
            state.save()
            return True, "Бюджет сброшен до 0"
        except Exception as exc:
            logger.error("Reset budget write failure: %s", exc)
            return False, str(exc)

    def get_order_mcp_events(self, order_id: str, limit: int = 50) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if not self.guard_log.is_file():
            return events

        try:
            with self.guard_log.open("r", encoding="utf-8") as file_handle:
                for line in file_handle:
                    line = line.strip()
                    if line:
                        try:
                            record = json.loads(line)
                            if record.get("order_id") == order_id:
                                events.append(record)
                        except Exception as parse_exc:
                            logger.debug("Guard log line parse error: %s", parse_exc)
        except Exception as exc:
            logger.warning("Guard log read error: %s", exc)

        events.reverse()
        return events[:limit]

    def get_financial_summary(self) -> DashboardMetrics:
        orders = self.list_orders()
        total_count = len(orders)

        leads = [o for o in orders if o.status == "lead"]
        in_prog = [o for o in orders if o.status in ("in_progress", "warranty", "development", "testing")]
        reviews = [o for o in orders if o.status == "review"]
        delivered = [o for o in orders if o.status == "delivered"]

        leads_price = sum(o.price_rub for o in leads)
        in_prog_price = sum(o.price_rub for o in in_prog)
        delivered_price = sum(o.price_rub for o in delivered)
        total_price = sum(o.price_rub for o in orders)

        total_cost_rub = sum(o.cost_rub for o in orders)
        total_virtual_rub = sum(o.virtual_cost_rub for o in orders)
        total_kwork_fee = sum(o.kwork_fee_rub for o in orders)
        total_net_margin = sum(o.net_margin_rub for o in orders)
        total_hours = sum(o.hours_spent for o in orders)

        margin_pct = ((total_net_margin / total_price) * 100) if total_price > 0 else 100.0

        return DashboardMetrics(
            total_count=total_count,
            leads_count=len(leads),
            leads_price=leads_price,
            in_progress_count=len(in_prog),
            in_progress_price=in_prog_price,
            review_count=len(reviews),
            delivered_count=len(delivered),
            delivered_price=delivered_price,
            total_cost_rub=round(total_cost_rub, 2),
            total_hours=round(total_hours, 2),
            margin_pct=round(margin_pct, 1),
            total_kwork_fee_rub=round(total_kwork_fee, 2),
            total_net_margin_rub=round(total_net_margin, 2),
            total_virtual_cost_rub=round(total_virtual_rub, 2),
        )
