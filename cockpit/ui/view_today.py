from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import customtkinter

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
        self.header_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(15, 10))
        self.header_frame.grid_columnconfigure(0, weight=1)
        self.header_frame.grid_columnconfigure(1, weight=0)

        self.title_label = customtkinter.CTkLabel(
            self.header_frame,
            text="📅 Фокус на сегодня",
            font=customtkinter.CTkFont(size=22, weight="bold"),
        )
        self.title_label.grid(row=0, column=0, sticky="w")

        self.refresh_btn = customtkinter.CTkButton(
            self.header_frame,
            text="🔄 Обновить",
            width=100,
            command=self.load_data,
        )
        self.refresh_btn.grid(row=0, column=1, sticky="e")

        self.metrics_frame = customtkinter.CTkFrame(self, fg_color="#1E293B", corner_radius=8)
        self.metrics_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=10)
        self.metrics_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.active_cnt_label = customtkinter.CTkLabel(
            self.metrics_frame,
            text="В работе\n0",
            font=customtkinter.CTkFont(size=14, weight="bold"),
        )
        self.active_cnt_label.grid(row=0, column=0, padx=15, pady=12)

        self.pipeline_val_label = customtkinter.CTkLabel(
            self.metrics_frame,
            text="В пайплайне\n0 ₽",
            font=customtkinter.CTkFont(size=14, weight="bold"),
            text_color="#38BDF8",
        )
        self.pipeline_val_label.grid(row=0, column=1, padx=15, pady=12)

        self.hours_val_label = customtkinter.CTkLabel(
            self.metrics_frame,
            text="Затрачено часов\n0.0 ч",
            font=customtkinter.CTkFont(size=14, weight="bold"),
        )
        self.hours_val_label.grid(row=0, column=2, padx=15, pady=12)

        self.margin_val_label = customtkinter.CTkLabel(
            self.metrics_frame,
            text="Маржинальность\n100%",
            font=customtkinter.CTkFont(size=14, weight="bold"),
            text_color="#10B981",
        )
        self.margin_val_label.grid(row=0, column=3, padx=15, pady=12)

        self.alerts_title = customtkinter.CTkLabel(
            self,
            text="⚠️ Внимание и алерты",
            font=customtkinter.CTkFont(size=16, weight="bold"),
        )
        self.alerts_title.grid(row=2, column=0, sticky="w", padx=20, pady=(15, 6))

        self.alerts_container = customtkinter.CTkFrame(self, fg_color="transparent")
        self.alerts_container.grid(row=3, column=0, sticky="ew", padx=20, pady=4)
        self.alerts_container.grid_columnconfigure(0, weight=1)

        self.active_title = customtkinter.CTkLabel(
            self,
            text="🔥 Активные заказы",
            font=customtkinter.CTkFont(size=16, weight="bold"),
        )
        self.active_title.grid(row=4, column=0, sticky="w", padx=20, pady=(20, 6))

        self.cards_container = customtkinter.CTkFrame(self, fg_color="transparent")
        self.cards_container.grid(row=5, column=0, sticky="ew", padx=20, pady=4)
        self.cards_container.grid_columnconfigure(0, weight=1)

    def load_data(self) -> None:
        try:
            summary = self.controller.orders_facade.get_financial_summary()
            self.active_cnt_label.configure(text=f"В работе\n{summary.in_progress_count}")
            self.pipeline_val_label.configure(text=f"В пайплайне\n{summary.in_progress_value:,.0f} ₽".replace(",", " "))
            self.hours_val_label.configure(text=f"Затрачено часов\n{summary.total_hours_spent:.1f} ч")
            self.margin_val_label.configure(text=f"Маржинальность\n{summary.margin_percent:.0f}%")

            active_orders = self.controller.orders_facade.list_orders(stage_filter="in_progress")
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
                alerts.append(f"Заказ {order.order_id}: исчерпан лимит вызовов ({order.mcp_calls}/25)")
            if order.deadline and "сегодня" in order.deadline.lower():
                alerts.append(f"Дедлайн сегодня у заказа {order.order_id} ({order.client})")

        if not alerts:
            label = customtkinter.CTkLabel(
                self.alerts_container,
                text="✅ Все активные заказы идут штатно, критических алертов нет",
                text_color="#10B981",
                font=customtkinter.CTkFont(size=13),
            )
            label.grid(row=0, column=0, sticky="w", pady=4)
            return

        for index, alert_text in enumerate(alerts):
            frame = customtkinter.CTkFrame(self.alerts_container, fg_color="#451A1A", corner_radius=6)
            frame.grid(row=index, column=0, sticky="ew", pady=3)
            alert_label = customtkinter.CTkLabel(
                frame,
                text=f"• {alert_text}",
                text_color="#FCA5A5",
                font=customtkinter.CTkFont(size=13),
            )
            alert_label.pack(side="left", padx=12, pady=6)

    def _render_cards(self, active_orders: list[Any]) -> None:
        for child in self.cards_container.winfo_children():
            child.destroy()

        if not active_orders:
            empty_label = customtkinter.CTkLabel(
                self.cards_container,
                text="Сейчас нет активных заказов в работе. Создайте новый заказ!",
                text_color="#94A3B8",
                font=customtkinter.CTkFont(size=14),
            )
            empty_label.grid(row=0, column=0, sticky="w", pady=10)
            return

        for index, order in enumerate(active_orders):
            card = customtkinter.CTkFrame(self.cards_container, fg_color="#1E293B", corner_radius=8)
            card.grid(row=index, column=0, sticky="ew", pady=6)
            card.grid_columnconfigure(0, weight=1)
            card.grid_columnconfigure(1, weight=0)

            info_frame = customtkinter.CTkFrame(card, fg_color="transparent")
            info_frame.grid(row=0, column=0, sticky="w", padx=14, pady=10)

            title_row = customtkinter.CTkLabel(
                info_frame,
                text=f"{order.order_id}  |  {order.client}  |  {order.title}",
                font=customtkinter.CTkFont(size=15, weight="bold"),
            )
            title_row.pack(anchor="w")

            meta_text = (
                f"Конфигурация: {order.configuration or 'Не указана'}  •  "
                f"Тип: {order.kind}  •  "
                f"Цена: {order.price:,.0f} ₽  •  "
                f"Дедлайн: {order.deadline or '—'}  •  "
                f"Этап: {order.stage}  •  "
                f"Вызовы: {order.mcp_calls}/25"
            ).replace(",", " ")
            meta_label = customtkinter.CTkLabel(
                info_frame,
                text=meta_text,
                font=customtkinter.CTkFont(size=12),
                text_color="#94A3B8",
            )
            meta_label.pack(anchor="w", pady=(3, 0))

            actions_frame = customtkinter.CTkFrame(card, fg_color="transparent")
            actions_frame.grid(row=0, column=1, sticky="e", padx=14, pady=10)

            vscode_btn = customtkinter.CTkButton(
                actions_frame,
                text="В VS Code",
                width=90,
                command=lambda o_dir=order.order_dir: self.on_open_vscode(o_dir),
            )
            vscode_btn.pack(side="left", padx=4)

            prompt_btn = customtkinter.CTkButton(
                actions_frame,
                text="Промпт в буфер",
                width=120,
                fg_color="#0D9488",
                hover_color="#0F766E",
                command=lambda ord_obj=order: self.on_copy_prompt(ord_obj),
            )
            prompt_btn.pack(side="left", padx=4)

            detail_btn = customtkinter.CTkButton(
                actions_frame,
                text="Карточка",
                width=80,
                fg_color="#334155",
                hover_color="#475569",
                command=lambda ord_id=order.order_id: self.controller.show_view("order_detail", order_id=ord_id),
            )
            detail_btn.pack(side="left", padx=4)

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
