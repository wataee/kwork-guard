from __future__ import annotations

import csv
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import customtkinter

LOGGER = logging.getLogger("cockpit.view_finance")
if not LOGGER.handlers:
    _log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handler = RotatingFileHandler(_log_dir / "cockpit.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)


class ViewFinance(customtkinter.CTkFrame):
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
            text="📊 Финансовая аналитика и маржинальность",
            font=customtkinter.CTkFont(size=22, weight="bold"),
        )
        self.title_label.grid(row=0, column=0, sticky="w")

        btn_box = customtkinter.CTkFrame(self.top_frame, fg_color="transparent")
        btn_box.grid(row=0, column=1, sticky="e")

        self.export_btn = customtkinter.CTkButton(
            btn_box,
            text="📥 Экспорт в CSV",
            fg_color="#0D9488",
            hover_color="#0F766E",
            command=self.on_export_csv,
        )
        self.export_btn.pack(side="left", padx=4)

        self.refresh_btn = customtkinter.CTkButton(
            btn_box,
            text="🔄 Обновить",
            width=90,
            command=self.load_data,
        )
        self.refresh_btn.pack(side="left", padx=4)

        self.kpi_frame = customtkinter.CTkFrame(self, fg_color="#1E293B", corner_radius=8)
        self.kpi_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=8)
        self.kpi_frame.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.kpi_revenue = customtkinter.CTkLabel(self.kpi_frame, text="Выручка\n0 ₽", font=customtkinter.CTkFont(size=14, weight="bold"), text_color="#10B981")
        self.kpi_revenue.grid(row=0, column=0, padx=10, pady=12)

        self.kpi_ai_cost = customtkinter.CTkLabel(self.kpi_frame, text="Расход AI\n0.00 ₽", font=customtkinter.CTkFont(size=14, weight="bold"), text_color="#EF4444")
        self.kpi_ai_cost.grid(row=0, column=1, padx=10, pady=12)

        self.kpi_margin = customtkinter.CTkLabel(self.kpi_frame, text="Маржинальность\n100%", font=customtkinter.CTkFont(size=14, weight="bold"), text_color="#38BDF8")
        self.kpi_margin.grid(row=0, column=2, padx=10, pady=12)

        self.kpi_rate = customtkinter.CTkLabel(self.kpi_frame, text="Ставка ₽/ч\n0 ₽/ч", font=customtkinter.CTkFont(size=14, weight="bold"))
        self.kpi_rate.grid(row=0, column=3, padx=10, pady=12)

        self.kpi_pipeline = customtkinter.CTkLabel(self.kpi_frame, text="В пайплайне\n0 ₽", font=customtkinter.CTkFont(size=14, weight="bold"), text_color="#F59E0B")
        self.kpi_pipeline.grid(row=0, column=4, padx=10, pady=12)

        self.table_scroll = customtkinter.CTkScrollableFrame(self, fg_color="transparent")
        self.table_scroll.grid(row=2, column=0, sticky="nsew", padx=20, pady=(4, 15))
        self.table_scroll.grid_columnconfigure(0, weight=1)

        self._cached_orders: list[Any] = []

    def load_data(self) -> None:
        try:
            summary = self.controller.orders_facade.get_financial_summary()
            self.kpi_revenue.configure(text=f"Выручка\n{summary.delivered_value:,.0f} ₽".replace(",", " "))
            self.kpi_ai_cost.configure(text=f"Расход AI\n{summary.total_real_ai_cost_rub:.2f} ₽")
            self.kpi_margin.configure(text=f"Маржинальность\n{summary.margin_percent:.0f}%")
            self.kpi_rate.configure(text=f"Ставка ₽/ч\n{summary.hourly_rate:,.0f} ₽/ч".replace(",", " "))
            self.kpi_pipeline.configure(text=f"В пайплайне\n{summary.in_progress_value:,.0f} ₽".replace(",", " "))

            self._cached_orders = self.controller.orders_facade.list_orders()
            self._render_table(self._cached_orders)
        except Exception as error:
            LOGGER.error("Failed to load finance data: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка финансов", str(error), error=True)

    def _render_table(self, orders: list[Any]) -> None:
        for child in self.table_scroll.winfo_children():
            child.destroy()

        if not orders:
            lbl = customtkinter.CTkLabel(self.table_scroll, text="Нет данных по заказам", text_color="#94A3B8")
            lbl.grid(row=0, column=0, pady=20)
            return

        for index, order in enumerate(orders):
            row_frame = customtkinter.CTkFrame(self.table_scroll, fg_color="#1E293B", corner_radius=6)
            row_frame.grid(row=index, column=0, sticky="ew", pady=3)
            row_frame.grid_columnconfigure(0, weight=1)
            row_frame.grid_columnconfigure(1, weight=0)

            left = customtkinter.CTkFrame(row_frame, fg_color="transparent")
            left.grid(row=0, column=0, sticky="w", padx=12, pady=8)

            txt = f"{order.order_id}  |  {order.client}  |  {order.title}"
            customtkinter.CTkLabel(left, text=txt, font=customtkinter.CTkFont(weight="bold")).pack(anchor="w")

            sub = f"{order.kind.upper()}  •  Статус: {order.stage}  •  Часы: {order.hours_spent:.1f} ч"
            customtkinter.CTkLabel(left, text=sub, font=customtkinter.CTkFont(size=11), text_color="#94A3B8").pack(anchor="w")

            right = customtkinter.CTkFrame(row_frame, fg_color="transparent")
            right.grid(row=0, column=1, sticky="e", padx=12, pady=8)

            price_str = f"{order.price:,.0f} ₽".replace(",", " ")
            customtkinter.CTkLabel(
                right,
                text=price_str,
                font=customtkinter.CTkFont(size=14, weight="bold"),
                text_color="#10B981",
            ).pack(side="right", padx=6)

    def on_export_csv(self) -> None:
        if not self._cached_orders:
            self.controller.show_notification("Экспорт", "Нет данных для экспорта", error=True)
            return
        try:
            target_file = self.controller.base_dir / "reports" / "finance_orders.csv"
            target_file.parent.mkdir(parents=True, exist_ok=True)
            with target_file.open("w", newline="", encoding="utf-8-sig") as file_handle:
                writer = csv.writer(file_handle, delimiter=";")
                writer.writerow(["Order ID", "Client", "Title", "Kind", "Stage", "Price", "Hours", "MCP Calls"])
                for order in self._cached_orders:
                    writer.writerow([
                        order.order_id,
                        order.client,
                        order.title,
                        order.kind,
                        order.stage,
                        order.price,
                        order.hours_spent,
                        order.mcp_calls,
                    ])
            self.controller.show_notification("Экспорт завершен", f"Файл сохранен:\n{target_file.name}")
        except Exception as error:
            LOGGER.error("Export CSV error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка экспорта", str(error), error=True)
