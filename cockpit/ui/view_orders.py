from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import customtkinter

LOGGER = logging.getLogger("cockpit.view_orders")
if not LOGGER.handlers:
    _log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handler = RotatingFileHandler(_log_dir / "cockpit.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)

STAGE_COLORS: dict[str, str] = {
    "intake": "#3B82F6",
    "development": "#EAB308",
    "testing": "#A855F7",
    "delivered": "#10B981",
    "warranty": "#06B6D4",
    "archive": "#64748B",
}


class ViewOrders(customtkinter.CTkFrame):
    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.controller = controller

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.top_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.top_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(15, 8))
        self.top_frame.grid_columnconfigure(0, weight=1)
        self.top_frame.grid_columnconfigure(1, weight=0)

        self.title_label = customtkinter.CTkLabel(
            self.top_frame,
            text="📋 Реестр заказов",
            font=customtkinter.CTkFont(size=22, weight="bold"),
        )
        self.title_label.grid(row=0, column=0, sticky="w")

        self.new_order_btn = customtkinter.CTkButton(
            self.top_frame,
            text="➕ Создать заказ",
            fg_color="#059669",
            hover_color="#047857",
            font=customtkinter.CTkFont(weight="bold"),
            command=lambda: self.controller.show_view("new_order"),
        )
        self.new_order_btn.grid(row=0, column=1, sticky="e")

        self.filter_frame = customtkinter.CTkFrame(self, fg_color="#1E293B", corner_radius=8)
        self.filter_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=8)
        self.filter_frame.grid_columnconfigure(0, weight=1)

        self.search_entry = customtkinter.CTkEntry(
            self.filter_frame,
            placeholder_text="Поиск по клиенту, номеру или названию... (Ctrl+K)",
            width=320,
        )
        self.search_entry.grid(row=0, column=0, padx=12, pady=10, sticky="ew")
        self.search_entry.bind("<KeyRelease>", lambda event: self.filter_orders())

        self.stage_filter = customtkinter.CTkOptionMenu(
            self.filter_frame,
            values=["Все статусы", "В работе (активные)", "Сдан", "Гарантия", "Архив"],
            command=lambda choice: self.filter_orders(),
        )
        self.stage_filter.grid(row=0, column=1, padx=6, pady=10)

        self.kind_filter = customtkinter.CTkOptionMenu(
            self.filter_frame,
            values=["Все типы", "ВПФ", "Отчет", "Обработка", "Расширение", "ТЗ"],
            command=lambda choice: self.filter_orders(),
        )
        self.kind_filter.grid(row=0, column=2, padx=6, pady=10)

        self.refresh_btn = customtkinter.CTkButton(
            self.filter_frame,
            text="🔄 Обновить",
            width=90,
            fg_color="#334155",
            hover_color="#475569",
            command=self.load_orders,
        )
        self.refresh_btn.grid(row=0, column=3, padx=(6, 12), pady=10)

        self.orders_list = customtkinter.CTkScrollableFrame(self, fg_color="transparent")
        self.orders_list.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 15))
        self.orders_list.grid_columnconfigure(0, weight=1)

        self._all_orders: list[Any] = []

    def load_orders(self) -> None:
        try:
            self._all_orders = self.controller.orders_facade.list_orders()
            self.filter_orders()
        except Exception as error:
            LOGGER.error("Failed to load orders: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка загрузки заказов", str(error), error=True)

    def filter_orders(self) -> None:
        query = self.search_entry.get().strip().lower()
        stage_choice = self.stage_filter.get()
        kind_choice = self.kind_filter.get()

        stage_mapping = {
            "В работе (активные)": "in_progress",
            "Сдан": "delivered",
            "Гарантия": "warranty",
            "Архив": "archive",
        }
        kind_mapping = {
            "ВПФ": "vpf",
            "Отчет": "report",
            "Обработка": "processor",
            "Расширение": "extension",
            "ТЗ": "tz",
        }

        filtered: list[Any] = []
        for order in self._all_orders:
            if stage_choice in stage_mapping:
                target_stage = stage_mapping[stage_choice]
                if target_stage == "in_progress" and order.stage not in {"intake", "development", "testing", "rework"}:
                    continue
                if target_stage != "in_progress" and order.stage != target_stage:
                    continue

            if kind_choice in kind_mapping and order.kind != kind_mapping[kind_choice]:
                continue

            if query:
                haystack = f"{order.order_id} {order.client} {order.title} {order.configuration}".lower()
                if query not in haystack:
                    continue

            filtered.append(order)

        self._render_table(filtered)

    def _render_table(self, orders: list[Any]) -> None:
        for child in self.orders_list.winfo_children():
            child.destroy()

        if not orders:
            empty_lbl = customtkinter.CTkLabel(
                self.orders_list,
                text="Заказы не найдены",
                text_color="#94A3B8",
                font=customtkinter.CTkFont(size=14),
            )
            empty_lbl.grid(row=0, column=0, pady=20)
            return

        for index, order in enumerate(orders):
            row_frame = customtkinter.CTkFrame(self.orders_list, fg_color="#1E293B", corner_radius=6)
            row_frame.grid(row=index, column=0, sticky="ew", pady=4)
            row_frame.grid_columnconfigure(0, weight=1)
            row_frame.grid_columnconfigure(1, weight=0)

            left_box = customtkinter.CTkFrame(row_frame, fg_color="transparent")
            left_box.grid(row=0, column=0, sticky="w", padx=14, pady=10)

            title_row = customtkinter.CTkLabel(
                left_box,
                text=f"{order.order_id}  |  {order.client}  |  {order.title}",
                font=customtkinter.CTkFont(size=14, weight="bold"),
            )
            title_row.pack(anchor="w")

            meta_line = (
                f"{order.kind.upper()}  •  "
                f"{order.configuration or 'Общая 1С'}  •  "
                f"{order.price:,.0f} ₽  •  "
                f"{order.hours_spent:.1f} ч  •  "
                f"Вызовы: {order.mcp_calls}/25  •  "
                f"Дедлайн: {order.deadline or '—'}"
            ).replace(",", " ")
            meta_lbl = customtkinter.CTkLabel(
                left_box,
                text=meta_line,
                font=customtkinter.CTkFont(size=12),
                text_color="#94A3B8",
            )
            meta_lbl.pack(anchor="w", pady=(2, 0))

            right_box = customtkinter.CTkFrame(row_frame, fg_color="transparent")
            right_box.grid(row=0, column=1, sticky="e", padx=14, pady=10)

            stage_color = STAGE_COLORS.get(order.stage, "#64748B")
            badge = customtkinter.CTkLabel(
                right_box,
                text=f" {order.stage.upper()} ",
                text_color=stage_color,
                font=customtkinter.CTkFont(size=12, weight="bold"),
            )
            badge.pack(side="left", padx=10)

            open_btn = customtkinter.CTkButton(
                right_box,
                text="Открыть",
                width=80,
                command=lambda ord_id=order.order_id: self.controller.show_view("order_detail", order_id=ord_id),
            )
            open_btn.pack(side="left")
