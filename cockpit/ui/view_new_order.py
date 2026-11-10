from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import customtkinter

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
    "Расширение": "extension",
    "ТЗ": "tz",
}
KIND_REVERSE: dict[str, str] = {v: k for k, v in KIND_LABELS.items()}


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

        self.raw_text_box = customtkinter.CTkTextbox(self.intake_frame, height=120)
        self.raw_text_box.grid(row=1, column=0, sticky="ew", padx=14, pady=6)

        self.parse_btn = customtkinter.CTkButton(
            self.intake_frame,
            text="⚡ Распознать параметры (AI / Regex)",
            fg_color="#0D9488",
            hover_color="#0F766E",
            font=customtkinter.CTkFont(weight="bold"),
            command=self.on_parse_intake,
        )
        self.parse_btn.grid(row=2, column=0, sticky="w", padx=14, pady=(6, 12))

        self.form_frame = customtkinter.CTkFrame(self, fg_color="#1E293B", corner_radius=8)
        self.form_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=10)
        self.form_frame.grid_columnconfigure((0, 1), weight=1)

        self._build_form_fields()

        self.btn_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.btn_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=15)
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

    def on_parse_intake(self) -> None:
        raw_text = self.raw_text_box.get("1.0", "end").strip()
        if not raw_text:
            self.controller.show_notification("Интейк", "Введите текст переписки или ТЗ", error=True)
            return

        try:
            result = self.controller.intake_parser.parse_raw_kwork_text_regex(raw_text)
            if result.order_slug:
                self.slug_entry.delete(0, "end")
                self.slug_entry.insert(0, result.order_slug)
            if result.client:
                self.client_entry.delete(0, "end")
                self.client_entry.insert(0, result.client)
            if result.title:
                self.title_entry.delete(0, "end")
                self.title_entry.insert(0, result.title)
            if result.price > 0:
                self.price_entry.delete(0, "end")
                self.price_entry.insert(0, str(int(result.price)))
            if result.deadline:
                self.deadline_entry.delete(0, "end")
                self.deadline_entry.insert(0, result.deadline)
            if result.configuration:
                self.config_entry.delete(0, "end")
                self.config_entry.insert(0, result.configuration)
            if result.object_name:
                self.object_name_entry.delete(0, "end")
                self.object_name_entry.insert(0, result.object_name)

            label_kind = KIND_REVERSE.get(result.kind, "ВПФ")
            self.kind_segmented.set(label_kind)

            self.controller.show_notification(
                "ТЗ распознано",
                f"Успешно извлечено: клиент '{result.client}', конфигурация '{result.configuration}'",
            )
        except Exception as error:
            LOGGER.error("Failed to parse intake text: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка распознавания", str(error), error=True)

    def on_submit_create(self, open_vscode: bool = False) -> None:
        slug = self.slug_entry.get().strip()
        client = self.client_entry.get().strip() or "client"
        title = self.title_entry.get().strip() or "Новая разработка 1С"
        kind_label = self.kind_segmented.get()
        kind = KIND_LABELS.get(kind_label, "vpf")
        config = self.config_entry.get().strip()
        deadline = self.deadline_entry.get().strip()
        object_name = self.object_name_entry.get().strip()

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
            )

            if open_vscode:
                self.controller.onec_runner.open_in_vscode(order_detail.order_dir)

            self.controller.show_notification(
                "Заказ создан",
                f"Заказ {order_detail.order_id} успешно создан",
            )
            self.controller.show_view("order_detail", order_id=order_detail.order_id)
        except Exception as error:
            LOGGER.error("Order creation failed: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка создания заказа", str(error), error=True)
