from __future__ import annotations

import csv
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import customtkinter

from cockpit.ui.theme import (
    ACCENT_INDIGO_FG,
    BG_ELEVATED,
    BG_MAIN,
    BG_SURFACE,
    BORDER,
    DANGER_FG,
    SUCCESS_FG,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING_FG,
    create_button,
    create_card,
    font_mono,
    font_ui,
)

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
        self.top_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 8))
        self.top_frame.grid_columnconfigure(0, weight=1)

        self.title_label = customtkinter.CTkLabel(
            self.top_frame,
            text="Финансовая аналитика и чистая маржа",
            font=font_ui(20, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.title_label.grid(row=0, column=0, sticky="w")

        btn_box = customtkinter.CTkFrame(self.top_frame, fg_color="transparent")
        btn_box.grid(row=0, column=1, sticky="e")

        self.export_btn = create_button(
            btn_box,
            text="Экспорт в CSV",
            variant="primary",
            command=self.on_export_csv,
        )
        self.export_btn.pack(side="left", padx=4)

        self.refresh_btn = create_button(
            btn_box,
            text="↻ Обновить",
            variant="secondary",
            width=96,
            command=self.load_data,
        )
        self.refresh_btn.pack(side="left", padx=(4, 0))

        self.kpi_frame = create_card(self, fg_color=BG_SURFACE, corner_radius=8)
        self.kpi_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=8)
        self.kpi_frame.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.kpi_revenue = customtkinter.CTkLabel(self.kpi_frame, text="Выручка\n0 ₽", font=font_mono(14, "bold"), text_color=SUCCESS_FG)
        self.kpi_revenue.grid(row=0, column=0, padx=10, pady=14)

        self.kpi_net_margin = customtkinter.CTkLabel(self.kpi_frame, text="Чистая маржа\n0 ₽", font=font_mono(14, "bold"), text_color=ACCENT_INDIGO_FG)
        self.kpi_net_margin.grid(row=0, column=1, padx=10, pady=14)

        self.kpi_ai_cost = customtkinter.CTkLabel(self.kpi_frame, text="Расход AI\n0.00 ₽", font=font_mono(14, "bold"), text_color=DANGER_FG)
        self.kpi_ai_cost.grid(row=0, column=2, padx=10, pady=14)

        self.kpi_margin = customtkinter.CTkLabel(self.kpi_frame, text="Маржа %\n100%", font=font_mono(14, "bold"), text_color=TEXT_PRIMARY)
        self.kpi_margin.grid(row=0, column=3, padx=10, pady=14)

        self.kpi_rate = customtkinter.CTkLabel(self.kpi_frame, text="Ставка ₽/ч\n0 ₽/ч", font=font_mono(14, "bold"), text_color=WARNING_FG)
        self.kpi_rate.grid(row=0, column=4, padx=10, pady=14)

        self.table_scroll = customtkinter.CTkScrollableFrame(self, fg_color="transparent")
        self.table_scroll.grid(row=2, column=0, sticky="nsew", padx=20, pady=(4, 14))
        self.table_scroll.grid_columnconfigure(0, weight=1)

        self._cached_orders: list[Any] = []

    def load_data(self) -> None:
        try:
            summary = self.controller.orders_facade.get_financial_summary()
            self.kpi_revenue.configure(text=f"Сдано (выручка)\n{summary.delivered_value:,.0f} ₽".replace(",", " "))
            self.kpi_net_margin.configure(text=f"Чистая маржа\n{summary.total_net_margin_rub:,.0f} ₽".replace(",", " "))
            if summary.total_virtual_cost_rub > 0 and summary.total_real_ai_cost_rub == 0:
                ai_txt = f"Расход AI\n0.00 ₽ (~{summary.total_virtual_cost_rub:.1f} ₽)"
            else:
                ai_txt = f"Расход AI\n{summary.total_real_ai_cost_rub:.2f} ₽"
            self.kpi_ai_cost.configure(text=ai_txt)
            self.kpi_margin.configure(text=f"Маржа %\n{summary.margin_percent:.0f}%")
            self.kpi_rate.configure(text=f"Ставка ₽/ч\n{summary.hourly_rate:,.0f} ₽/ч".replace(",", " "))

            self._cached_orders = self.controller.orders_facade.list_orders()
            self._render_table(self._cached_orders)
        except Exception as error:
            LOGGER.error("Failed to load finance data: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка финансов", str(error), error=True)

    def _render_table(self, orders: list[Any]) -> None:
        for child in self.table_scroll.winfo_children():
            child.destroy()

        if not orders:
            lbl = customtkinter.CTkLabel(self.table_scroll, text="Нет данных по заказам", text_color=TEXT_SECONDARY, font=font_ui(13))
            lbl.grid(row=0, column=0, pady=20)
            return

        for index, order in enumerate(orders):
            row_frame = create_card(self.table_scroll, fg_color=BG_SURFACE, corner_radius=8)
            row_frame.grid(row=index, column=0, sticky="ew", pady=3)
            row_frame.grid_columnconfigure(0, weight=1)
            row_frame.grid_columnconfigure(1, weight=0)

            left = customtkinter.CTkFrame(row_frame, fg_color="transparent")
            left.grid(row=0, column=0, sticky="w", padx=14, pady=10)

            txt = f"{order.order_id}  ·  @{order.client}  ·  {order.title}"
            customtkinter.CTkLabel(left, text=txt, font=font_ui(13, "bold"), text_color=TEXT_PRIMARY).pack(anchor="w")

            warn_tag = "  ·  ⚠ API > 5% (Эконом)" if order.is_api_over_threshold else ""
            sub = (
                f"{order.kind.upper()}  ·  {order.stage}  ·  "
                f"Комиссия Kwork: {order.kwork_fee_rub:,.0f} ₽  ·  "
                f"API: {order.cost_rub:.2f} ₽ (вирт: {order.virtual_cost_rub:.2f} ₽){warn_tag}"
            ).replace(",", " ")
            customtkinter.CTkLabel(left, text=sub, font=font_mono(11), text_color=TEXT_SECONDARY).pack(anchor="w", pady=(2, 0))

            right = customtkinter.CTkFrame(row_frame, fg_color="transparent")
            right.grid(row=0, column=1, sticky="e", padx=14, pady=10)

            margin_str = f"Маржа: {order.net_margin_rub:,.0f} ₽ ({order.price:,.0f} ₽)".replace(",", " ")
            customtkinter.CTkLabel(
                right,
                text=margin_str,
                font=font_mono(13, "bold"),
                text_color=SUCCESS_FG if order.net_margin_rub >= 0 else DANGER_FG,
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
                writer.writerow([
                    "Order ID", "Client", "Title", "Kind", "Stage",
                    "Price", "Kwork Fee", "API Cost RUB", "Virtual Cost RUB",
                    "Net Margin RUB", "Hours", "MCP Calls", "Economy Mode",
                ])
                for order in self._cached_orders:
                    writer.writerow([
                        order.order_id,
                        order.client,
                        order.title,
                        order.kind,
                        order.stage,
                        order.price,
                        order.kwork_fee_rub,
                        order.cost_rub,
                        order.virtual_cost_rub,
                        order.net_margin_rub,
                        order.hours_spent,
                        order.mcp_calls,
                        order.economy_mode,
                    ])
            self.controller.show_notification("Экспорт завершен", f"Файл сохранен:\n{target_file.name}")
        except Exception as error:
            LOGGER.error("Export CSV error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка экспорта", str(error), error=True)
