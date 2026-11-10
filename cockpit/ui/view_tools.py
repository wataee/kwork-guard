from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import subprocess
import threading
from typing import Any
import customtkinter

LOGGER = logging.getLogger("cockpit.view_tools")
if not LOGGER.handlers:
    _log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handler = RotatingFileHandler(_log_dir / "cockpit.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)


class ViewTools(customtkinter.CTkFrame):
    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.controller = controller

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.top_frame = customtkinter.CTkFrame(self, fg_color="transparent")
        self.top_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(15, 8))
        self.top_frame.grid_columnconfigure(0, weight=1)

        self.title_label = customtkinter.CTkLabel(
            self.top_frame,
            text="🧰 Инструменты автоматизации и База сниппетов",
            font=customtkinter.CTkFont(size=22, weight="bold"),
        )
        self.title_label.grid(row=0, column=0, sticky="w")

        self.actions_bar = customtkinter.CTkFrame(self, fg_color="#1E293B", corner_radius=8)
        self.actions_bar.grid(row=1, column=0, sticky="ew", padx=20, pady=8)

        self.btn_verify_lib = customtkinter.CTkButton(
            self.actions_bar,
            text="🔍 Проверить базу сниппетов",
            fg_color="#0D9488",
            hover_color="#0F766E",
            command=self.on_verify_library,
        )
        self.btn_verify_lib.pack(side="left", padx=10, pady=10)

        self.btn_gen_lessons = customtkinter.CTkButton(
            self.actions_bar,
            text="📅 Сформировать еженедельный отчет",
            fg_color="#334155",
            hover_color="#475569",
            command=self.on_generate_lessons,
        )
        self.btn_gen_lessons.pack(side="left", padx=6, pady=10)

        self.btn_run_tests = customtkinter.CTkButton(
            self.actions_bar,
            text="🧪 Запустить pytest (Zero-Trust)",
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            command=self.on_run_pytest,
        )
        self.btn_run_tests.pack(side="left", padx=6, pady=10)

        self.main_split = customtkinter.CTkFrame(self, fg_color="transparent")
        self.main_split.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 15))
        self.main_split.grid_columnconfigure(0, weight=1)
        self.main_split.grid_columnconfigure(1, weight=2)
        self.main_split.grid_rowconfigure(0, weight=1)

        self.left_col = customtkinter.CTkFrame(self.main_split, fg_color="#1E293B", corner_radius=8)
        self.left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=0)
        self.left_col.grid_rowconfigure(2, weight=1)
        self.left_col.grid_columnconfigure(0, weight=1)

        lbl_snippets = customtkinter.CTkLabel(
            self.left_col,
            text="Золотой фонд 1С (сниппеты):",
            font=customtkinter.CTkFont(size=14, weight="bold"),
        )
        lbl_snippets.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        self.search_snip = customtkinter.CTkEntry(self.left_col, placeholder_text="Поиск сниппета...")
        self.search_snip.grid(row=1, column=0, sticky="ew", padx=12, pady=4)
        self.search_snip.bind("<KeyRelease>", lambda e: self.filter_snippets())

        self.snippets_list = customtkinter.CTkScrollableFrame(self.left_col, fg_color="transparent")
        self.snippets_list.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 10))
        self.snippets_list.grid_columnconfigure(0, weight=1)

        self.right_col = customtkinter.CTkFrame(self.main_split, fg_color="#1E293B", corner_radius=8)
        self.right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=0)
        self.right_col.grid_rowconfigure(1, weight=1)
        self.right_col.grid_columnconfigure(0, weight=1)

        preview_header = customtkinter.CTkFrame(self.right_col, fg_color="transparent")
        preview_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
        preview_header.grid_columnconfigure(0, weight=1)
        preview_header.grid_columnconfigure(1, weight=0)

        self.preview_title = customtkinter.CTkLabel(
            preview_header,
            text="Просмотр содержимого:",
            font=customtkinter.CTkFont(size=14, weight="bold"),
        )
        self.preview_title.grid(row=0, column=0, sticky="w")

        self.btn_copy_snip = customtkinter.CTkButton(
            preview_header,
            text="📋 Скопировать код",
            width=130,
            command=self.on_copy_selected_snippet,
        )
        self.btn_copy_snip.grid(row=0, column=1, sticky="e")

        self.preview_box = customtkinter.CTkTextbox(
            self.right_col,
            font=customtkinter.CTkFont(family="Consolas", size=12),
        )
        self.preview_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(4, 12))

        self._snippets_data: list[dict[str, Any]] = []
        self._current_snippet_code: str = ""

    def load_data(self) -> None:
        index_file = self.controller.base_dir / "library" / "index.json"
        self._snippets_data = []
        if index_file.exists():
            try:
                raw = json.loads(index_file.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    self._snippets_data = raw
                elif isinstance(raw, dict) and "snippets" in raw:
                    self._snippets_data = raw["snippets"]
            except Exception as error:
                LOGGER.error("Failed to read library/index.json: %s", error)

        self.filter_snippets()

    def filter_snippets(self) -> None:
        query = self.search_snip.get().strip().lower()
        for child in self.snippets_list.winfo_children():
            child.destroy()

        matched = [
            s for s in self._snippets_data
            if not query or query in s.get("id", "").lower() or query in s.get("title", "").lower() or query in s.get("description", "").lower()
        ]

        if not matched:
            customtkinter.CTkLabel(self.snippets_list, text="Сниппеты не найдены", text_color="#94A3B8").grid(row=0, column=0, pady=10)
            return

        for idx, item in enumerate(matched):
            snip_id = item.get("id", f"snippet_{idx}")
            title = item.get("title", snip_id)
            btn = customtkinter.CTkButton(
                self.snippets_list,
                text=title,
                anchor="w",
                fg_color="#334155",
                hover_color="#475569",
                command=lambda snip=item: self.select_snippet(snip),
            )
            btn.grid(row=idx, column=0, sticky="ew", pady=2)

    def select_snippet(self, item: dict[str, Any]) -> None:
        file_path_str = item.get("file", "")
        code = ""
        if file_path_str:
            target = self.controller.base_dir / "library" / file_path_str
            if target.exists():
                try:
                    code = target.read_text(encoding="utf-8")
                except Exception as error:
                    LOGGER.error("Failed to read snippet file %s: %s", target, error)
        if not code:
            code = item.get("code", item.get("description", ""))

        self._current_snippet_code = code
        self.preview_title.configure(text=f"Сниппет: {item.get('title', item.get('id', ''))}")
        self.preview_box.delete("1.0", "end")
        self.preview_box.insert("1.0", code)

    def on_copy_selected_snippet(self) -> None:
        if not self._current_snippet_code:
            self.controller.show_notification("Сниппет", "Выберите сниппет для копирования", error=True)
            return
        self.clipboard_clear()
        self.clipboard_append(self._current_snippet_code)
        self.controller.show_notification("Сниппет скопирован", "Код сниппета помещен в буфер обмена")

    def on_verify_library(self) -> None:
        try:
            from tools.library import verify_library
            ok, report = verify_library(self.controller.base_dir)
            self.preview_title.configure(text="Результат проверки базы сниппетов:")
            self.preview_box.delete("1.0", "end")
            self.preview_box.insert("1.0", report)
            self.controller.show_notification("Библиотека", "Проверка завершена", error=not ok)
        except Exception as error:
            LOGGER.error("Verify library error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка проверки", str(error), error=True)

    def on_generate_lessons(self) -> None:
        try:
            from tools.lessons import generate_weekly_report
            report = generate_weekly_report(self.controller.base_dir)
            self.preview_title.configure(text="Еженедельный отчет по урокам:")
            self.preview_box.delete("1.0", "end")
            self.preview_box.insert("1.0", report)
            self.controller.show_notification("Уроки", "Еженедельный отчет сформирован")
        except Exception as error:
            LOGGER.error("Generate lessons error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка отчета", str(error), error=True)

    def on_run_pytest(self) -> None:
        self.preview_title.configure(text="Выполнение pytest...")
        self.preview_box.delete("1.0", "end")
        self.preview_box.insert("1.0", "Запуск unit-тестов. Пожалуйста, подождите...\n")

        def worker() -> None:
            rc_ok = False
            output = ""
            try:
                proc = subprocess.run(
                    ["pytest", "tests/", "-v", "--tb=short"],
                    cwd=str(self.controller.base_dir),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=60,
                )
                output = proc.stdout + "\n" + proc.stderr
                rc_ok = proc.returncode == 0
            except Exception as error:
                LOGGER.error("Pytest execution failed: %s", error, exc_info=True)
                output = f"Ошибка запуска pytest: {error}"
            try:
                if self.winfo_exists():
                    self.after(0, lambda: self._on_pytest_done(rc_ok, output))
            except Exception as sched_err:
                LOGGER.debug("Widget destroyed before pytest thread finished: %s", sched_err)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _on_pytest_done(self, success: bool, output: str) -> None:
        self.preview_title.configure(text="Результаты unit-тестов (pytest):")
        self.preview_box.delete("1.0", "end")
        self.preview_box.insert("1.0", output)
        if success:
            self.controller.show_notification("Тесты пройдены", "100% тестов успешно завершены")
        else:
            self.controller.show_notification("Тесты не прошли", "Обнаружены ошибки в тестах", error=True)
