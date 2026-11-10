from __future__ import annotations

import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from typing import Any, Optional
import customtkinter
from PIL import Image

from cockpit.services.kwork_sync import load_attachments_manifest
from cockpit.services.workspace_bridge import (
    launch_antigravity_workspace,
    prepare_antigravity_workspace,
)
from cockpit.ui.theme import (
    ACCENT_INDIGO_BG,
    ACCENT_INDIGO_FG,
    ACCENT_PRIMARY,
    BG_ELEVATED,
    BG_MAIN,
    BG_SURFACE,
    BORDER,
    DANGER_BG,
    DANGER_FG,
    NEUTRAL_BG,
    NEUTRAL_FG,
    RADIUS_CONTROL,
    STAGE_STYLES,
    SUCCESS_BG,
    SUCCESS_FG,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING_BG,
    WARNING_FG,
    create_badge,
    create_button,
    create_card,
    create_entry,
    create_option_menu,
    create_textbox,
    font_mono,
    font_ui,
    format_deadline_countdown,
)
from guard.bsl_linter import format_diagnostics_for_llm, lint_bsl_code
from guard.config import load_config
from guard.library import save_snippet

LOGGER = logging.getLogger("cockpit.view_order_detail")
if not LOGGER.handlers:
    _log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handler = RotatingFileHandler(_log_dir / "cockpit.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)

STAGE_OPTIONS: list[str] = ["lead", "in_progress", "review", "delivered", "warranty", "cancelled"]
KIND_OPTIONS: list[str] = ["vpf", "report", "processor", "ext", "intake"]

DEFAULT_CHECKLIST: dict[str, bool] = {
    "bsl_lint_clean": False,
    "tested_in_thin_client": False,
    "outputs_validated": False,
    "no_hardcoded_paths": False,
    "delivery_docs_ready": False,
}

CHECKLIST_LABELS: dict[str, str] = {
    "bsl_lint_clean": "1. Синтаксический контроль BSL пройден без ошибок",
    "tested_in_thin_client": "2. Открытие и выполнение в тонком клиенте 1С проверено",
    "outputs_validated": "3. Формирование печатной формы/отчета проверено на данных",
    "no_hardcoded_paths": "4. Нет абсолютных тестовых путей и отладочных 'Сообщить()'",
    "delivery_docs_ready": "5. Инструкция по подключению и бриф согласованы",
}


class ViewOrderDetail(customtkinter.CTkFrame):
    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.controller = controller
        self.current_order_id: Optional[str] = None
        self.current_detail: Any = None
        self._thumb_refs: list[Any] = []
        self._tick_active = True
        self._tick_after_id: Optional[str] = None

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.top_bar = create_card(self, fg_color=BG_SURFACE, corner_radius=8)
        self.top_bar.grid(row=0, column=0, sticky="ew", padx=20, pady=(12, 8))
        self.top_bar.grid_columnconfigure(1, weight=1)

        self.back_btn = create_button(
            self.top_bar,
            text="← Назад",
            variant="secondary",
            height=30,
            width=78,
            command=lambda: self.controller.show_view("orders"),
        )
        self.back_btn.grid(row=0, column=0, padx=(12, 10), pady=10, sticky="w")

        hdr_meta = customtkinter.CTkFrame(self.top_bar, fg_color="transparent")
        hdr_meta.grid(row=0, column=1, sticky="ew", pady=8)

        self.header_label = customtkinter.CTkLabel(
            hdr_meta,
            text="Заказ #...",
            font=font_ui(16, "bold"),
            text_color=TEXT_PRIMARY,
            anchor="w",
        )
        self.header_label.pack(anchor="w")

        self.header_sub_lbl = customtkinter.CTkLabel(
            hdr_meta,
            text="",
            font=font_mono(11),
            text_color=TEXT_SECONDARY,
            anchor="w",
        )
        self.header_sub_lbl.pack(anchor="w")

        hdr_actions = customtkinter.CTkFrame(self.top_bar, fg_color="transparent")
        hdr_actions.grid(row=0, column=2, padx=(10, 12), pady=8, sticky="e")

        self.stage_menu = create_option_menu(
            hdr_actions,
            values=STAGE_OPTIONS,
            height=30,
            width=125,
            command=self.on_change_stage,
        )
        self.stage_menu.pack(side="left", padx=4)

        self.start_work_btn = create_button(
            hdr_actions,
            text="Открыть в Antigravity",
            variant="primary",
            height=30,
            command=self.on_open_antigravity_workspace,
        )
        self.start_work_btn.pack(side="left", padx=(6, 0))

        self.tabs = customtkinter.CTkTabview(
            self,
            fg_color=BG_MAIN,
            segmented_button_fg_color=BG_SURFACE,
            segmented_button_selected_color=BG_ELEVATED,
            segmented_button_selected_hover_color="#23232A",
            segmented_button_unselected_color=BG_SURFACE,
            segmented_button_unselected_hover_color=BG_ELEVATED,
            text_color=TEXT_PRIMARY,
            corner_radius=8,
        )
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 14))

        self.tab_workspace = self.tabs.add("Рабочий стол")
        self.tab_overview = self.tabs.add("Обзор")
        self.tab_dev = self.tabs.add("Разработка")
        self.tab_brief = self.tabs.add("ТЗ и Бриф")
        self.tab_budget = self.tabs.add("Бюджет и Логи")
        self.tab_delivery = self.tabs.add("Сдача и Чек-лист")
        self.tab_warranty = self.tabs.add("Гарантия и Git")

        self._build_tab_workspace()
        self._build_tab_overview()
        self._build_tab_dev()
        self._build_tab_brief()
        self._build_tab_budget()
        self._build_tab_delivery()
        self._build_tab_warranty()

        self._tick_after_id = self.after(60_000, self._tick_deadline)

    def _tick_deadline(self) -> None:
        if not self._tick_active or not self.winfo_exists():
            return
        if self.current_detail is not None:
            self._update_deadline_banner(self.current_detail)
        if self._tick_active and self.winfo_exists():
            self._tick_after_id = self.after(60_000, self._tick_deadline)

    def _build_tab_workspace(self) -> None:
        self.tab_workspace.grid_rowconfigure(0, weight=1)
        self.tab_workspace.grid_columnconfigure(0, weight=3)
        self.tab_workspace.grid_columnconfigure(1, weight=2)

        left_col = customtkinter.CTkScrollableFrame(self.tab_workspace, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(4, 10), pady=6)
        left_col.grid_columnconfigure(0, weight=1)

        self.ws_tz_card = create_card(left_col, fg_color=BG_SURFACE, corner_radius=8)
        self.ws_tz_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.ws_tz_card.grid_columnconfigure(0, weight=1)

        tz_hdr = customtkinter.CTkFrame(self.ws_tz_card, fg_color="transparent")
        tz_hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        tz_hdr.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            tz_hdr,
            text="Техническое задание (brief.md)",
            font=font_ui(14, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")

        self.ws_copy_context_btn = create_button(
            tz_hdr,
            text="Копировать контекст",
            variant="secondary",
            height=26,
            command=self.on_dev_copy_prompt,
        )
        self.ws_copy_context_btn.grid(row=0, column=1, sticky="e")

        self.ws_tz_box = create_textbox(self.ws_tz_card, height=190, state="disabled")
        self.ws_tz_box.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))

        self.ws_chat_card = create_card(left_col, fg_color=BG_SURFACE, corner_radius=8)
        self.ws_chat_card.grid(row=1, column=0, sticky="nsew")
        self.ws_chat_card.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            self.ws_chat_card,
            text="История переписки с покупателем",
            font=font_ui(14, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 6))

        self.ws_chat_container = customtkinter.CTkFrame(self.ws_chat_card, fg_color="transparent")
        self.ws_chat_container.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.ws_chat_container.grid_columnconfigure(0, weight=1)

        right_col = customtkinter.CTkScrollableFrame(self.tab_workspace, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="nsew", padx=(0, 4), pady=6)
        right_col.grid_columnconfigure(0, weight=1)

        self.dl_card = create_card(right_col, fg_color=BG_SURFACE, corner_radius=8)
        self.dl_card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.dl_card.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            self.dl_card,
            text="ДЕДЛАЙН И СТАТУС",
            font=font_mono(10, "bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(10, 2))

        self.dl_big_label = customtkinter.CTkLabel(
            self.dl_card,
            text="—",
            font=font_mono(20, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.dl_big_label.grid(row=1, column=0, sticky="w", padx=14, pady=2)

        self.dl_sub_label = customtkinter.CTkLabel(
            self.dl_card,
            text="Срок сдачи: —",
            font=font_mono(11),
            text_color=TEXT_SECONDARY,
        )
        self.dl_sub_label.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 12))

        cta_card = create_card(right_col, fg_color=BG_SURFACE, corner_radius=8)
        cta_card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        cta_card.grid_columnconfigure(0, weight=1)

        self.btn_open_agy = create_button(
            cta_card,
            text="Открыть Workspace в Antigravity",
            variant="primary",
            height=38,
            command=self.on_open_antigravity_workspace,
        )
        self.btn_open_agy.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))

        self.btn_quick_deliver = create_button(
            cta_card,
            text="Собрать архив на сдачу и скопировать ответ",
            variant="success",
            height=34,
            command=self.on_deliver_order,
        )
        self.btn_quick_deliver.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        util_row = customtkinter.CTkFrame(cta_card, fg_color="transparent")
        util_row.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        util_row.grid_columnconfigure((0, 1), weight=1)

        create_button(
            util_row,
            text="Открыть папку",
            variant="secondary",
            height=28,
            command=self.on_open_order_folder,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))

        create_button(
            util_row,
            text="Тест в 1С",
            variant="secondary",
            height=28,
            command=self.on_dev_launch_1c,
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        meta_card = create_card(right_col, fg_color=BG_SURFACE, corner_radius=8)
        meta_card.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        meta_card.grid_columnconfigure((0, 1), weight=1)

        customtkinter.CTkLabel(
            meta_card,
            text="КОНФИГУРАЦИЯ И ТИП ЗАДАЧИ",
            font=font_mono(10, "bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 4))

        self.ws_config_entry = create_entry(meta_card, placeholder_text="УТ 11.5 / БП 3.0", height=30)
        self.ws_config_entry.grid(row=1, column=0, sticky="ew", padx=(12, 4), pady=(0, 8))

        self.ws_kind_menu = create_option_menu(meta_card, values=KIND_OPTIONS, height=30)
        self.ws_kind_menu.grid(row=1, column=1, sticky="ew", padx=(4, 12), pady=(0, 8))

        create_button(
            meta_card,
            text="Сохранить и обновить ORDER_CONTEXT",
            variant="secondary",
            height=28,
            command=self.on_save_inspector_meta,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 10))

        self.gallery_card = create_card(right_col, fg_color=BG_SURFACE, corner_radius=8)
        self.gallery_card.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        self.gallery_card.grid_columnconfigure(0, weight=1)

        self.gallery_title = customtkinter.CTkLabel(
            self.gallery_card,
            text="Скриншоты (0)",
            font=font_ui(13, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.gallery_title.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        self.gallery_container = customtkinter.CTkFrame(self.gallery_card, fg_color="transparent")
        self.gallery_container.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))

        self.files_card = create_card(right_col, fg_color=BG_SURFACE, corner_radius=8)
        self.files_card.grid(row=4, column=0, sticky="ew", pady=(0, 6))
        self.files_card.grid_columnconfigure(0, weight=1)

        self.files_title = customtkinter.CTkLabel(
            self.files_card,
            text="Файлы 1С и документы (0)",
            font=font_ui(13, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.files_title.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        self.files_container = customtkinter.CTkFrame(self.files_card, fg_color="transparent")
        self.files_container.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.files_container.grid_columnconfigure(0, weight=1)

    def _build_tab_overview(self) -> None:
        self.tab_overview.grid_columnconfigure((0, 1), weight=1)

        customtkinter.CTkLabel(self.tab_overview, text="Учет времени (ч):", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 2)
        )

        hours_box = customtkinter.CTkFrame(self.tab_overview, fg_color="transparent")
        hours_box.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
        self.hours_label = customtkinter.CTkLabel(hours_box, text="0.0 ч", font=font_mono(14, "bold"), text_color=TEXT_PRIMARY)
        self.hours_label.pack(side="left", padx=(0, 10))

        create_button(hours_box, text="+0.5 ч", variant="secondary", width=64, height=28, command=lambda: self.on_add_hours(0.5)).pack(
            side="left", padx=3
        )
        create_button(hours_box, text="+1.0 ч", variant="secondary", width=64, height=28, command=lambda: self.on_add_hours(1.0)).pack(
            side="left", padx=3
        )

        customtkinter.CTkLabel(self.tab_overview, text="Название:", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 2)
        )
        self.ov_title_entry = create_entry(self.tab_overview)
        self.ov_title_entry.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))

        customtkinter.CTkLabel(self.tab_overview, text="Стоимость (₽):", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=4, column=0, sticky="w", padx=10, pady=(6, 2)
        )
        self.ov_price_entry = create_entry(self.tab_overview, mono=True)
        self.ov_price_entry.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 8))

        customtkinter.CTkLabel(self.tab_overview, text="Дедлайн:", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=4, column=1, sticky="w", padx=10, pady=(6, 2)
        )
        self.ov_deadline_entry = create_entry(self.tab_overview, mono=True)
        self.ov_deadline_entry.grid(row=5, column=1, sticky="ew", padx=10, pady=(0, 8))

        customtkinter.CTkLabel(self.tab_overview, text="Конфигурация:", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=6, column=0, sticky="w", padx=10, pady=(6, 2)
        )
        self.ov_config_entry = create_entry(self.tab_overview)
        self.ov_config_entry.grid(row=7, column=0, sticky="ew", padx=10, pady=(0, 8))

        customtkinter.CTkLabel(self.tab_overview, text="Заметки / Требования клиента:", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=8, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 2)
        )
        self.ov_notes_box = create_textbox(self.tab_overview, height=110)
        self.ov_notes_box.grid(row=9, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 12))

        self.save_overview_btn = create_button(
            self.tab_overview,
            text="Сохранить изменения",
            variant="primary",
            height=34,
            command=self.on_save_overview,
        )
        self.save_overview_btn.grid(row=10, column=0, columnspan=2, sticky="e", padx=10, pady=8)

    def _build_tab_dev(self) -> None:
        self.tab_dev.grid_columnconfigure(0, weight=1)

        actions_bar = customtkinter.CTkFrame(self.tab_dev, fg_color="transparent")
        actions_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=8)

        create_button(actions_bar, text="Открыть в Antigravity", variant="primary", command=self.on_open_antigravity_workspace).pack(
            side="left", padx=(0, 6)
        )
        create_button(actions_bar, text="Открыть в VS Code", variant="blue", command=self.on_dev_open_vscode).pack(
            side="left", padx=4
        )
        create_button(actions_bar, text="Скопировать промпт", variant="secondary", command=self.on_dev_copy_prompt).pack(
            side="left", padx=4
        )
        create_button(actions_bar, text="Тест в тонком клиенте 1С", variant="secondary", command=self.on_dev_launch_1c).pack(
            side="left", padx=4
        )

        customtkinter.CTkLabel(
            self.tab_dev,
            text="Синтаксический контроль BSL / сохранение сниппета в Library:",
            font=font_ui(13, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=1, column=0, sticky="w", padx=10, pady=(10, 4))

        self.bsl_editor = create_textbox(self.tab_dev, height=170, mono=True)
        self.bsl_editor.grid(row=2, column=0, sticky="ew", padx=10, pady=4)

        linter_actions = customtkinter.CTkFrame(self.tab_dev, fg_color="transparent")
        linter_actions.grid(row=3, column=0, sticky="ew", padx=10, pady=6)

        create_button(linter_actions, text="Проверить BSL-код", variant="primary", command=self.on_dev_run_linter).pack(
            side="left", padx=(0, 8)
        )

        self.snippet_id_entry = create_entry(linter_actions, placeholder_text="ID сниппета (custom_calc)", width=190, height=32)
        self.snippet_id_entry.pack(side="left", padx=4)

        self.snippet_type_menu = create_option_menu(linter_actions, values=["bsl", "query"], width=85, height=32)
        self.snippet_type_menu.pack(side="left", padx=4)

        self.btn_save_snippet = create_button(
            linter_actions,
            text="Сохранить сниппет в Library",
            variant="success",
            command=self.on_save_snippet_to_library,
        )
        self.btn_save_snippet.pack(side="left", padx=4)

        self.linter_results = create_textbox(self.tab_dev, height=120, mono=True, state="disabled")
        self.linter_results.grid(row=4, column=0, sticky="ew", padx=10, pady=6)

    def _build_tab_brief(self) -> None:
        self.tab_brief.grid_columnconfigure(0, weight=1)
        self.tab_brief.grid_rowconfigure(1, weight=1)

        btn_row = customtkinter.CTkFrame(self.tab_brief, fg_color="transparent")
        btn_row.grid(row=0, column=0, sticky="ew", padx=10, pady=8)

        create_button(btn_row, text="Сохранить ТЗ", variant="primary", command=self.on_save_brief).pack(side="left", padx=(0, 6))
        create_button(btn_row, text="Проверить полноту ТЗ", variant="secondary", command=self.on_validate_brief).pack(side="left", padx=4)

        self.brief_editor = create_textbox(self.tab_brief, height=360)
        self.brief_editor.grid(row=1, column=0, sticky="nsew", padx=10, pady=6)

    def _build_tab_budget(self) -> None:
        self.tab_budget.grid_columnconfigure(0, weight=1)
        self.tab_budget.grid_rowconfigure(3, weight=1)

        stats_card = create_card(self.tab_budget, fg_color=BG_SURFACE, corner_radius=8)
        stats_card.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
        stats_card.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.b_calls_lbl = customtkinter.CTkLabel(stats_card, text="Вызовы MCP\n0 / 25", font=font_mono(13, "bold"), text_color=TEXT_PRIMARY)
        self.b_calls_lbl.grid(row=0, column=0, padx=8, pady=12)

        self.b_status_lbl = customtkinter.CTkLabel(stats_card, text="Лимит\nВ норме", font=font_ui(13, "bold"), text_color=SUCCESS_FG)
        self.b_status_lbl.grid(row=0, column=1, padx=8, pady=12)

        self.b_tokens_lbl = customtkinter.CTkLabel(stats_card, text="Токены LLM\n0", font=font_mono(13, "bold"), text_color=TEXT_PRIMARY)
        self.b_tokens_lbl.grid(row=0, column=2, padx=8, pady=12)

        self.b_cost_lbl = customtkinter.CTkLabel(stats_card, text="Затраты AI\n0.00 ₽", font=font_mono(13, "bold"), text_color=ACCENT_INDIGO_FG)
        self.b_cost_lbl.grid(row=0, column=3, padx=8, pady=12)

        self.b_margin_lbl = customtkinter.CTkLabel(stats_card, text="Чистая маржа\n0 ₽", font=font_mono(13, "bold"), text_color=SUCCESS_FG)
        self.b_margin_lbl.grid(row=0, column=4, padx=8, pady=12)

        actions_bar = customtkinter.CTkFrame(self.tab_budget, fg_color="transparent")
        actions_bar.grid(row=1, column=0, sticky="ew", padx=10, pady=6)

        create_button(actions_bar, text="+10 вызовов", variant="primary", command=self.on_budget_extend).pack(side="left", padx=(0, 6))
        create_button(actions_bar, text="Сбросить лимит", variant="secondary", command=self.on_budget_reset).pack(side="left", padx=4)

        self.b_guard_metrics_lbl = customtkinter.CTkLabel(
            actions_bar,
            text="",
            font=font_mono(11),
            text_color=TEXT_SECONDARY,
        )
        self.b_guard_metrics_lbl.pack(side="left", padx=14)

        self.b_economy_lbl = customtkinter.CTkLabel(
            actions_bar,
            text="",
            font=font_ui(12, "bold"),
            text_color=WARNING_FG,
        )
        self.b_economy_lbl.pack(side="right", padx=8)

        customtkinter.CTkLabel(
            self.tab_budget,
            text="События MCP-вызовов по заказу:",
            font=font_ui(13, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=2, column=0, sticky="w", padx=10, pady=(10, 4))

        self.mcp_log_box = create_textbox(self.tab_budget, height=220, mono=True, state="disabled")
        self.mcp_log_box.grid(row=3, column=0, sticky="nsew", padx=10, pady=4)

    def _build_tab_delivery(self) -> None:
        self.tab_delivery.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            self.tab_delivery,
            text="Чек-лист готовности заказа к сдаче:",
            font=font_ui(14, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 6))

        self.checklist_vars: dict[str, customtkinter.BooleanVar] = {}
        for idx, (key, label_text) in enumerate(CHECKLIST_LABELS.items()):
            var = customtkinter.BooleanVar(value=False)
            self.checklist_vars[key] = var
            chk = customtkinter.CTkCheckBox(
                self.tab_delivery,
                text=label_text,
                variable=var,
                text_color=TEXT_PRIMARY,
                font=font_ui(12),
                command=self.on_toggle_checklist,
            )
            chk.grid(row=idx + 1, column=0, sticky="w", padx=14, pady=4)

        deliver_action_frame = customtkinter.CTkFrame(self.tab_delivery, fg_color="transparent")
        deliver_action_frame.grid(row=len(CHECKLIST_LABELS) + 1, column=0, sticky="ew", padx=10, pady=20)

        self.btn_deliver_zip = create_button(
            deliver_action_frame,
            text="Собрать поставку (Verify + ZIP + Kwork)",
            variant="success",
            height=40,
            command=self.on_deliver_order,
        )
        self.btn_deliver_zip.pack(side="left", padx=4)

    def _build_tab_warranty(self) -> None:
        self.tab_warranty.grid_columnconfigure(0, weight=1)
        self.tab_warranty.grid_rowconfigure(2, weight=1)

        btn_bar = customtkinter.CTkFrame(self.tab_warranty, fg_color="transparent")
        btn_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=8)

        create_button(btn_bar, text="Создать гарантийную ветку Git", variant="primary", command=self.on_create_warranty_branch).pack(
            side="left", padx=(0, 6)
        )
        create_button(btn_bar, text="Сохранить DECISIONS.md", variant="secondary", command=self.on_save_decisions).pack(
            side="left", padx=4
        )

        customtkinter.CTkLabel(
            self.tab_warranty,
            text="Журнал архитектурных решений заказа (DECISIONS.md):",
            font=font_ui(13, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=1, column=0, sticky="w", padx=10, pady=(8, 4))

        self.decisions_editor = create_textbox(self.tab_warranty, height=300)
        self.decisions_editor.grid(row=2, column=0, sticky="nsew", padx=10, pady=4)

    def load_order(self, order_id: str) -> None:
        self.current_order_id = order_id
        detail = self.controller.orders_facade.get_order(order_id)
        if not detail:
            self.controller.show_notification("Ошибка", f"Заказ {order_id} не найден", error=True)
            return

        self.current_detail = detail
        title_str = detail.title if len(detail.title) <= 60 else detail.title[:57].rstrip() + "..."
        sub_str = f"{detail.order_id}  ·  @{detail.client}  ·  {detail.price:,.0f} ₽  ·  {detail.configuration}".replace(",", " ")
        if len(sub_str) > 85:
            sub_str = sub_str[:82].rstrip() + "..."
        self.header_label.configure(text=title_str)
        self.header_sub_lbl.configure(text=sub_str)

        self._fill_tab_workspace(detail)
        self._fill_tab_overview(detail)
        self._fill_tab_brief(detail)
        self._fill_tab_budget(detail)
        self._fill_tab_delivery(detail)
        self._fill_tab_warranty(detail)

    def _update_deadline_banner(self, detail: Any) -> None:
        dl_text, dl_fg, dl_bg, _ = format_deadline_countdown(
            getattr(detail, "deadline_timestamp", 0),
            detail.deadline or "",
        )
        self.dl_card.configure(fg_color=dl_bg)
        self.dl_big_label.configure(text=dl_text, text_color=dl_fg)
        self.dl_sub_label.configure(text=f"Срок сдачи: {detail.deadline or 'не указан'}")

    def _fill_tab_workspace(self, detail: Any) -> None:
        self._update_deadline_banner(detail)
        self.ws_config_entry.delete(0, "end")
        self.ws_config_entry.insert(0, detail.configuration or "")
        kind_val = detail.kind if detail.kind in KIND_OPTIONS else "ext"
        self.ws_kind_menu.set(kind_val)

        brief_path = detail.order_dir / "brief.md"
        brief_text = brief_path.read_text(encoding="utf-8", errors="replace") if brief_path.is_file() else (detail.notes or "ТЗ отсутствует.")
        self.ws_tz_box.configure(state="normal")
        self.ws_tz_box.delete("1.0", "end")
        self.ws_tz_box.insert("1.0", brief_text)
        self.ws_tz_box.configure(state="disabled")

        self._render_chat_timeline(detail)
        self._render_attachments_inspector(detail)

    def _render_chat_timeline(self, detail: Any) -> None:
        for child in self.ws_chat_container.winfo_children():
            child.destroy()

        chat_file = detail.order_dir / "chat_history.json"
        messages: list[dict[str, Any]] = []
        if chat_file.is_file():
            try:
                raw = json.loads(chat_file.read_text(encoding="utf-8"))
                messages = list(raw.get("messages") or [])
            except Exception as exc:
                LOGGER.warning("Chat timeline load warning: %s", exc)

        if not messages:
            customtkinter.CTkLabel(
                self.ws_chat_container,
                text="История переписки по заказу пока пуста.",
                font=font_ui(12),
                text_color=TEXT_MUTED,
            ).grid(row=0, column=0, sticky="w", pady=10)
            return

        for idx, msg in enumerate(messages):
            is_client = bool(msg.get("is_client", True))
            bubble = create_card(
                self.ws_chat_container,
                fg_color=BG_ELEVATED if is_client else ACCENT_INDIGO_BG,
                border_color=BORDER if is_client else "#4338CA",
                corner_radius=8,
            )
            bubble.grid(row=idx, column=0, sticky="w" if is_client else "e", pady=4)

            ts = int(msg.get("timestamp") or 0)
            ts_str = datetime.datetime.fromtimestamp(ts).strftime("%d.%m %H:%M") if ts > 0 else "—"
            sender = str(msg.get("sender") or (detail.client if is_client else "Вы"))
            customtkinter.CTkLabel(
                bubble,
                text=f"{sender}  ·  {ts_str}",
                font=font_mono(10),
                text_color=TEXT_SECONDARY if is_client else ACCENT_INDIGO_FG,
            ).pack(anchor="w", padx=10, pady=(6, 2))

            txt = str(msg.get("text") or "").strip()
            if txt:
                customtkinter.CTkLabel(
                    bubble,
                    text=txt,
                    font=font_ui(12),
                    text_color=TEXT_PRIMARY,
                    wraplength=500,
                    justify="left",
                ).pack(anchor="w", padx=10, pady=(0, 6))

            for att in msg.get("attachments") or []:
                fname = str(att.get("filename") or "файл")
                customtkinter.CTkLabel(
                    bubble,
                    text=f"📎 {fname}",
                    font=font_mono(11),
                    text_color=ACCENT_INDIGO_FG,
                ).pack(anchor="w", padx=10, pady=(0, 6))

    def _render_attachments_inspector(self, detail: Any) -> None:
        for child in self.gallery_container.winfo_children():
            child.destroy()
        for child in self.files_container.winfo_children():
            child.destroy()
        self._thumb_refs.clear()

        order_dir = detail.order_dir
        manifest = load_attachments_manifest(order_dir)
        entries = list(manifest.get("files") or [])

        seen_paths: set[str] = set()
        screenshots: list[Path] = []
        doc_files: list[tuple[str, Path]] = []

        for entry in entries:
            rel = str(entry.get("relative_path") or "")
            p = order_dir / rel
            if not rel or not p.is_file() or rel in seen_paths:
                continue
            seen_paths.add(rel)
            if str(entry.get("category") or "") == "screenshots":
                screenshots.append(p)
            else:
                doc_files.append((str(entry.get("category") or "documents"), p))

        att_root = order_dir / "attachments"
        if att_root.is_dir():
            for cat in ("screenshots", "1c_files", "documents"):
                sub = att_root / cat
                if sub.is_dir():
                    for p in sorted(sub.iterdir()):
                        if p.is_file():
                            rel = f"attachments/{cat}/{p.name}"
                            if rel not in seen_paths:
                                seen_paths.add(rel)
                                if cat == "screenshots":
                                    screenshots.append(p)
                                else:
                                    doc_files.append((cat, p))

        self.gallery_title.configure(text=f"Скриншоты ({len(screenshots)})")
        if not screenshots:
            customtkinter.CTkLabel(
                self.gallery_container,
                text="Скриншотов нет",
                font=font_ui(11),
                text_color=TEXT_MUTED,
            ).pack(anchor="w", padx=4, pady=4)
        else:
            row_frame = customtkinter.CTkFrame(self.gallery_container, fg_color="transparent")
            row_frame.pack(fill="x")
            for idx, img_path in enumerate(screenshots):
                col_idx = idx % 3
                row_idx = idx // 3
                try:
                    pil_img = Image.open(img_path)
                    ctk_img = customtkinter.CTkImage(light_image=pil_img, dark_image=pil_img, size=(92, 68))
                    self._thumb_refs.append(ctk_img)
                    btn = customtkinter.CTkButton(
                        row_frame,
                        image=ctk_img,
                        text="",
                        width=98,
                        height=74,
                        fg_color=BG_ELEVATED,
                        hover_color="#23232A",
                        border_color=BORDER,
                        border_width=1,
                        corner_radius=6,
                        command=lambda p=img_path: self._open_image_modal(p),
                    )
                    btn.grid(row=row_idx, column=col_idx, padx=3, pady=3)
                except Exception as exc:
                    LOGGER.debug("Thumbnail error for %s: %s", img_path, exc)

        self.files_title.configure(text=f"Файлы 1С и документы ({len(doc_files)})")
        if not doc_files:
            customtkinter.CTkLabel(
                self.files_container,
                text="Файлов нет",
                font=font_ui(11),
                text_color=TEXT_MUTED,
            ).grid(row=0, column=0, sticky="w", padx=4, pady=4)
        else:
            for idx, (cat, fpath) in enumerate(doc_files):
                row = create_card(self.files_container, fg_color=BG_ELEVATED, corner_radius=6)
                row.grid(row=idx, column=0, sticky="ew", pady=2)
                row.grid_columnconfigure(1, weight=1)

                cat_tag = "1С" if cat == "1c_files" else "DOC"
                tag_fg = ACCENT_INDIGO_FG if cat == "1c_files" else NEUTRAL_FG
                tag_bg = ACCENT_INDIGO_BG if cat == "1c_files" else NEUTRAL_BG
                create_badge(row, cat_tag, fg_color=tag_bg, text_color=tag_fg, mono=True, font_size=10).grid(
                    row=0, column=0, padx=(8, 6), pady=6
                )

                customtkinter.CTkLabel(
                    row,
                    text=fpath.name,
                    font=font_mono(11),
                    text_color=TEXT_PRIMARY,
                    anchor="w",
                ).grid(row=0, column=1, sticky="ew", padx=2)

                create_button(
                    row,
                    text="Открыть",
                    variant="secondary",
                    height=24,
                    width=68,
                    command=lambda p=fpath: self._open_file(p),
                ).grid(row=0, column=2, padx=(4, 8), pady=4)

    def _open_file(self, target: Path) -> None:
        if target.exists() and sys.platform == "win32":
            os.startfile(str(target))

    def _open_image_modal(self, img_path: Path) -> None:
        if not img_path.is_file():
            return
        modal = customtkinter.CTkToplevel(self)
        modal.title(img_path.name)
        modal.geometry("860x640")
        modal.configure(fg_color=BG_MAIN)
        modal.attributes("-topmost", True)

        bar = create_card(modal, fg_color=BG_SURFACE, corner_radius=8)
        bar.pack(fill="x", padx=12, pady=(12, 6))
        customtkinter.CTkLabel(bar, text=str(img_path), font=font_mono(11), text_color=TEXT_SECONDARY).pack(
            side="left", padx=12, pady=8
        )
        create_button(
            bar,
            text="Копировать путь",
            variant="primary",
            height=28,
            command=lambda: self._copy_path_notify(str(img_path)),
        ).pack(side="right", padx=12, pady=6)

        pil_img = Image.open(img_path)
        w, h = pil_img.size
        ratio = min(800 / max(1, w), 520 / max(1, h), 1.0)
        ctk_img = customtkinter.CTkImage(
            light_image=pil_img,
            dark_image=pil_img,
            size=(int(w * ratio), int(h * ratio)),
        )
        lbl = customtkinter.CTkLabel(modal, image=ctk_img, text="")
        lbl._modal_ref = ctk_img
        lbl.pack(expand=True, padx=12, pady=12)

    def _copy_path_notify(self, path_str: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(path_str)
        self.controller.show_notification("Путь скопирован", path_str)

    def on_open_order_folder(self) -> None:
        if self.current_detail and sys.platform == "win32":
            os.startfile(str(self.current_detail.order_dir))

    def on_save_inspector_meta(self) -> None:
        if not self.current_detail:
            return
        cfg_val = self.ws_config_entry.get().strip()
        kind_val = self.ws_kind_menu.get().strip()
        self.controller.orders_facade.update_order(
            self.current_detail.order_id,
            configuration=cfg_val,
            kind=kind_val,
        )
        prepare_antigravity_workspace(self.current_detail.order_dir, base_dir=self.controller.base_dir)
        self.load_order(self.current_detail.order_id)
        self.controller.show_notification("Сохранено", "Конфигурация, тип и ORDER_CONTEXT.md обновлены")

    def on_open_antigravity_workspace(self) -> None:
        if not self.current_detail:
            return
        order_dir = self.current_detail.order_dir

        def worker() -> dict[str, Any]:
            return launch_antigravity_workspace(
                order_dir,
                base_dir=self.controller.base_dir,
                system_doctor=self.controller.system_doctor,
                onec_runner=self.controller.onec_runner,
            )

        def done(res: dict[str, Any]) -> None:
            warnings = res.get("warnings") or []
            ide = res.get("ide_used") or "Workspace"
            msg = f"Открыто в {ide}.\nGuard :8766: {'OK' if res.get('guard_online') else 'Офлайн'}\nEDT :8765: {'OK' if res.get('edt_online') else 'Офлайн'}"
            if warnings:
                msg += "\n\nЗамечания:\n" + "\n".join(warnings)
            self.controller.show_notification("Antigravity Workspace", msg, error=not res.get("ok"))

        self.controller.run_in_background(worker, on_done=done)

    def on_start_work_1click(self) -> None:
        if not self.current_detail:
            return
        order_id = self.current_detail.order_id
        order_dir = self.current_detail.order_dir

        def worker() -> dict[str, Any]:
            res = self.controller.orders_facade.start_order_work(order_id)
            prepare_antigravity_workspace(order_dir, base_dir=self.controller.base_dir)
            if res.get("ok"):
                self.controller.onec_runner.open_in_vscode(order_dir)
            return res

        def done(res: dict[str, Any]) -> None:
            if res.get("ok"):
                prompt = str(res.get("prompt") or "")
                if prompt:
                    self.clipboard_clear()
                    self.clipboard_append(prompt)
                self.load_order(order_id)
                self.controller.show_notification(
                    "Взято в работу",
                    f"Шаблон '{res.get('template')}' ({res.get('copied_files', 0)} файлов) подготовлен.",
                )
            else:
                self.controller.show_notification("Ошибка старта", str(res.get("error") or "Неизвестная ошибка"), error=True)

        self.controller.run_in_background(worker, on_done=done)

    def _fill_tab_overview(self, detail: Any) -> None:
        self.stage_menu.set(detail.stage)
        self.hours_label.configure(text=f"{detail.hours_spent:.1f} ч")
        self.ov_title_entry.delete(0, "end")
        self.ov_title_entry.insert(0, detail.title)
        self.ov_price_entry.delete(0, "end")
        self.ov_price_entry.insert(0, str(int(detail.price)))
        self.ov_deadline_entry.delete(0, "end")
        self.ov_deadline_entry.insert(0, detail.deadline or "")
        self.ov_config_entry.delete(0, "end")
        self.ov_config_entry.insert(0, detail.configuration or "")
        self.ov_notes_box.delete("1.0", "end")
        self.ov_notes_box.insert("1.0", detail.notes or "")

    def on_change_stage(self, new_stage: str) -> None:
        if not self.current_order_id:
            return
        try:
            self.controller.orders_facade.advance_stage(self.current_order_id, new_stage)
            self.controller.top_bar.refresh_status()
            self.controller.show_notification("Статус изменен", f"Заказ переведен в '{new_stage}'")
        except Exception as error:
            LOGGER.error("Advance stage error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка смены статуса", str(error), error=True)

    def on_add_hours(self, delta: float) -> None:
        if not self.current_order_id:
            return
        try:
            self.controller.orders_facade.add_hours(self.current_order_id, delta)
            updated = self.controller.orders_facade.get_order(self.current_order_id)
            if updated:
                self.hours_label.configure(text=f"{updated.hours_spent:.1f} ч")
            self.controller.show_notification("Время учтено", f"Добавлено {delta} ч")
        except Exception as error:
            LOGGER.error("Add hours error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка учета времени", str(error), error=True)

    def on_save_overview(self) -> None:
        if not self.current_order_id:
            return
        try:
            try:
                price_val = float(self.ov_price_entry.get().strip().replace(" ", "") or "0")
            except ValueError:
                price_val = 0.0

            self.controller.orders_facade.update_order(
                self.current_order_id,
                title=self.ov_title_entry.get().strip(),
                price=price_val,
                deadline=self.ov_deadline_entry.get().strip(),
                configuration=self.ov_config_entry.get().strip(),
                notes=self.ov_notes_box.get("1.0", "end").strip(),
            )
            self.load_order(self.current_order_id)
            self.controller.show_notification("Сохранено", "Параметры заказа обновлены")
        except Exception as error:
            LOGGER.error("Save overview error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка сохранения", str(error), error=True)

    def on_dev_open_vscode(self) -> None:
        if not self.current_detail:
            return
        target_dir = self.current_detail.order_dir

        def worker() -> tuple[bool, str]:
            return self.controller.onec_runner.open_in_vscode(target_dir)

        def done(res: tuple[bool, str]) -> None:
            ok, msg = res
            if not ok:
                self.controller.show_notification("VS Code", msg, error=True)

        self.controller.run_in_background(worker, on_done=done)

    def on_dev_copy_prompt(self) -> None:
        if not self.current_detail:
            return
        try:
            prompt = self.controller.prompt_builder.build_roo_starter_prompt(self.current_detail.order_dir)
            self.clipboard_clear()
            self.clipboard_append(prompt)
            self.controller.show_notification("Промпт скопирован", "Стартовый промпт помещен в буфер обмена")
        except Exception as error:
            LOGGER.error("Copy prompt error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка", str(error), error=True)

    def on_dev_launch_1c(self) -> None:
        if not self.current_detail:
            return
        order_dir = self.current_detail.order_dir
        candidates = (
            list(order_dir.glob("*.epf"))
            + list(order_dir.glob("*.erf"))
            + list(order_dir.glob("delivery/*.epf"))
            + list(order_dir.glob("delivery/*.erf"))
            + list(order_dir.glob("attachments/1c_files/*.epf"))
            + list(order_dir.glob("attachments/1c_files/*.erf"))
        )
        if not candidates:
            self.controller.show_notification(
                "1С Тонкий клиент",
                "В каталоге заказа еще нет .epf или .erf файлов",
                error=True,
            )
            return
        target_file = candidates[0]
        ib_path = str(load_config().get("onec", {}).get("test_ib_path") or "")

        def worker() -> tuple[bool, str]:
            return self.controller.onec_runner.launch_epf_in_thin_client(ib_path, target_file)

        def done(res: tuple[bool, str]) -> None:
            ok, msg = res
            self.controller.show_notification("Запуск 1С", msg, error=not ok)

        self.controller.run_in_background(worker, on_done=done)

    def on_save_snippet_to_library(self) -> None:
        code = self.bsl_editor.get("1.0", "end").strip()
        if not code:
            self.controller.show_notification("Library", "Введите код сниппета в редактор", error=True)
            return
        raw_id = self.snippet_id_entry.get().strip() or (f"{self.current_order_id}_snippet" if self.current_order_id else "custom_snippet")
        stype = self.snippet_type_menu.get()
        title = f"{raw_id} (из {self.current_order_id or 'Cockpit'})"
        lib_dir = self.controller.base_dir / "library"
        ok, msg = save_snippet(
            snippet_id=raw_id,
            title=title,
            code=code,
            snippet_type=stype,
            tags=[stype, self.current_detail.kind if self.current_detail else "custom"],
            library_dir=lib_dir,
        )
        self.controller.show_notification("Library", msg, error=not ok)

    def on_dev_run_linter(self) -> None:
        code = self.bsl_editor.get("1.0", "end")
        try:
            issues = lint_bsl_code(code)
            formatted = format_diagnostics_for_llm(issues, include_warnings=True)
            self.linter_results.configure(state="normal")
            self.linter_results.delete("1.0", "end")
            if not formatted:
                self.linter_results.insert("1.0", "✅ Ошибок не обнаружено. Код BSL корректен.")
            else:
                self.linter_results.insert("1.0", formatted)
            self.linter_results.configure(state="disabled")
        except Exception as error:
            LOGGER.error("Lint BSL error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка линтера", str(error), error=True)

    def _fill_tab_brief(self, detail: Any) -> None:
        brief_path = detail.order_dir / "brief.md"
        content = ""
        if brief_path.exists():
            try:
                content = brief_path.read_text(encoding="utf-8")
            except Exception as error:
                LOGGER.error("Failed to read brief.md: %s", error)
        self.brief_editor.delete("1.0", "end")
        self.brief_editor.insert("1.0", content)

    def on_save_brief(self) -> None:
        if not self.current_detail:
            return
        brief_path = self.current_detail.order_dir / "brief.md"
        try:
            content = self.brief_editor.get("1.0", "end")
            brief_path.write_text(content, encoding="utf-8")
            prepare_antigravity_workspace(self.current_detail.order_dir, base_dir=self.controller.base_dir)
            self.controller.show_notification("ТЗ сохранено", f"Файл {brief_path.name} успешно обновлен")
        except Exception as error:
            LOGGER.error("Save brief error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка сохранения", str(error), error=True)

    def on_validate_brief(self) -> None:
        content = self.brief_editor.get("1.0", "end").strip()
        if len(content) < 50:
            self.controller.show_notification("Проверка ТЗ", "ТЗ слишком короткое или пустое", error=True)
            return

        missing = []
        if "конфигурация" not in content.lower():
            missing.append("конфигурация 1С")
        if "результат" not in content.lower() and "выход" not in content.lower():
            missing.append("ожидаемый результат")

        if missing:
            msg = f"В ТЗ не найдены разделы: {', '.join(missing)}"
            self.controller.show_notification("Замечания к ТЗ", msg, error=True)
        else:
            self.controller.show_notification("Проверка ТЗ", "✅ Все ключевые разделы ТЗ присутствуют")

    def _fill_tab_budget(self, detail: Any) -> None:
        try:
            budget = self.controller.orders_facade.get_budget(detail.order_id)
            self.b_calls_lbl.configure(text=f"Вызовы MCP\n{budget.call_count} / {budget.max_calls}")
            if budget.is_exceeded:
                self.b_status_lbl.configure(text="Лимит\nПревышен!", text_color=DANGER_FG)
            elif budget.is_warning:
                self.b_status_lbl.configure(text="Лимит\nВнимание (>20)", text_color=WARNING_FG)
            else:
                self.b_status_lbl.configure(text="Лимит\nВ норме", text_color=SUCCESS_FG)

            self.b_tokens_lbl.configure(text=f"Токены LLM\n{budget.total_tokens:,}".replace(",", " "))
            if budget.virtual_cost_rub > 0 and budget.real_cost_rub == 0:
                cost_str = f"Затраты AI\n0.00 ₽ (подписка ~{budget.virtual_cost_rub:.1f} ₽)"
            else:
                cost_str = f"Затраты AI\n{budget.real_cost_rub:.2f} ₽"
            self.b_cost_lbl.configure(text=cost_str)
            self.b_margin_lbl.configure(text=f"Чистая маржа\n{budget.net_margin_rub:,.0f} ₽".replace(",", " "))

            metrics_file = detail.order_dir / "guard_metrics.json"
            if metrics_file.is_file():
                gm = json.loads(metrics_file.read_text(encoding="utf-8"))
                self.b_guard_metrics_lbl.configure(
                    text=f"Блокировок линтера: {gm.get('linter_blocks', 0)}  ·  Сжатых схем: {gm.get('schemas_compacted', 0)}"
                )
            else:
                self.b_guard_metrics_lbl.configure(text="")

            if budget.economy_mode or budget.is_api_over_threshold:
                self.b_economy_lbl.configure(text="⚠ Эконом-режим (>5% бюджета на API)")
            else:
                self.b_economy_lbl.configure(text="")

            events = self.controller.orders_facade.get_order_mcp_events(detail.order_id)
            self.mcp_log_box.configure(state="normal")
            self.mcp_log_box.delete("1.0", "end")
            if not events:
                self.mcp_log_box.insert("1.0", "MCP-вызовов по данному заказу пока не зафиксировано.")
            else:
                lines = []
                for ev in events[-50:]:
                    ts = ev.get("timestamp", "")
                    tool = ev.get("tool", "")
                    status = ev.get("status", "")
                    dur = ev.get("duration_ms", 0)
                    lines.append(f"[{ts}] {tool} -> {status} ({dur}ms)")
                self.mcp_log_box.insert("1.0", "\n".join(lines))
            self.mcp_log_box.configure(state="disabled")
        except Exception as error:
            LOGGER.error("Failed to load budget tab: %s", error, exc_info=True)

    def on_budget_extend(self) -> None:
        if not self.current_order_id:
            return
        try:
            _, msg = self.controller.orders_facade.extend_budget(self.current_order_id, 10)
            self._fill_tab_budget(self.current_detail)
            self.controller.show_notification("Бюджет расширен", msg)
        except Exception as error:
            LOGGER.error("Budget extend error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка", str(error), error=True)

    def on_budget_reset(self) -> None:
        if not self.current_order_id:
            return
        try:
            _, msg = self.controller.orders_facade.reset_budget(self.current_order_id)
            self._fill_tab_budget(self.current_detail)
            self.controller.show_notification("Бюджет сброшен", msg)
        except Exception as error:
            LOGGER.error("Budget reset error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка", str(error), error=True)

    def _fill_tab_delivery(self, detail: Any) -> None:
        chk_data = detail.checklist or {}
        for key, var in self.checklist_vars.items():
            var.set(chk_data.get(key, False))

    def on_toggle_checklist(self) -> None:
        if not self.current_order_id:
            return
        payload = {k: v.get() for k, v in self.checklist_vars.items()}
        try:
            self.controller.orders_facade.save_checklist(self.current_order_id, payload)
        except Exception as error:
            LOGGER.error("Failed to save checklist: %s", error)

    def on_deliver_order(self) -> None:
        if not self.current_detail:
            return
        order_dir = self.current_detail.order_dir
        order_id = self.current_detail.order_id

        def worker() -> dict[str, Any]:
            res = self.controller.packager.compile_and_package_order(
                order_dir,
                onec_runner=self.controller.onec_runner,
            )
            self.controller.orders_facade.advance_stage(order_id, "delivered")
            self.controller.packager.reveal_in_explorer(res["zip_path"])
            return res

        def done(res: dict[str, Any]) -> None:
            self.stage_menu.set("delivered")
            kwork_msg = str(res.get("kwork_message") or "")
            if kwork_msg:
                self.clipboard_clear()
                self.clipboard_append(kwork_msg)
            zip_file = res.get("zip_path")
            tpl_errs = res.get("template_errors") or []
            if tpl_errs:
                self.controller.show_notification("Предупреждение по шаблонам", "\n".join(tpl_errs), error=True)
            else:
                self.controller.show_notification(
                    "Заказ сдан!",
                    f"Архив собран: {zip_file}\nСообщение для Kwork скопировано в буфер.",
                )

        def on_err(error: Exception) -> None:
            self.controller.show_notification("Ошибка сдачи заказа", str(error), error=True)

        self.controller.run_in_background(worker, on_done=done, on_error=on_err)

    def _fill_tab_warranty(self, detail: Any) -> None:
        dec_path = detail.order_dir / "DECISIONS.md"
        content = ""
        if dec_path.exists():
            try:
                content = dec_path.read_text(encoding="utf-8")
            except Exception as error:
                LOGGER.error("Failed to read DECISIONS.md: %s", error)
        self.decisions_editor.delete("1.0", "end")
        self.decisions_editor.insert("1.0", content)

    def on_create_warranty_branch(self) -> None:
        if not self.current_detail:
            return
        try:
            self.controller.orders_facade.advance_stage(self.current_detail.order_id, "warranty")
            self.stage_menu.set("warranty")
            self.controller.show_notification(
                "Гарантия",
                f"Заказ {self.current_detail.order_id} переведен на 30-дневную гарантию",
            )
        except Exception as error:
            LOGGER.error("Warranty branch error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка", str(error), error=True)

    def on_save_decisions(self) -> None:
        if not self.current_detail:
            return
        dec_path = self.current_detail.order_dir / "DECISIONS.md"
        try:
            content = self.decisions_editor.get("1.0", "end")
            dec_path.write_text(content, encoding="utf-8")
            self.controller.show_notification("DECISIONS.md", "Журнал решений успешно сохранен")
        except Exception as error:
            LOGGER.error("Save decisions error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка сохранения", str(error), error=True)

    def destroy(self) -> None:
        self._tick_active = False
        if self._tick_after_id is not None:
            try:
                self.after_cancel(self._tick_after_id)
            except Exception:
                self._tick_after_id = None
        super().destroy()
