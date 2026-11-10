from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import customtkinter

from cockpit.services.kwork_sync import get_attachment_counts
from cockpit.ui.theme import (
    ACCENT_INDIGO_BG,
    ACCENT_INDIGO_FG,
    BG_ELEVATED,
    BG_MAIN,
    BG_SURFACE,
    BORDER,
    BORDER_HOVER,
    NEUTRAL_BG,
    NEUTRAL_FG,
    STAGE_STYLES,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    create_badge,
    create_button,
    create_card,
    create_entry,
    create_option_menu,
    font_mono,
    font_ui,
    format_deadline_countdown,
    parse_deadline_timestamp,
)

LOGGER = logging.getLogger("cockpit.view_orders")
if not LOGGER.handlers:
    _log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handler = RotatingFileHandler(_log_dir / "cockpit.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)

KIND_PILL_LABELS: dict[str, str] = {
    "vpf": "epf · ВПФ",
    "report": "erf · СКД",
    "ext": "cfe · Расш.",
    "extension": "cfe · Расш.",
    "processor": "epf · Обр.",
    "intake": "tz · ТЗ",
    "project": "prj · Проект",
}

ACTIVE_STAGES = {"lead", "in_progress", "review", "intake", "development", "testing", "rework"}


def _sort_key_by_urgency(order: Any) -> tuple[int, int, str]:
    is_active = 0 if order.stage in ACTIVE_STAGES else 1
    dl_ts = parse_deadline_timestamp(getattr(order, "deadline_timestamp", 0), order.deadline or "")
    _, _, _, rem_sec = format_deadline_countdown(dl_ts, order.deadline or "")
    return (is_active, rem_sec, str(order.order_id))


class ViewOrders(customtkinter.CTkFrame):
    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.controller = controller
        self._tick_active = True

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.top_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.top_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(14, 6))
        self.top_frame.grid_columnconfigure(0, weight=1)
        self.top_frame.grid_columnconfigure(1, weight=0)

        title_box = customtkinter.CTkFrame(self.top_frame, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w")

        self.title_label = customtkinter.CTkLabel(
            title_box,
            text="Реестр заказов",
            font=font_ui(20, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.title_label.pack(side="left")

        self.count_pill = create_badge(title_box, "0", fg_color=BG_ELEVATED, text_color=TEXT_SECONDARY, mono=True)
        self.count_pill.pack(side="left", padx=(10, 0))

        self.new_order_btn = create_button(
            self.top_frame,
            text="+ Новый заказ",
            variant="primary",
            height=32,
            command=lambda: self.controller.show_view("new_order"),
        )
        self.new_order_btn.grid(row=0, column=1, sticky="e")

        self.filter_frame = create_card(self, fg_color=BG_SURFACE, corner_radius=8)
        self.filter_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 8))
        self.filter_frame.grid_columnconfigure(0, weight=1)

        self.search_entry = create_entry(
            self.filter_frame,
            placeholder_text="Поиск по клиенту, ID, конфигурации или названию... (Ctrl+K)",
            height=32,
        )
        self.search_entry.grid(row=0, column=0, padx=(10, 6), pady=8, sticky="ew")
        self.search_entry.bind("<KeyRelease>", lambda event: self.filter_orders())

        self.stage_filter = create_option_menu(
            self.filter_frame,
            values=["Все статусы", "В работе (активные)", "Сдан", "Гарантия", "Архив"],
            height=32,
            command=lambda choice: self.filter_orders(),
        )
        self.stage_filter.grid(row=0, column=1, padx=4, pady=8)

        self.kind_filter = create_option_menu(
            self.filter_frame,
            values=["Все типы", "ВПФ", "Отчет", "Обработка", "Расширение", "ТЗ"],
            height=32,
            command=lambda choice: self.filter_orders(),
        )
        self.kind_filter.grid(row=0, column=2, padx=4, pady=8)

        self.refresh_btn = create_button(
            self.filter_frame,
            text="↻ Обновить",
            variant="secondary",
            height=32,
            width=96,
            command=self.load_orders,
        )
        self.refresh_btn.grid(row=0, column=3, padx=(4, 10), pady=8)

        self.orders_list = customtkinter.CTkScrollableFrame(self, fg_color="transparent")
        self.orders_list.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 14))
        self.orders_list.grid_columnconfigure(0, weight=1)

        self._all_orders: list[Any] = []
        self._filtered_orders: list[Any] = []
        self._deadline_labels: list[tuple[customtkinter.CTkFrame, Any]] = []
        self._tick_after_id: Any = self.after(60_000, self._tick_timers)

    def _tick_timers(self) -> None:
        if not self._tick_active or not self.winfo_exists():
            return
        try:
            for pill, order in list(self._deadline_labels):
                if pill.winfo_exists() and hasattr(pill, "text_label"):
                    txt, fg, bg, _ = format_deadline_countdown(
                        getattr(order, "deadline_timestamp", 0),
                        order.deadline or "",
                    )
                    pill.configure(fg_color=bg)
                    pill.text_label.configure(text=txt, text_color=fg)
        except Exception as exc:
            LOGGER.debug("Timer tick skipped: %s", exc)
        if self._tick_active and self.winfo_exists():
            self._tick_after_id = self.after(60_000, self._tick_timers)

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
                if target_stage == "in_progress" and order.stage not in ACTIVE_STAGES:
                    continue
                if target_stage != "in_progress" and order.stage != target_stage:
                    continue

            if kind_choice in kind_mapping:
                mapped_k = kind_mapping[kind_choice]
                if mapped_k == "extension" and order.kind not in ("ext", "extension"):
                    continue
                if mapped_k == "tz" and order.kind not in ("intake", "tz"):
                    continue
                if mapped_k not in ("extension", "tz") and order.kind != mapped_k:
                    continue

            if query:
                haystack = f"{order.order_id} {order.client} {order.title} {order.configuration}".lower()
                if query not in haystack:
                    continue

            filtered.append(order)

        filtered.sort(key=_sort_key_by_urgency)
        self._filtered_orders = filtered
        if hasattr(self.count_pill, "text_label"):
            self.count_pill.text_label.configure(text=str(len(filtered)))
        self._render_table(filtered)

    def _render_table(self, orders: list[Any]) -> None:
        self._deadline_labels.clear()
        for child in self.orders_list.winfo_children():
            child.destroy()

        if not orders:
            empty_card = create_card(self.orders_list, fg_color=BG_SURFACE)
            empty_card.grid(row=0, column=0, sticky="ew", pady=16)
            empty_lbl = customtkinter.CTkLabel(
                empty_card,
                text="Заказы не найдены. Нажмите «+ Новый заказ» или «↻ Обновить Kwork».",
                text_color=TEXT_SECONDARY,
                font=font_ui(13),
            )
            empty_lbl.pack(pady=28)
            return

        for index, order in enumerate(orders):
            card = create_card(self.orders_list, fg_color=BG_SURFACE, border_color=BORDER, corner_radius=8)
            card.grid(row=index, column=0, sticky="ew", pady=4)
            card.grid_columnconfigure(0, weight=1)
            card.grid_columnconfigure(1, weight=0)

            left_box = customtkinter.CTkFrame(card, fg_color="transparent")
            left_box.grid(row=0, column=0, sticky="ew", padx=14, pady=10)

            top_row = customtkinter.CTkFrame(left_box, fg_color="transparent")
            top_row.pack(fill="x", anchor="w")

            id_lbl = customtkinter.CTkLabel(
                top_row,
                text=order.order_id,
                font=font_mono(11, "bold"),
                text_color=ACCENT_INDIGO_FG,
            )
            id_lbl.pack(side="left")

            sep_lbl = customtkinter.CTkLabel(
                top_row,
                text="·",
                font=font_ui(12, "bold"),
                text_color=TEXT_MUTED,
            )
            sep_lbl.pack(side="left", padx=6)

            raw_title = str(order.title or order.order_id)
            short_title = raw_title if len(raw_title) <= 58 else raw_title[:55].rstrip() + "..."
            title_lbl = customtkinter.CTkLabel(
                top_row,
                text=short_title,
                font=font_ui(14, "bold"),
                text_color=TEXT_PRIMARY,
            )
            title_lbl.pack(side="left")

            client_lbl = customtkinter.CTkLabel(
                top_row,
                text=f"  @{order.client}",
                font=font_ui(12),
                text_color=TEXT_SECONDARY,
            )
            client_lbl.pack(side="left", padx=(6, 0))

            pills_row = customtkinter.CTkFrame(left_box, fg_color="transparent")
            pills_row.pack(fill="x", anchor="w", pady=(6, 0))

            cfg_name = order.configuration or "Общая 1С"
            cfg_pill = create_badge(pills_row, cfg_name, fg_color=BG_ELEVATED, text_color=TEXT_PRIMARY, font_size=11)
            cfg_pill.pack(side="left", padx=(0, 6))

            kind_label = KIND_PILL_LABELS.get(str(order.kind).lower(), str(order.kind).lower())
            kind_pill = create_badge(pills_row, kind_label, fg_color=ACCENT_INDIGO_BG, text_color=ACCENT_INDIGO_FG, mono=True, font_size=11)
            kind_pill.pack(side="left", padx=(0, 6))

            att_counts = get_attachment_counts(order.order_dir) if order.order_dir else {"files": 0, "screenshots": 0}
            att_text = f"📎 {att_counts['files']}  |  🖼 {att_counts['screenshots']}"
            att_pill = create_badge(pills_row, att_text, fg_color=NEUTRAL_BG, text_color=TEXT_SECONDARY, mono=True, font_size=11)
            att_pill.pack(side="left", padx=(0, 8))

            meta_text = f"{order.price:,.0f} ₽  ·  {order.hours_spent:.1f} ч  ·  MCP {order.mcp_calls}/25".replace(",", " ")
            meta_lbl = customtkinter.CTkLabel(
                pills_row,
                text=meta_text,
                font=font_mono(11),
                text_color=TEXT_SECONDARY,
            )
            meta_lbl.pack(side="left")

            right_box = customtkinter.CTkFrame(card, fg_color="transparent")
            right_box.grid(row=0, column=1, sticky="e", padx=14, pady=10)

            dl_text, dl_fg, dl_bg, _ = format_deadline_countdown(
                getattr(order, "deadline_timestamp", 0),
                order.deadline or "",
            )
            dl_pill = create_badge(right_box, dl_text, fg_color=dl_bg, text_color=dl_fg, mono=True, font_size=11)
            dl_pill.pack(side="left", padx=(0, 6))
            self._deadline_labels.append((dl_pill, order))

            st_label, st_fg, st_bg = STAGE_STYLES.get(order.stage, (order.stage, NEUTRAL_FG, NEUTRAL_BG))
            stage_pill = create_badge(right_box, st_label, fg_color=st_bg, text_color=st_fg, font_size=11)
            stage_pill.pack(side="left", padx=(0, 8))

            open_btn = create_button(
                right_box,
                text="Открыть →",
                variant="secondary",
                height=30,
                width=92,
                command=lambda oid=order.order_id: self.controller.show_view("order_detail", order_id=oid),
            )
            open_btn.pack(side="left")

            def on_enter(event: Any, c: customtkinter.CTkFrame = card) -> None:
                if c.winfo_exists():
                    c.configure(fg_color=BG_ELEVATED, border_color=BORDER_HOVER)

            def on_leave(event: Any, c: customtkinter.CTkFrame = card) -> None:
                if c.winfo_exists():
                    c.configure(fg_color=BG_SURFACE, border_color=BORDER)

            card.bind("<Enter>", on_enter)
            card.bind("<Leave>", on_leave)

            def bind_recursive(widget: Any, oid: str = order.order_id) -> None:
                widget.bind("<Button-1>", lambda e: self.controller.show_view("order_detail", order_id=oid))
                for child in widget.winfo_children():
                    if not isinstance(child, customtkinter.CTkButton):
                        bind_recursive(child, oid)

            bind_recursive(left_box)
            card.bind("<Button-1>", lambda e, oid=order.order_id: self.controller.show_view("order_detail", order_id=oid))

    def destroy(self) -> None:
        self._tick_active = False
        if self._tick_after_id is not None:
            try:
                self.after_cancel(self._tick_after_id)
            except Exception as exc:
                LOGGER.debug("ViewOrders after_cancel skipped: %s", exc)
        super().destroy()
