from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import customtkinter

from guard.dual_backend import read_env_api_key

LOGGER = logging.getLogger("cockpit.view_new_order")
if not LOGGER.handlers:
    _log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handler = RotatingFileHandler(_log_dir / "cockpit.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)

KIND_LABELS: dict[str, str] = {
    "ВПФ": "vpf",
    "Отчет СКД": "report",
    "Обработка": "processor",
    "Расширение": "ext",
    "ТЗ": "intake",
}
KIND_REVERSE: dict[str, str] = {
    "vpf": "ВПФ",
    "report": "Отчет СКД",
    "processor": "Обработка",
    "ext": "Расширение",
    "extension": "Расширение",
    "intake": "ТЗ",
    "tz": "ТЗ",
}


class ViewNewOrder(customtkinter.CTkScrollableFrame):
    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.controller = controller

        self.grid_columnconfigure(0, weight=1)

        self.title_label = customtkinter.CTkLabel(
            self,
            text="➕ Создание нового заказа",
            font=customtkinter.CTkFont(size=22, weight="bold"),
        )
        self.title_label.grid(row=0, column=0, sticky="w", padx=20, pady=(15, 10))

        self.intake_frame = customtkinter.CTkFrame(self, fg_color="#1E293B", corner_radius=8)
        self.intake_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=8)
        self.intake_frame.grid_columnconfigure(0, weight=1)

        self.intake_label = customtkinter.CTkLabel(
            self.intake_frame,
            text="Быстрый парсинг ТЗ из Kwork (вставьте переписку или отрывок):",
            font=customtkinter.CTkFont(size=14, weight="bold"),
        )
        self.intake_label.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 6))

        self.raw_text_box = customtkinter.CTkTextbox(self.intake_frame, height=110)
        self.raw_text_box.grid(row=1, column=0, sticky="ew", padx=14, pady=6)

        parse_btn_row = customtkinter.CTkFrame(self.intake_frame, fg_color="transparent")
        parse_btn_row.grid(row=2, column=0, sticky="w", padx=14, pady=(6, 12))

        self.parse_btn = customtkinter.CTkButton(
            parse_btn_row,
            text="⚡ Распознать параметры (Regex + Вопросы)",
            fg_color="#0D9488",
            hover_color="#0F766E",
            font=customtkinter.CTkFont(weight="bold"),
            command=self.on_parse_intake,
        )
        self.parse_btn.pack(side="left", padx=(0, 8))

        self.parse_ai_btn = customtkinter.CTkButton(
            parse_btn_row,
            text="🤖 Умный разбор через LLM",
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            command=self.on_parse_intake_llm,
        )
        self.parse_ai_btn.pack(side="left")

        self.form_frame = customtkinter.CTkFrame(self, fg_color="#1E293B", corner_radius=8)
        self.form_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=8)
        self.form_frame.grid_columnconfigure((0, 1), weight=1)

        self._build_form_fields()

        self.questions_frame = customtkinter.CTkFrame(self, fg_color="#1E293B", corner_radius=8)
        self.questions_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=8)
        self.questions_frame.grid_columnconfigure(0, weight=1)

        q_header = customtkinter.CTkFrame(self.questions_frame, fg_color="transparent")
        q_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(10, 4))
        q_header.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            q_header,
            text="❓ Уточняющие вопросы клиенту:",
            font=customtkinter.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        self.copy_questions_btn = customtkinter.CTkButton(
            q_header,
            text="📋 Скопировать вопросы для чата Kwork",
            fg_color="#0D9488",
            hover_color="#0F766E",
            command=self.on_copy_questions,
        )
        self.copy_questions_btn.grid(row=0, column=1, sticky="e")

        self.questions_box = customtkinter.CTkTextbox(self.questions_frame, height=110)
        self.questions_box.grid(row=1, column=0, sticky="ew", padx=14, pady=(4, 12))

        self.btn_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.btn_frame.grid(row=4, column=0, sticky="ew", padx=20, pady=15)
        self.btn_frame.grid_columnconfigure(0, weight=1)
        self.btn_frame.grid_columnconfigure(1, weight=1)

        self.create_btn = customtkinter.CTkButton(
            self.btn_frame,
            text="Создать заказ",
            height=40,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            font=customtkinter.CTkFont(size=14, weight="bold"),
            command=lambda: self.on_submit_create(open_vscode=False),
        )
        self.create_btn.grid(row=0, column=0, padx=6, sticky="ew")

        self.create_and_open_btn = customtkinter.CTkButton(
            self.btn_frame,
            text="🚀 Создать и открыть в VS Code",
            height=40,
            fg_color="#059669",
            hover_color="#047857",
            font=customtkinter.CTkFont(size=14, weight="bold"),
            command=lambda: self.on_submit_create(open_vscode=True),
        )
        self.create_and_open_btn.grid(row=0, column=1, padx=6, sticky="ew")

    def _build_form_fields(self) -> None:
        row = 0

        lbl_slug = customtkinter.CTkLabel(self.form_frame, text="Код заказа (slug):")
        lbl_slug.grid(row=row, column=0, sticky="w", padx=14, pady=(10, 2))
        self.slug_entry = customtkinter.CTkEntry(self.form_frame, placeholder_text="client_kind_title")
        self.slug_entry.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))

        lbl_client = customtkinter.CTkLabel(self.form_frame, text="Логин / Имя клиента:")
        lbl_client.grid(row=row, column=1, sticky="w", padx=14, pady=(10, 2))
        self.client_entry = customtkinter.CTkEntry(self.form_frame, placeholder_text="ivan_ivanov")
        self.client_entry.grid(row=row + 1, column=1, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        lbl_title = customtkinter.CTkLabel(self.form_frame, text="Краткое название задачи:")
        lbl_title.grid(row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(8, 2))
        self.title_entry = customtkinter.CTkEntry(self.form_frame, placeholder_text="Печатная форма счета с логотипом")
        self.title_entry.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        lbl_kind = customtkinter.CTkLabel(self.form_frame, text="Тип разработки:")
        lbl_kind.grid(row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(8, 2))
        self.kind_segmented = customtkinter.CTkSegmentedButton(
            self.form_frame,
            values=["ВПФ", "Отчет СКД", "Обработка", "Расширение", "ТЗ"],
        )
        self.kind_segmented.set("ВПФ")
        self.kind_segmented.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        lbl_price = customtkinter.CTkLabel(self.form_frame, text="Стоимость (₽):")
        lbl_price.grid(row=row, column=0, sticky="w", padx=14, pady=(8, 2))
        self.price_entry = customtkinter.CTkEntry(self.form_frame, placeholder_text="5000")
        self.price_entry.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))

        lbl_deadline = customtkinter.CTkLabel(self.form_frame, text="Срок / Дедлайн:")
        lbl_deadline.grid(row=row, column=1, sticky="w", padx=14, pady=(8, 2))
        self.deadline_entry = customtkinter.CTkEntry(self.form_frame, placeholder_text="3 дня / 2026-11-15")
        self.deadline_entry.grid(row=row + 1, column=1, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        lbl_config = customtkinter.CTkLabel(self.form_frame, text="Конфигурация 1С:")
        lbl_config.grid(row=row, column=0, sticky="w", padx=14, pady=(8, 2))
        self.config_entry = customtkinter.CTkEntry(self.form_frame, placeholder_text="1С:УНФ 3.0 / 1С:УТ 11.5 / БП 3.0")
        self.config_entry.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))

        lbl_object = customtkinter.CTkLabel(self.form_frame, text="Имя объекта 1С:")
        lbl_object.grid(row=row, column=1, sticky="w", padx=14, pady=(8, 2))
        self.object_name_entry = customtkinter.CTkEntry(self.form_frame, placeholder_text="СчетНаОплатуПокупателю")
        self.object_name_entry.grid(row=row + 1, column=1, sticky="ew", padx=14, pady=(0, 14))

    def _apply_parsed_result(self, result: dict[str, Any]) -> None:
        slug_val = str(result.get("order_id") or "")
        client_val = str(result.get("client_name") or "")
        title_val = str(result.get("title") or "")
        price_val = float(result.get("price_rub") or 0.0)
        deadline_val = str(result.get("deadline_days") or "")
        config_val = str(result.get("config_name") or "")
        object_val = str(result.get("object_name") or "")
        kind_val = str(result.get("kind") or "vpf")
        questions_txt = str(result.get("clarifying_questions_text") or "")

        if slug_val:
            self.slug_entry.delete(0, "end")
            self.slug_entry.insert(0, slug_val)
        if client_val:
            self.client_entry.delete(0, "end")
            self.client_entry.insert(0, client_val)
        if title_val:
            self.title_entry.delete(0, "end")
            self.title_entry.insert(0, title_val)
        if price_val > 0:
            self.price_entry.delete(0, "end")
            self.price_entry.insert(0, str(int(price_val)))
        if deadline_val:
            self.deadline_entry.delete(0, "end")
            self.deadline_entry.insert(0, f"{deadline_val} дн.")
        if config_val:
            self.config_entry.delete(0, "end")
            self.config_entry.insert(0, config_val)
        if object_val:
            self.object_name_entry.delete(0, "end")
            self.object_name_entry.insert(0, object_val)

        label_kind = KIND_REVERSE.get(kind_val, "ВПФ")
        self.kind_segmented.set(label_kind)

        self.questions_box.delete("1.0", "end")
        self.questions_box.insert("1.0", questions_txt)

    def on_parse_intake(self) -> None:
        raw_text = self.raw_text_box.get("1.0", "end").strip()
        if not raw_text:
            self.controller.show_notification("Интейк", "Введите текст переписки или ТЗ", error=True)
            return

        try:
            result = self.controller.intake_parser.parse_raw_kwork_text_regex(raw_text)
            self._apply_parsed_result(result)
            self.controller.show_notification(
                "ТЗ распознано",
                f"Извлечено: клиент '{result.get('client_name')}', конфигурация '{result.get('config_name')}'",
            )
        except Exception as error:
            LOGGER.error("Failed to parse intake text: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка распознавания", str(error), error=True)

    def on_parse_intake_llm(self) -> None:
        raw_text = self.raw_text_box.get("1.0", "end").strip()
        if not raw_text:
            self.controller.show_notification("Интейк", "Введите текст переписки или ТЗ", error=True)
            return

        base_dir = self.controller.base_dir
        api_key = read_env_api_key("OPENROUTER_API_KEY", base_dir) or read_env_api_key("ANTIGRAVITY_API_KEY", base_dir) or "antigravity-local"

        def worker() -> dict[str, Any]:
            return self.controller.intake_parser.parse_raw_kwork_text_llm(raw_text, api_key)

        def done(result: dict[str, Any]) -> None:
            self._apply_parsed_result(result)
            self.controller.show_notification(
                "LLM-разбор завершен",
                f"Клиент '{result.get('client_name')}', конфигурация '{result.get('config_name')}'",
            )

        self.controller.run_in_background(worker, on_done=done)

    def on_copy_questions(self) -> None:
        txt = self.questions_box.get("1.0", "end").strip()
        if not txt:
            self.controller.show_notification("Вопросы", "Нет сгенерированных вопросов", error=True)
            return
        self.clipboard_clear()
        self.clipboard_append(txt)
        self.controller.show_notification("Скопировано", "Уточняющие вопросы скопированы в буфер для чата Kwork")

    def on_submit_create(self, open_vscode: bool = False) -> None:
        slug = self.slug_entry.get().strip()
        client = self.client_entry.get().strip() or "client"
        title = self.title_entry.get().strip() or "Новая разработка 1С"
        kind_label = self.kind_segmented.get()
        kind = KIND_LABELS.get(kind_label, "vpf")
        config = self.config_entry.get().strip()
        deadline = self.deadline_entry.get().strip()
        object_name = self.object_name_entry.get().strip()
        raw_notes = self.raw_text_box.get("1.0", "end").strip()
        questions_txt = self.questions_box.get("1.0", "end").strip()

        try:
            price_val = float(self.price_entry.get().strip().replace(" ", "") or "0")
        except ValueError:
            price_val = 0.0

        if not slug:
            slug = f"{client}_{kind}"

        try:
            order_detail = self.controller.orders_facade.create_order(
                order_slug=slug,
                client=client,
                title=title,
                kind=kind,
                price=price_val,
                configuration=config,
                deadline=deadline,
                object_name=object_name,
                notes=raw_notes,
                clarifying_questions_text=questions_txt,
            )

            if open_vscode:
                self.controller.run_in_background(
                    lambda: self.controller.onec_runner.open_in_vscode(order_detail.order_dir)
                )

            self.controller.show_notification(
                "Заказ создан",
                f"Заказ {order_detail.order_id} успешно создан",
            )
            self.controller.show_view("order_detail", order_id=order_detail.order_id)
        except Exception as error:
            LOGGER.error("Order creation failed: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка создания заказа", str(error), error=True)
