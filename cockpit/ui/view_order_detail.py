from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import customtkinter

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
        self.current_order_id: str | None = None
        self.current_detail: Any = None

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.header_frame = customtkinter.CTkFrame(self, fg_color="#1E293B", corner_radius=0)
        self.header_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        self.header_frame.grid_columnconfigure(1, weight=1)

        self.back_btn = customtkinter.CTkButton(
            self.header_frame,
            text="← Назад",
            width=70,
            fg_color="#334155",
            hover_color="#475569",
            command=lambda: self.controller.show_view("orders"),
        )
        self.back_btn.grid(row=0, column=0, padx=(16, 10), pady=12)

        self.order_heading = customtkinter.CTkLabel(
            self.header_frame,
            text="Заказ: —",
            font=customtkinter.CTkFont(size=16, weight="bold"),
        )
        self.order_heading.grid(row=0, column=1, sticky="w", padx=6, pady=12)

        self.start_work_btn = customtkinter.CTkButton(
            self.header_frame,
            text="🚀 В работу (1-Click Start)",
            fg_color="#059669",
            hover_color="#047857",
            font=customtkinter.CTkFont(weight="bold"),
            command=self.on_start_work_1click,
        )
        self.start_work_btn.grid(row=0, column=2, padx=(6, 16), pady=12)

        self.tabview = customtkinter.CTkTabview(self)
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=20, pady=10)

        self.tab_overview = self.tabview.add("Обзор")
        self.tab_dev = self.tabview.add("Разработка")
        self.tab_brief = self.tabview.add("ТЗ и Бриф")
        self.tab_budget = self.tabview.add("Бюджет и Логи")
        self.tab_delivery = self.tabview.add("Сдача в 1 клик")
        self.tab_warranty = self.tabview.add("Гарантия и Git")

        self._build_tab_overview()
        self._build_tab_dev()
        self._build_tab_brief()
        self._build_tab_budget()
        self._build_tab_delivery()
        self._build_tab_warranty()

    def load_order(self, order_id: str) -> None:
        self.current_order_id = order_id
        try:
            detail = self.controller.orders_facade.get_order(order_id)
            if not detail:
                self.controller.show_notification("Ошибка", f"Заказ {order_id} не найден", error=True)
                return

            self.current_detail = detail
            self.order_heading.configure(
                text=f"{detail.order_id}  |  Клиент: {detail.client}  |  {detail.title}"
            )
            self._fill_tab_overview(detail)
            self._fill_tab_brief(detail)
            self._fill_tab_budget(detail)
            self._fill_tab_delivery(detail)
            self._fill_tab_warranty(detail)
        except Exception as error:
            LOGGER.error("Failed to load order %s: %s", order_id, error, exc_info=True)
            self.controller.show_notification("Ошибка загрузки", str(error), error=True)

    def on_start_work_1click(self) -> None:
        if not self.current_order_id:
            return
        target_id = self.current_order_id

        def worker() -> dict[str, Any]:
            res = self.controller.orders_facade.start_order_work(target_id)
            if res.get("ok"):
                order_dir = Path(res["order_dir"])
                self.controller.onec_runner.open_in_vscode(order_dir)
            return res

        def done(res: dict[str, Any]) -> None:
            if not res.get("ok"):
                self.controller.show_notification("Ошибка старта", str(res.get("error", "Не удалось запустить")), error=True)
                return
            prompt = str(res.get("prompt") or "")
            if prompt:
                self.clipboard_clear()
                self.clipboard_append(prompt)
            self.stage_menu.set("in_progress")
            self.controller.show_notification(
                "Заказ взят в работу",
                f"Шаблон: {res.get('template')} (файлов: {res.get('copied_files')})\n"
                f"Профиль MCP: {res.get('mcp_profile')}\n"
                f"Промпт скопирован в буфер, папка открыта в IDE.",
            )

        self.controller.run_in_background(worker, on_done=done)

    def _build_tab_overview(self) -> None:
        self.tab_overview.grid_columnconfigure((0, 1), weight=1)

        lbl_stage = customtkinter.CTkLabel(self.tab_overview, text="Текущий этап:")
        lbl_stage.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 2))
        self.stage_menu = customtkinter.CTkOptionMenu(
            self.tab_overview,
            values=STAGE_OPTIONS,
            command=self.on_change_stage,
        )
        self.stage_menu.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))

        lbl_hours = customtkinter.CTkLabel(self.tab_overview, text="Учет времени (ч):")
        lbl_hours.grid(row=0, column=1, sticky="w", padx=10, pady=(10, 2))

        hours_box = customtkinter.CTkFrame(self.tab_overview, fg_color="transparent")
        hours_box.grid(row=1, column=1, sticky="ew", padx=10, pady=(0, 10))
        self.hours_label = customtkinter.CTkLabel(
            hours_box,
            text="0.0 ч",
            font=customtkinter.CTkFont(size=14, weight="bold"),
        )
        self.hours_label.pack(side="left", padx=(0, 10))

        btn_add_half = customtkinter.CTkButton(
            hours_box,
            text="+0.5 ч",
            width=60,
            command=lambda: self.on_add_hours(0.5),
        )
        btn_add_half.pack(side="left", padx=3)

        btn_add_one = customtkinter.CTkButton(
            hours_box,
            text="+1.0 ч",
            width=60,
            command=lambda: self.on_add_hours(1.0),
        )
        btn_add_one.pack(side="left", padx=3)

        lbl_title = customtkinter.CTkLabel(self.tab_overview, text="Название:")
        lbl_title.grid(row=2, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 2))
        self.ov_title_entry = customtkinter.CTkEntry(self.tab_overview)
        self.ov_title_entry.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 8))

        lbl_price = customtkinter.CTkLabel(self.tab_overview, text="Стоимость (₽):")
        lbl_price.grid(row=4, column=0, sticky="w", padx=10, pady=(6, 2))
        self.ov_price_entry = customtkinter.CTkEntry(self.tab_overview)
        self.ov_price_entry.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 8))

        lbl_deadline = customtkinter.CTkLabel(self.tab_overview, text="Дедлайн:")
        lbl_deadline.grid(row=4, column=1, sticky="w", padx=10, pady=(6, 2))
        self.ov_deadline_entry = customtkinter.CTkEntry(self.tab_overview)
        self.ov_deadline_entry.grid(row=5, column=1, sticky="ew", padx=10, pady=(0, 8))

        lbl_config = customtkinter.CTkLabel(self.tab_overview, text="Конфигурация:")
        lbl_config.grid(row=6, column=0, sticky="w", padx=10, pady=(6, 2))
        self.ov_config_entry = customtkinter.CTkEntry(self.tab_overview)
        self.ov_config_entry.grid(row=7, column=0, sticky="ew", padx=10, pady=(0, 8))

        lbl_notes = customtkinter.CTkLabel(self.tab_overview, text="Заметки / Требования клиента:")
        lbl_notes.grid(row=8, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 2))
        self.ov_notes_box = customtkinter.CTkTextbox(self.tab_overview, height=100)
        self.ov_notes_box.grid(row=9, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 12))

        self.save_overview_btn = customtkinter.CTkButton(
            self.tab_overview,
            text="💾 Сохранить изменения",
            fg_color="#059669",
            hover_color="#047857",
            command=self.on_save_overview,
        )
        self.save_overview_btn.grid(row=10, column=0, columnspan=2, sticky="e", padx=10, pady=8)

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
            self.controller.show_notification("Сохранено", "Параметры заказа обновлены")
        except Exception as error:
            LOGGER.error("Save overview error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка сохранения", str(error), error=True)

    def _build_tab_dev(self) -> None:
        self.tab_dev.grid_columnconfigure(0, weight=1)

        actions_bar = customtkinter.CTkFrame(self.tab_dev, fg_color="transparent")
        actions_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=8)

        btn_vscode = customtkinter.CTkButton(
            actions_bar,
            text="🚀 Открыть в VS Code",
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            command=self.on_dev_open_vscode,
        )
        btn_vscode.pack(side="left", padx=4)

        btn_prompt = customtkinter.CTkButton(
            actions_bar,
            text="📋 Скопировать промпт",
            fg_color="#0D9488",
            hover_color="#0F766E",
            command=self.on_dev_copy_prompt,
        )
        btn_prompt.pack(side="left", padx=4)

        btn_test_1c = customtkinter.CTkButton(
            actions_bar,
            text="▶ Тест в тонком клиенте 1С",
            fg_color="#D97706",
            hover_color="#B45309",
            command=self.on_dev_launch_1c,
        )
        btn_test_1c.pack(side="left", padx=4)

        linter_lbl = customtkinter.CTkLabel(
            self.tab_dev,
            text="Синтаксический контроль BSL / сохранение сниппета в Library:",
            font=customtkinter.CTkFont(size=13, weight="bold"),
        )
        linter_lbl.grid(row=1, column=0, sticky="w", padx=10, pady=(10, 4))

        self.bsl_editor = customtkinter.CTkTextbox(self.tab_dev, height=165, font=customtkinter.CTkFont(family="Consolas", size=12))
        self.bsl_editor.grid(row=2, column=0, sticky="ew", padx=10, pady=4)

        linter_actions = customtkinter.CTkFrame(self.tab_dev, fg_color="transparent")
        linter_actions.grid(row=3, column=0, sticky="ew", padx=10, pady=4)

        btn_lint = customtkinter.CTkButton(
            linter_actions,
            text="🔍 Проверить BSL-код",
            command=self.on_dev_run_linter,
        )
        btn_lint.pack(side="left", padx=(0, 8))

        self.snippet_id_entry = customtkinter.CTkEntry(
            linter_actions,
            placeholder_text="ID сниппета (напр. custom_calc)",
            width=190,
        )
        self.snippet_id_entry.pack(side="left", padx=4)

        self.snippet_type_menu = customtkinter.CTkOptionMenu(
            linter_actions,
            values=["bsl", "query"],
            width=85,
        )
        self.snippet_type_menu.pack(side="left", padx=4)

        self.btn_save_snippet = customtkinter.CTkButton(
            linter_actions,
            text="💾 Сохранить сниппет в Library",
            fg_color="#0D9488",
            hover_color="#0F766E",
            command=self.on_save_snippet_to_library,
        )
        self.btn_save_snippet.pack(side="left", padx=4)

        self.linter_results = customtkinter.CTkTextbox(self.tab_dev, height=120, state="disabled")
        self.linter_results.grid(row=4, column=0, sticky="ew", padx=10, pady=6)

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
        candidates = list(order_dir.glob("*.epf")) + list(order_dir.glob("*.erf")) + list(order_dir.glob("delivery/*.epf")) + list(order_dir.glob("delivery/*.erf"))
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

    def _build_tab_brief(self) -> None:
        self.tab_brief.grid_columnconfigure(0, weight=1)

        btn_row = customtkinter.CTkFrame(self.tab_brief, fg_color="transparent")
        btn_row.grid(row=0, column=0, sticky="ew", padx=10, pady=8)

        btn_save_brief = customtkinter.CTkButton(
            btn_row,
            text="💾 Сохранить ТЗ",
            fg_color="#059669",
            hover_color="#047857",
            command=self.on_save_brief,
        )
        btn_save_brief.pack(side="left", padx=4)

        btn_check_brief = customtkinter.CTkButton(
            btn_row,
            text="🔍 Проверить полноту ТЗ",
            command=self.on_validate_brief,
        )
        btn_check_brief.pack(side="left", padx=4)

        self.brief_editor = customtkinter.CTkTextbox(self.tab_brief, height=350, font=customtkinter.CTkFont(size=12))
        self.brief_editor.grid(row=1, column=0, sticky="nsew", padx=10, pady=6)

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

    def _build_tab_budget(self) -> None:
        self.tab_budget.grid_columnconfigure(0, weight=1)

        stats_card = customtkinter.CTkFrame(self.tab_budget, fg_color="#1E293B", corner_radius=8)
        stats_card.grid(row=0, column=0, sticky="ew", padx=10, pady=8)
        stats_card.grid_columnconfigure((0, 1, 2, 3, 4), weight=1)

        self.b_calls_lbl = customtkinter.CTkLabel(stats_card, text="Вызовы MCP\n0 / 25", font=customtkinter.CTkFont(size=13, weight="bold"))
        self.b_calls_lbl.grid(row=0, column=0, padx=8, pady=10)

        self.b_status_lbl = customtkinter.CTkLabel(stats_card, text="Лимит\nВ норме", font=customtkinter.CTkFont(size=13, weight="bold"), text_color="#10B981")
        self.b_status_lbl.grid(row=0, column=1, padx=8, pady=10)

        self.b_tokens_lbl = customtkinter.CTkLabel(stats_card, text="Токены LLM\n0", font=customtkinter.CTkFont(size=13, weight="bold"))
        self.b_tokens_lbl.grid(row=0, column=2, padx=8, pady=10)

        self.b_cost_lbl = customtkinter.CTkLabel(stats_card, text="Затраты AI\n0.00 ₽", font=customtkinter.CTkFont(size=13, weight="bold"), text_color="#38BDF8")
        self.b_cost_lbl.grid(row=0, column=3, padx=8, pady=10)

        self.b_margin_lbl = customtkinter.CTkLabel(stats_card, text="Чистая маржа\n0 ₽", font=customtkinter.CTkFont(size=13, weight="bold"), text_color="#10B981")
        self.b_margin_lbl.grid(row=0, column=4, padx=8, pady=10)

        actions_bar = customtkinter.CTkFrame(self.tab_budget, fg_color="transparent")
        actions_bar.grid(row=1, column=0, sticky="ew", padx=10, pady=6)

        btn_extend = customtkinter.CTkButton(
            actions_bar,
            text="+10 вызовов",
            fg_color="#0D9488",
            hover_color="#0F766E",
            command=self.on_budget_extend,
        )
        btn_extend.pack(side="left", padx=4)

        btn_reset = customtkinter.CTkButton(
            actions_bar,
            text="Сбросить лимит",
            fg_color="#475569",
            hover_color="#334155",
            command=self.on_budget_reset,
        )
        btn_reset.pack(side="left", padx=4)

        self.b_economy_lbl = customtkinter.CTkLabel(
            actions_bar,
            text="",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            text_color="#F59E0B",
        )
        self.b_economy_lbl.pack(side="right", padx=8)

        lbl_log = customtkinter.CTkLabel(self.tab_budget, text="События MCP-вызовов по заказу:", font=customtkinter.CTkFont(weight="bold"))
        lbl_log.grid(row=2, column=0, sticky="w", padx=10, pady=(10, 4))

        self.mcp_log_box = customtkinter.CTkTextbox(self.tab_budget, height=220, state="disabled")
        self.mcp_log_box.grid(row=3, column=0, sticky="nsew", padx=10, pady=4)

    def _fill_tab_budget(self, detail: Any) -> None:
        try:
            budget = self.controller.orders_facade.get_budget(detail.order_id)
            self.b_calls_lbl.configure(text=f"Вызовы MCP\n{budget.call_count} / {budget.max_calls}")
            if budget.is_exceeded:
                self.b_status_lbl.configure(text="Лимит\nПревышен!", text_color="#EF4444")
            elif budget.is_warning:
                self.b_status_lbl.configure(text="Лимит\nВнимание (>20)", text_color="#F59E0B")
            else:
                self.b_status_lbl.configure(text="Лимит\nВ норме", text_color="#10B981")

            self.b_tokens_lbl.configure(text=f"Токены LLM\n{budget.total_tokens:,}".replace(",", " "))
            if budget.virtual_cost_rub > 0 and budget.real_cost_rub == 0:
                cost_str = f"Затраты AI\n0.00 ₽ (подписка ~{budget.virtual_cost_rub:.1f} ₽)"
            else:
                cost_str = f"Затраты AI\n{budget.real_cost_rub:.2f} ₽"
            self.b_cost_lbl.configure(text=cost_str)
            self.b_margin_lbl.configure(text=f"Чистая маржа\n{budget.net_margin_rub:,.0f} ₽".replace(",", " "))

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

    def _build_tab_delivery(self) -> None:
        self.tab_delivery.grid_columnconfigure(0, weight=1)

        lbl = customtkinter.CTkLabel(
            self.tab_delivery,
            text="Чек-лист готовности заказа к сдаче:",
            font=customtkinter.CTkFont(size=14, weight="bold"),
        )
        lbl.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 6))

        self.checklist_vars: dict[str, customtkinter.BooleanVar] = {}
        for idx, (key, label_text) in enumerate(CHECKLIST_LABELS.items()):
            var = customtkinter.BooleanVar(value=False)
            self.checklist_vars[key] = var
            chk = customtkinter.CTkCheckBox(
                self.tab_delivery,
                text=label_text,
                variable=var,
                command=self.on_toggle_checklist,
            )
            chk.grid(row=idx + 1, column=0, sticky="w", padx=14, pady=4)

        deliver_action_frame = customtkinter.CTkFrame(self.tab_delivery, fg_color="transparent")
        deliver_action_frame.grid(row=len(CHECKLIST_LABELS) + 1, column=0, sticky="ew", padx=10, pady=20)

        self.btn_deliver_zip = customtkinter.CTkButton(
            deliver_action_frame,
            text="📦 Собрать поставку (Verify + ZIP + Kwork)",
            height=42,
            fg_color="#059669",
            hover_color="#047857",
            font=customtkinter.CTkFont(size=14, weight="bold"),
            command=self.on_deliver_order,
        )
        self.btn_deliver_zip.pack(side="left", padx=4)

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

    def _build_tab_warranty(self) -> None:
        self.tab_warranty.grid_columnconfigure(0, weight=1)

        btn_bar = customtkinter.CTkFrame(self.tab_warranty, fg_color="transparent")
        btn_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=8)

        btn_branch = customtkinter.CTkButton(
            btn_bar,
            text="🌿 Создать гарантийную ветку Git",
            fg_color="#0891B2",
            hover_color="#0E7490",
            command=self.on_create_warranty_branch,
        )
        btn_branch.pack(side="left", padx=4)

        btn_save_dec = customtkinter.CTkButton(
            btn_bar,
            text="💾 Сохранить DECISIONS.md",
            command=self.on_save_decisions,
        )
        btn_save_dec.pack(side="left", padx=4)

        lbl = customtkinter.CTkLabel(
            self.tab_warranty,
            text="Журнал архитектурных решений заказа (DECISIONS.md):",
            font=customtkinter.CTkFont(weight="bold"),
        )
        lbl.grid(row=1, column=0, sticky="w", padx=10, pady=(8, 4))

        self.decisions_editor = customtkinter.CTkTextbox(self.tab_warranty, height=300)
        self.decisions_editor.grid(row=2, column=0, sticky="nsew", padx=10, pady=4)

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
