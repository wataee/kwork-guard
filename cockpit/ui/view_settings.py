from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import customtkinter

from guard.fs_utils import atomic_write_json, atomic_write_text

LOGGER = logging.getLogger("cockpit.view_settings")
if not LOGGER.handlers:
    _log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handler = RotatingFileHandler(_log_dir / "cockpit.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)


class ViewSettings(customtkinter.CTkScrollableFrame):
    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.controller = controller

        self.grid_columnconfigure(0, weight=1)

        self.title_label = customtkinter.CTkLabel(
            self,
            text="⚙️ Настройки Dual-Backend и Диагностика",
            font=customtkinter.CTkFont(size=22, weight="bold"),
        )
        self.title_label.grid(row=0, column=0, sticky="w", padx=20, pady=(15, 10))

        self.doc_frame = customtkinter.CTkFrame(self, fg_color="#1E293B", corner_radius=8)
        self.doc_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=8)
        self.doc_frame.grid_columnconfigure(0, weight=1)

        doc_header = customtkinter.CTkFrame(self.doc_frame, fg_color="transparent")
        doc_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        doc_header.grid_columnconfigure(0, weight=1)
        doc_header.grid_columnconfigure(1, weight=0)

        customtkinter.CTkLabel(
            doc_header,
            text="🩺 Системный Доктор (12 проверок окружения):",
            font=customtkinter.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        self.btn_run_doctor = customtkinter.CTkButton(
            doc_header,
            text="🔍 Запустить диагностику",
            fg_color="#0D9488",
            hover_color="#0F766E",
            command=self.on_run_diagnostics,
        )
        self.btn_run_doctor.grid(row=0, column=1, sticky="e")

        self.doc_results_container = customtkinter.CTkFrame(self.doc_frame, fg_color="transparent")
        self.doc_results_container.grid(row=1, column=0, sticky="ew", padx=14, pady=(4, 12))
        self.doc_results_container.grid_columnconfigure(0, weight=1)

        self.config_frame = customtkinter.CTkFrame(self, fg_color="#1E293B", corner_radius=8)
        self.config_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=10)
        self.config_frame.grid_columnconfigure((0, 1), weight=1)

        customtkinter.CTkLabel(
            self.config_frame,
            text="Параметры Dual-Backend, OpenRouter и 1С:",
            font=customtkinter.CTkFont(size=14, weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 8))

        self._build_config_inputs()

    def _build_config_inputs(self) -> None:
        row = 1

        lbl_prov = customtkinter.CTkLabel(self.config_frame, text="Активный LLM-провайдер:")
        lbl_prov.grid(row=row, column=0, sticky="w", padx=14, pady=(4, 2))
        self.provider_menu = customtkinter.CTkOptionMenu(
            self.config_frame,
            values=["antigravity", "openrouter"],
        )
        self.provider_menu.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))

        self.fallback_var = customtkinter.BooleanVar(value=True)
        self.fallback_chk = customtkinter.CTkCheckBox(
            self.config_frame,
            text="Фоллбэк на OpenRouter при 429 / таймауте",
            variable=self.fallback_var,
        )
        self.fallback_chk.grid(row=row + 1, column=1, sticky="w", padx=14, pady=(0, 8))

        row += 2

        lbl_agy_url = customtkinter.CTkLabel(self.config_frame, text="Antigravity Gateway URL:")
        lbl_agy_url.grid(row=row, column=0, sticky="w", padx=14, pady=(4, 2))
        self.agy_url_entry = customtkinter.CTkEntry(self.config_frame)
        self.agy_url_entry.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))

        lbl_agy_key = customtkinter.CTkLabel(self.config_frame, text="Antigravity Key (.env):")
        lbl_agy_key.grid(row=row, column=1, sticky="w", padx=14, pady=(4, 2))
        self.agy_key_entry = customtkinter.CTkEntry(self.config_frame, show="*")
        self.agy_key_entry.grid(row=row + 1, column=1, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        lbl_fast = customtkinter.CTkLabel(self.config_frame, text="Быстрая модель (интейк/сжатие):")
        lbl_fast.grid(row=row, column=0, sticky="w", padx=14, pady=(4, 2))
        self.fast_model_entry = customtkinter.CTkEntry(self.config_frame)
        self.fast_model_entry.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))

        lbl_code = customtkinter.CTkLabel(self.config_frame, text="Модель кода (BSL/СКД/EDT MCP):")
        lbl_code.grid(row=row, column=1, sticky="w", padx=14, pady=(4, 2))
        self.code_model_entry = customtkinter.CTkEntry(self.config_frame)
        self.code_model_entry.grid(row=row + 1, column=1, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        lbl_api = customtkinter.CTkLabel(self.config_frame, text="OpenRouter API Key (.env):")
        lbl_api.grid(row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(4, 2))
        self.api_key_entry = customtkinter.CTkEntry(self.config_frame, show="*")
        self.api_key_entry.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        lbl_thin = customtkinter.CTkLabel(self.config_frame, text="Путь к 1cv8c.exe (Тонкий клиент):")
        lbl_thin.grid(row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(4, 2))
        self.thin_entry = customtkinter.CTkEntry(self.config_frame)
        self.thin_entry.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        lbl_ib = customtkinter.CTkLabel(self.config_frame, text="Путь к тестовой базе (IB):")
        lbl_ib.grid(row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(4, 2))
        self.ib_entry = customtkinter.CTkEntry(self.config_frame)
        self.ib_entry.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        lbl_edt = customtkinter.CTkLabel(self.config_frame, text="EDT MCP Порт:")
        lbl_edt.grid(row=row, column=0, sticky="w", padx=14, pady=(4, 2))
        self.edt_port_entry = customtkinter.CTkEntry(self.config_frame)
        self.edt_port_entry.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))

        lbl_guard = customtkinter.CTkLabel(self.config_frame, text="Guard Порт:")
        lbl_guard.grid(row=row, column=1, sticky="w", padx=14, pady=(4, 2))
        self.guard_port_entry = customtkinter.CTkEntry(self.config_frame)
        self.guard_port_entry.grid(row=row + 1, column=1, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        lbl_rate = customtkinter.CTkLabel(self.config_frame, text="Курс USD к RUB:")
        lbl_rate.grid(row=row, column=0, sticky="w", padx=14, pady=(4, 2))
        self.rate_entry = customtkinter.CTkEntry(self.config_frame)
        self.rate_entry.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 14))

        self.btn_save_config = customtkinter.CTkButton(
            self.config_frame,
            text="💾 Сохранить настройки",
            fg_color="#059669",
            hover_color="#047857",
            command=self.on_save_settings,
        )
        self.btn_save_config.grid(row=row + 1, column=1, sticky="e", padx=14, pady=(0, 14))

    def load_data(self) -> None:
        cfg_file = self.controller.base_dir / "config.json"
        cfg_data: dict[str, Any] = {}
        if cfg_file.exists():
            try:
                cfg_data = json.loads(cfg_file.read_text(encoding="utf-8"))
            except Exception as error:
                LOGGER.error("Failed to read config.json: %s", error)

        env_file = self.controller.base_dir / ".env"
        api_key = ""
        agy_key = ""
        if env_file.exists():
            try:
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith("OPENROUTER_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    elif line.startswith("ANTIGRAVITY_API_KEY="):
                        agy_key = line.split("=", 1)[1].strip().strip('"').strip("'")
            except Exception as error:
                LOGGER.error("Failed to read .env: %s", error)

        self.provider_menu.set(str(cfg_data.get("active_provider", "antigravity")))
        providers = cfg_data.get("providers", {})
        agy_cfg = providers.get("antigravity", {})
        self.fallback_var.set(bool(agy_cfg.get("fallback_to_openrouter", True)))

        self.agy_url_entry.delete(0, "end")
        self.agy_url_entry.insert(0, str(agy_cfg.get("base_url", "http://127.0.0.1:3000/v1")))

        self.agy_key_entry.delete(0, "end")
        self.agy_key_entry.insert(0, agy_key)

        routing = cfg_data.get("model_routing", {})
        self.fast_model_entry.delete(0, "end")
        self.fast_model_entry.insert(0, str(routing.get("fast_tasks", "google/gemini-2.5-flash")))

        self.code_model_entry.delete(0, "end")
        self.code_model_entry.insert(0, str(routing.get("coding_tasks", "anthropic/claude-3-7-sonnet")))

        self.api_key_entry.delete(0, "end")
        self.api_key_entry.insert(0, api_key)

        onec = cfg_data.get("onec", {})
        self.thin_entry.delete(0, "end")
        self.thin_entry.insert(0, onec.get("thin_client_path", ""))

        self.ib_entry.delete(0, "end")
        self.ib_entry.insert(0, onec.get("test_ib_path", ""))

        self.edt_port_entry.delete(0, "end")
        self.edt_port_entry.insert(0, str(cfg_data.get("upstream_port", 8765)))

        self.guard_port_entry.delete(0, "end")
        self.guard_port_entry.insert(0, str(cfg_data.get("guard_port", 8766)))

        openrouter_cfg = cfg_data.get("openrouter", {})
        self.rate_entry.delete(0, "end")
        self.rate_entry.insert(0, str(openrouter_cfg.get("usd_to_rub", 95.0)))

    def on_run_diagnostics(self) -> None:
        for child in self.doc_results_container.winfo_children():
            child.destroy()
        customtkinter.CTkLabel(
            self.doc_results_container,
            text="Выполняется проверка окружения...",
            text_color="#94A3B8",
        ).grid(row=0, column=0, sticky="w", pady=4)

        def worker() -> list[dict[str, str]]:
            return self.controller.system_doctor.run_full_diagnostics()

        def done(results: list[dict[str, str]]) -> None:
            if not self.winfo_exists():
                return
            for child in self.doc_results_container.winfo_children():
                child.destroy()

            all_ok = True
            for idx, res in enumerate(results):
                row = customtkinter.CTkFrame(self.doc_results_container, fg_color="transparent")
                row.grid(row=idx, column=0, sticky="ew", pady=2)
                row.grid_columnconfigure(1, weight=1)

                is_passed = (res.get("status") == "OK") if isinstance(res, dict) else getattr(res, "passed", False)
                name = res.get("check", "") if isinstance(res, dict) else getattr(res, "name", "")
                msg = res.get("message", "") if isinstance(res, dict) else getattr(res, "message", "")
                fix = res.get("fix", "") if isinstance(res, dict) else getattr(res, "remediation", "")

                if not is_passed:
                    all_ok = False

                icon = "✅" if is_passed else "❌"
                color = "#10B981" if is_passed else "#EF4444"
                customtkinter.CTkLabel(row, text=icon, font=customtkinter.CTkFont(size=14)).grid(row=0, column=0, padx=(0, 8))

                txt = f"{name}: {msg}"
                if not is_passed and fix:
                    txt += f"  (Решение: {fix})"
                customtkinter.CTkLabel(row, text=txt, text_color=color, anchor="w").grid(row=0, column=1, sticky="w")

            self.controller.show_notification(
                "Диагностика",
                "Все проверки пройдены" if all_ok else "Обнаружены замечания",
                error=not all_ok,
            )

        def on_err(error: Exception) -> None:
            self.controller.show_notification("Ошибка диагностики", str(error), error=True)

        self.controller.run_in_background(worker, on_done=done, on_error=on_err)

    def on_save_settings(self) -> None:
        cfg_file = self.controller.base_dir / "config.json"
        cfg_data: dict[str, Any] = {}
        if cfg_file.exists():
            try:
                cfg_data = json.loads(cfg_file.read_text(encoding="utf-8"))
            except Exception as error:
                LOGGER.error("Failed to load existing config.json: %s", error)

        try:
            edt_port = int(self.edt_port_entry.get().strip() or "8765")
            guard_port = int(self.guard_port_entry.get().strip() or "8766")
            usd_rub = float(self.rate_entry.get().strip() or "95.0")
        except ValueError as err:
            self.controller.show_notification("Ошибка ввода", f"Некорректный числовой формат: {err}", error=True)
            return

        active_prov = self.provider_menu.get().strip()
        cfg_data["active_provider"] = active_prov
        cfg_data["upstream_port"] = edt_port
        cfg_data["guard_port"] = guard_port

        providers = cfg_data.setdefault("providers", {})
        agy_cfg = providers.setdefault("antigravity", {})
        agy_cfg["base_url"] = self.agy_url_entry.get().strip() or "http://127.0.0.1:3000/v1"
        agy_cfg["api_key_env"] = "ANTIGRAVITY_API_KEY"
        agy_cfg["fallback_to_openrouter"] = bool(self.fallback_var.get())

        routing = cfg_data.setdefault("model_routing", {})
        routing["fast_tasks"] = self.fast_model_entry.get().strip() or "google/gemini-2.5-flash"
        routing["coding_tasks"] = self.code_model_entry.get().strip() or "anthropic/claude-3-7-sonnet"

        if "onec" not in cfg_data:
            cfg_data["onec"] = {}
        cfg_data["onec"]["thin_client_path"] = self.thin_entry.get().strip()
        cfg_data["onec"]["test_ib_path"] = self.ib_entry.get().strip()

        if "openrouter" not in cfg_data:
            cfg_data["openrouter"] = {}
        cfg_data["openrouter"]["usd_to_rub"] = usd_rub

        try:
            atomic_write_json(cfg_file, cfg_data)

            api_key = self.api_key_entry.get().strip()
            agy_key = self.agy_key_entry.get().strip()
            env_file = self.controller.base_dir / ".env"
            env_lines = []
            if env_file.exists():
                for line in env_file.read_text(encoding="utf-8").splitlines():
                    if not line.startswith("OPENROUTER_API_KEY=") and not line.startswith("ANTIGRAVITY_API_KEY="):
                        env_lines.append(line)
            env_lines.append(f'OPENROUTER_API_KEY="{api_key}"')
            env_lines.append(f'ANTIGRAVITY_API_KEY="{agy_key}"')
            atomic_write_text(env_file, "\n".join(env_lines) + "\n", encoding="utf-8")

            self.controller.top_bar.refresh_status()
            self.controller.show_notification("Настройки сохранены", "Конфигурация успешно обновлена")
        except Exception as error:
            LOGGER.error("Save settings error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка сохранения", str(error), error=True)
