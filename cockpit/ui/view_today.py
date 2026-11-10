from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import customtkinter

from cockpit.services.workspace_bridge import launch_antigravity_workspace
from cockpit.ui.theme import (
    ACCENT_INDIGO_BG,
    ACCENT_INDIGO_FG,
    BG_ELEVATED,
    BG_MAIN,
    BG_SURFACE,
    BORDER,
    DANGER_BG,
    DANGER_FG,
    SUCCESS_BG,
    SUCCESS_FG,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    create_badge,
    create_button,
    create_card,
    font_mono,
    font_ui,
    format_deadline_countdown,
    parse_deadline_timestamp,
)

LOGGER = logging.getLogger("cockpit.view_today")
if not LOGGER.handlers:
    _log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handler = RotatingFileHandler(_log_dir / "cockpit.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)


class ViewToday(customtkinter.CTkScrollableFrame):
    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.controller = controller

        self.grid_columnconfigure(0, weight=1)

        self.header_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.header_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 10))
        self.header_frame.grid_columnconfigure(0, weight=1)

        self.title_label = customtkinter.CTkLabel(
            self.header_frame,
            text="Фокус на сегодня",
            font=font_ui(20, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.title_label.grid(row=0, column=0, sticky="w")

        self.refresh_btn = create_button(
            self.header_frame,
            text="↻ Обновить",
            variant="secondary",
            height=30,
            width=100,
            command=self.load_data,
        )
        self.refresh_btn.grid(row=0, column=1, sticky="e")

        self.metrics_frame = create_card(self, fg_color=BG_SURFACE, corner_radius=8)
        self.metrics_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=8)
        self.metrics_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.active_cnt_label = customtkinter.CTkLabel(
            self.metrics_frame,
            text="В работе\n0",
            font=font_mono(14, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.active_cnt_label.grid(row=0, column=0, padx=14, pady=14)

        self.pipeline_val_label = customtkinter.CTkLabel(
            self.metrics_frame,
            text="В пайплайне\n0 ₽",
            font=font_mono(14, "bold"),
            text_color=ACCENT_INDIGO_FG,
        )
        self.pipeline_val_label.grid(row=0, column=1, padx=14, pady=14)

        self.hours_val_label = customtkinter.CTkLabel(
            self.metrics_frame,
            text="Затрачено часов\n0.0 ч",
            font=font_mono(14, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.hours_val_label.grid(row=0, column=2, padx=14, pady=14)

        self.margin_val_label = customtkinter.CTkLabel(
            self.metrics_frame,
            text="Маржинальность\n100%",
            font=font_mono(14, "bold"),
            text_color=SUCCESS_FG,
        )
        self.margin_val_label.grid(row=0, column=3, padx=14, pady=14)

        self.alerts_title = customtkinter.CTkLabel(
            self,
            text="Внимание и алерты",
            font=font_ui(15, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.alerts_title.grid(row=2, column=0, sticky="w", padx=20, pady=(14, 6))

        self.alerts_container = customtkinter.CTkFrame(self, fg_color="transparent")
        self.alerts_container.grid(row=3, column=0, sticky="ew", padx=20, pady=4)
        self.alerts_container.grid_columnconfigure(0, weight=1)

        self.active_title = customtkinter.CTkLabel(
            self,
            text="Активные заказы по срочности",
            font=font_ui(15, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.active_title.grid(row=4, column=0, sticky="w", padx=20, pady=(16, 6))

        self.cards_container = customtkinter.CTkFrame(self, fg_color="transparent")
        self.cards_container.grid(row=5, column=0, sticky="ew", padx=20, pady=(4, 16))
        self.cards_container.grid_columnconfigure(0, weight=1)

    def load_data(self) -> None:
        try:
            summary = self.controller.orders_facade.get_financial_summary()
            self.active_cnt_label.configure(text=f"В работе\n{summary.in_progress_count}")
            self.pipeline_val_label.configure(text=f"В пайплайне\n{summary.in_progress_value:,.0f} ₽".replace(",", " "))
            self.hours_val_label.configure(text=f"Затрачено часов\n{summary.total_hours_spent:.1f} ч")
            self.margin_val_label.configure(text=f"Маржинальность\n{summary.margin_percent:.0f}%")

            active_orders = self.controller.orders_facade.list_orders(stage_filter="in_progress")
            active_orders.sort(
                key=lambda o: format_deadline_countdown(
                    parse_deadline_timestamp(getattr(o, "deadline_timestamp", 0), o.deadline or ""),
                    o.deadline or "",
                )[3]
            )
            self._render_alerts(active_orders)
            self._render_cards(active_orders)
        except Exception as error:
            LOGGER.error("Failed to load today view data: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка загрузки", str(error), error=True)

    def _render_alerts(self, active_orders: list[Any]) -> None:
        for child in self.alerts_container.winfo_children():
            child.destroy()

        alerts: list[str] = []
        for order in active_orders:
            if order.mcp_calls >= 20:
                alerts.append(f"Заказ {order.order_id}: приближается лимит MCP ({order.mcp_calls}/25)")
            dl_txt, _, _, rem = format_deadline_countdown(
                getattr(order, "deadline_timestamp", 0),
                order.deadline or "",
            )
            if 0 < rem < 6 * 3600 or rem <= 0:
                alerts.append(f"Срочный дедлайн ({dl_txt}) — {order.order_id} (@{order.client})")
            elif order.deadline and "сегодня" in order.deadline.lower():
                alerts.append(f"Дедлайн сегодня у заказа {order.order_id} ({order.client})")

        if not alerts:
            card = create_card(self.alerts_container, fg_color=SUCCESS_BG, border_color="#14532D", corner_radius=6)
            card.grid(row=0, column=0, sticky="ew", pady=3)
            customtkinter.CTkLabel(
                card,
                text="✓ Все активные заказы в графике, критических алертов нет",
                text_color=SUCCESS_FG,
                font=font_ui(12),
            ).pack(side="left", padx=12, pady=8)
            return

        for index, alert_text in enumerate(alerts):
            card = create_card(self.alerts_container, fg_color=DANGER_BG, border_color="#7F1D1D", corner_radius=6)
            card.grid(row=index, column=0, sticky="ew", pady=3)
            customtkinter.CTkLabel(
                card,
                text=f"• {alert_text}",
                text_color=DANGER_FG,
                font=font_ui(12),
            ).pack(side="left", padx=12, pady=8)

    def _render_cards(self, active_orders: list[Any]) -> None:
        for child in self.cards_container.winfo_children():
            child.destroy()

        if not active_orders:
            empty_card = create_card(self.cards_container, fg_color=BG_SURFACE)
            empty_card.grid(row=0, column=0, sticky="ew", pady=6)
            customtkinter.CTkLabel(
                empty_card,
                text="Нет активных заказов. Создайте заказ или выполните синхронизацию Kwork.",
                text_color=TEXT_SECONDARY,
                font=font_ui(13),
            ).pack(pady=20)
            return

        for index, order in enumerate(active_orders):
            card = create_card(self.cards_container, fg_color=BG_SURFACE, corner_radius=8)
            card.grid(row=index, column=0, sticky="ew", pady=4)
            card.grid_columnconfigure(0, weight=1)
            card.grid_columnconfigure(1, weight=0)

            info_frame = customtkinter.CTkFrame(card, fg_color="transparent")
            info_frame.grid(row=0, column=0, sticky="w", padx=14, pady=10)

            top_row = customtkinter.CTkFrame(info_frame, fg_color="transparent")
            top_row.pack(anchor="w")
            customtkinter.CTkLabel(
                top_row,
                text=order.order_id,
                font=font_mono(11, "bold"),
                text_color=ACCENT_INDIGO_FG,
            ).pack(side="left", padx=(0, 8))
            raw_title = f"{order.title}  ·  @{order.client}"
            short_title = raw_title if len(raw_title) <= 54 else raw_title[:51].rstrip() + "..."
            customtkinter.CTkLabel(
                top_row,
                text=short_title,
                font=font_ui(14, "bold"),
                text_color=TEXT_PRIMARY,
            ).pack(side="left")

            meta_text = (
                f"{order.configuration or 'Общая 1С'}  ·  {order.kind.upper()}  ·  "
                f"{order.price:,.0f} ₽  ·  MCP {order.mcp_calls}/25"
            ).replace(",", " ")
            customtkinter.CTkLabel(
                info_frame,
                text=meta_text,
                font=font_mono(11),
                text_color=TEXT_SECONDARY,
            ).pack(anchor="w", pady=(4, 0))

            actions_frame = customtkinter.CTkFrame(card, fg_color="transparent")
            actions_frame.grid(row=0, column=1, sticky="e", padx=14, pady=10)

            dl_txt, dl_fg, dl_bg, _ = format_deadline_countdown(
                getattr(order, "deadline_timestamp", 0),
                order.deadline or "",
            )
            create_badge(actions_frame, dl_txt, fg_color=dl_bg, text_color=dl_fg, mono=True).pack(side="left", padx=4)

            create_button(
                actions_frame,
                text="Antigravity",
                variant="primary",
                height=30,
                width=92,
                command=lambda o_dir=order.order_dir: self.on_open_antigravity(o_dir),
            ).pack(side="left", padx=3)

            create_button(
                actions_frame,
                text="VS Code",
                variant="secondary",
                height=30,
                width=74,
                command=lambda o_dir=order.order_dir: self.on_open_vscode(o_dir),
            ).pack(side="left", padx=3)

            create_button(
                actions_frame,
                text="Промпт",
                variant="secondary",
                height=30,
                width=74,
                command=lambda ord_obj=order: self.on_copy_prompt(ord_obj),
            ).pack(side="left", padx=3)

            create_button(
                actions_frame,
                text="Открыть",
                variant="secondary",
                height=30,
                width=76,
                command=lambda ord_id=order.order_id: self.controller.show_view("order_detail", order_id=ord_id),
            ).pack(side="left", padx=(3, 0))

    def on_open_antigravity(self, order_dir: Path) -> None:
        self.controller.run_in_background(
            lambda: launch_antigravity_workspace(
                order_dir,
                base_dir=self.controller.base_dir,
                system_doctor=self.controller.system_doctor,
                onec_runner=self.controller.onec_runner,
            )
        )

    def on_open_vscode(self, order_dir: Path) -> None:
        try:
            ok, msg = self.controller.onec_runner.open_in_vscode(order_dir)
            if not ok:
                self.controller.show_notification("VS Code", msg, error=True)
        except Exception as error:
            LOGGER.error("Open in VS Code error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка", str(error), error=True)

    def on_copy_prompt(self, order: Any) -> None:
        try:
            order_path = getattr(order, "order_dir", None) or getattr(order, "path", None)
            if order_path and Path(order_path).is_dir():
                prompt = self.controller.prompt_builder.build_roo_starter_prompt(Path(order_path))
            else:
                prompt = f"Заказ {order.order_id} для {order.client}"
            self.clipboard_clear()
            self.clipboard_append(prompt)
            self.controller.show_notification("Промпт скопирован", f"Стартовый промпт для {order.order_id} помещен в буфер")
        except Exception as error:
            LOGGER.error("Copy prompt error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка", str(error), error=True)
