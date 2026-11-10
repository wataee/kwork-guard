from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import customtkinter

from cockpit.services.kwork_client import KworkAuthRequiredError
from cockpit.ui.theme import (
    BG_ELEVATED,
    BG_MAIN,
    BG_SURFACE,
    BORDER,
    DANGER_BG,
    DANGER_FG,
    NEUTRAL_BG,
    NEUTRAL_FG,
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
)
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
            text="Настройки Kwork, Antigravity и Системы",
            font=font_ui(20, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.title_label.grid(row=0, column=0, sticky="w", padx=20, pady=(14, 10))

        self.kwork_card = create_card(self, fg_color=BG_SURFACE, corner_radius=8)
        self.kwork_card.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.kwork_card.grid_columnconfigure((0, 1), weight=1)

        kw_hdr = customtkinter.CTkFrame(self.kwork_card, fg_color="transparent")
        kw_hdr.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 6))
        kw_hdr.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            kw_hdr,
            text="Авторизация и синхронизация Kwork (сессия хранится локально в state/kwork_session.json)",
            font=font_ui(14, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")

        self.kwork_auth_badge = create_badge(kw_hdr, "Не настроена", fg_color=NEUTRAL_BG, text_color=NEUTRAL_FG)
        self.kwork_auth_badge.grid(row=0, column=1, sticky="e")

        customtkinter.CTkLabel(
            self.kwork_card,
            text="Cookies из браузера (строка Cookie или JSON-массив EditThisCookie):",
            font=font_ui(12),
            text_color=TEXT_SECONDARY,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=14, pady=(4, 2))

        self.kwork_cookie_box = create_textbox(self.kwork_card, height=70, mono=True)
        self.kwork_cookie_box.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8))

        customtkinter.CTkLabel(self.kwork_card, text="Токен API / Сессии (опционально):", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=3, column=0, sticky="w", padx=14, pady=(4, 2)
        )
        self.kwork_token_entry = create_entry(self.kwork_card, show="*", mono=True)
        self.kwork_token_entry.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 8))

        customtkinter.CTkLabel(self.kwork_card, text="Логин Kwork:", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=3, column=1, sticky="w", padx=14, pady=(4, 2)
        )
        self.kwork_login_entry = create_entry(self.kwork_card, placeholder_text="username / email")
        self.kwork_login_entry.grid(row=4, column=1, sticky="ew", padx=14, pady=(0, 8))

        customtkinter.CTkLabel(
            self.kwork_card,
            text="Пароль (только для первичного входа; при капче/2FA импортируйте Cookie):",
            text_color=TEXT_SECONDARY,
            font=font_ui(12),
        ).grid(row=5, column=0, sticky="w", padx=14, pady=(4, 2))
        self.kwork_password_entry = create_entry(self.kwork_card, show="*")
        self.kwork_password_entry.grid(row=6, column=0, sticky="ew", padx=14, pady=(0, 12))

        kw_btns = customtkinter.CTkFrame(self.kwork_card, fg_color="transparent")
        kw_btns.grid(row=6, column=1, sticky="e", padx=14, pady=(0, 12))

        create_button(kw_btns, text="Сохранить сессию", variant="primary", command=self.on_save_kwork_session).pack(
            side="left", padx=3
        )
        create_button(kw_btns, text="Войти по паролю", variant="secondary", command=self.on_login_kwork_password).pack(
            side="left", padx=3
        )
        create_button(kw_btns, text="Проверить сессию", variant="secondary", command=self.on_check_kwork_session).pack(
            side="left", padx=3
        )
        create_button(kw_btns, text="Сбросить", variant="danger", width=76, command=self.on_clear_kwork_session).pack(
            side="left", padx=(3, 0)
        )

        self.doc_frame = create_card(self, fg_color=BG_SURFACE, corner_radius=8)
        self.doc_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=8)
        self.doc_frame.grid_columnconfigure(0, weight=1)

        doc_header = customtkinter.CTkFrame(self.doc_frame, fg_color="transparent")
        doc_header.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        doc_header.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            doc_header,
            text="Системный Доктор (12 проверок окружения)",
            font=font_ui(14, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")

        self.btn_run_doctor = create_button(
            doc_header,
            text="Запустить диагностику",
            variant="primary",
            command=self.on_run_diagnostics,
        )
        self.btn_run_doctor.grid(row=0, column=1, sticky="e")

        self.doc_results_container = customtkinter.CTkFrame(self.doc_frame, fg_color="transparent")
        self.doc_results_container.grid(row=1, column=0, sticky="ew", padx=14, pady=(4, 12))
        self.doc_results_container.grid_columnconfigure(0, weight=1)

        self.config_frame = create_card(self, fg_color=BG_SURFACE, corner_radius=8)
        self.config_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=(8, 16))
        self.config_frame.grid_columnconfigure((0, 1), weight=1)

        customtkinter.CTkLabel(
            self.config_frame,
            text="Параметры Dual-Backend, OpenRouter и 1С",
            font=font_ui(14, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 8))

        self._build_config_inputs()

    def _build_config_inputs(self) -> None:
        row = 1

        customtkinter.CTkLabel(self.config_frame, text="Активный LLM-провайдер:", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=row, column=0, sticky="w", padx=14, pady=(4, 2)
        )
        self.provider_menu = create_option_menu(self.config_frame, values=["antigravity", "openrouter"])
        self.provider_menu.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))

        self.fallback_var = customtkinter.BooleanVar(value=False)
        self.fallback_chk = customtkinter.CTkCheckBox(
            self.config_frame,
            text="Резервный переход на OpenRouter (выкл = строго раздельно)",
            variable=self.fallback_var,
            text_color=TEXT_PRIMARY,
            font=font_ui(12),
        )
        self.fallback_chk.grid(row=row + 1, column=1, sticky="w", padx=14, pady=(0, 8))

        row += 2

        customtkinter.CTkLabel(self.config_frame, text="Antigravity Gateway URL:", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=row, column=0, sticky="w", padx=14, pady=(4, 2)
        )
        self.agy_url_entry = create_entry(self.config_frame, mono=True)
        self.agy_url_entry.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))

        customtkinter.CTkLabel(self.config_frame, text="Antigravity Key (.env):", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=row, column=1, sticky="w", padx=14, pady=(4, 2)
        )
        self.agy_key_entry = create_entry(self.config_frame, show="*", mono=True)
        self.agy_key_entry.grid(row=row + 1, column=1, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        customtkinter.CTkLabel(self.config_frame, text="Быстрая модель (интейк/сжатие):", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=row, column=0, sticky="w", padx=14, pady=(4, 2)
        )
        self.fast_model_entry = create_entry(self.config_frame, mono=True)
        self.fast_model_entry.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))

        customtkinter.CTkLabel(self.config_frame, text="Модель кода (BSL/СКД/EDT MCP):", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=row, column=1, sticky="w", padx=14, pady=(4, 2)
        )
        self.code_model_entry = create_entry(self.config_frame, mono=True)
        self.code_model_entry.grid(row=row + 1, column=1, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        customtkinter.CTkLabel(self.config_frame, text="OpenRouter API Key (.env):", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(4, 2)
        )
        self.api_key_entry = create_entry(self.config_frame, show="*", mono=True)
        self.api_key_entry.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        customtkinter.CTkLabel(self.config_frame, text="Путь к 1cv8c.exe (Тонкий клиент):", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(4, 2)
        )
        self.thin_entry = create_entry(self.config_frame, mono=True)
        self.thin_entry.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        customtkinter.CTkLabel(self.config_frame, text="Путь к тестовой базе (IB):", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=row, column=0, columnspan=2, sticky="w", padx=14, pady=(4, 2)
        )
        self.ib_entry = create_entry(self.config_frame, mono=True)
        self.ib_entry.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        customtkinter.CTkLabel(self.config_frame, text="EDT MCP Порт:", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=row, column=0, sticky="w", padx=14, pady=(4, 2)
        )
        self.edt_port_entry = create_entry(self.config_frame, mono=True)
        self.edt_port_entry.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 8))

        customtkinter.CTkLabel(self.config_frame, text="Guard Порт:", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=row, column=1, sticky="w", padx=14, pady=(4, 2)
        )
        self.guard_port_entry = create_entry(self.config_frame, mono=True)
        self.guard_port_entry.grid(row=row + 1, column=1, sticky="ew", padx=14, pady=(0, 8))

        row += 2

        customtkinter.CTkLabel(self.config_frame, text="Курс USD к RUB:", text_color=TEXT_SECONDARY, font=font_ui(12)).grid(
            row=row, column=0, sticky="w", padx=14, pady=(4, 2)
        )
        self.rate_entry = create_entry(self.config_frame, mono=True)
        self.rate_entry.grid(row=row + 1, column=0, sticky="ew", padx=14, pady=(0, 14))

        self.btn_save_config = create_button(
            self.config_frame,
            text="Сохранить настройки",
            variant="primary",
            height=34,
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
        self.fallback_var.set(bool(agy_cfg.get("fallback_to_openrouter", False)))

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

        if hasattr(self.controller, "kwork_sync"):
            client = self.controller.kwork_sync.client
            self.kwork_login_entry.delete(0, "end")
            self.kwork_login_entry.insert(0, client.username or str(cfg_data.get("kwork", {}).get("username") or ""))
            self.kwork_token_entry.delete(0, "end")
            if client.token:
                self.kwork_token_entry.insert(0, client.token)
            if client.has_session():
                self._set_kwork_badge("Сессия сохранена", SUCCESS_FG, SUCCESS_BG)
            else:
                self._set_kwork_badge("Не настроена", NEUTRAL_FG, NEUTRAL_BG)

    def _set_kwork_badge(self, text: str, fg: str, bg: str) -> None:
        self.kwork_auth_badge.configure(fg_color=bg)
        if hasattr(self.kwork_auth_badge, "text_label"):
            self.kwork_auth_badge.text_label.configure(text=text, text_color=fg)

    def on_save_kwork_session(self) -> None:
        cookie_raw = self.kwork_cookie_box.get("1.0", "end").strip()
        token_raw = self.kwork_token_entry.get().strip()
        login_raw = self.kwork_login_entry.get().strip()
        client = self.controller.kwork_sync.client
        client.set_session_credentials(cookie_text=cookie_raw, token=token_raw, username=login_raw)
        if client.has_session():
            self._set_kwork_badge("Сессия сохранена", SUCCESS_FG, SUCCESS_BG)
            self.controller.show_notification("Kwork", "Куки и токен сессии сохранены в state/kwork_session.json")
        else:
            self._set_kwork_badge("Пустая сессия", WARNING_FG, WARNING_BG)
            self.controller.show_notification("Kwork", "Вставьте строку Cookie или токен", error=True)
        self.controller.top_bar.refresh_status()

    def on_login_kwork_password(self) -> None:
        login = self.kwork_login_entry.get().strip()
        pwd = self.kwork_password_entry.get()

        def worker() -> tuple[bool, str]:
            try:
                self.controller.kwork_sync.client.login_with_credentials(login, pwd)
                return True, f"Вход выполнен: {login}"
            except KworkAuthRequiredError as exc:
                return False, exc.message
            except Exception as exc:
                return False, str(exc)

        def done(res: tuple[bool, str]) -> None:
            ok, msg = res
            if ok:
                self._set_kwork_badge("Активна", SUCCESS_FG, SUCCESS_BG)
            else:
                self._set_kwork_badge("Требуется Cookie", WARNING_FG, WARNING_BG)
            self.controller.show_notification("Авторизация Kwork", msg, error=not ok)
            self.controller.top_bar.refresh_status()

        self.controller.run_in_background(worker, on_done=done)

    def on_check_kwork_session(self) -> None:
        def worker() -> tuple[bool, str]:
            return self.controller.kwork_sync.client.check_session()

        def done(res: tuple[bool, str]) -> None:
            ok, reason = res
            if ok:
                self._set_kwork_badge("Онлайн", SUCCESS_FG, SUCCESS_BG)
                self.controller.show_notification("Проверка Kwork", "Сессия активна и готова к синхронизации")
            else:
                self._set_kwork_badge(f"Ошибка: {reason}", DANGER_FG, DANGER_BG)
                self.controller.show_notification("Проверка Kwork", f"Сессия недоступна: {reason}", error=True)
            self.controller.top_bar.refresh_status()

        self.controller.run_in_background(worker, on_done=done)

    def on_clear_kwork_session(self) -> None:
        self.controller.kwork_sync.client.clear_session()
        self.kwork_cookie_box.delete("1.0", "end")
        self.kwork_token_entry.delete(0, "end")
        self._set_kwork_badge("Не настроена", NEUTRAL_FG, NEUTRAL_BG)
        self.controller.top_bar.refresh_status()
        self.controller.show_notification("Kwork", "Локальная сессия Kwork очищена")

    def on_run_diagnostics(self) -> None:
        for child in self.doc_results_container.winfo_children():
            child.destroy()
        customtkinter.CTkLabel(
            self.doc_results_container,
            text="Выполняется проверка окружения...",
            text_color=TEXT_SECONDARY,
            font=font_ui(12),
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

                icon = "●" if is_passed else "✕"
                color = SUCCESS_FG if is_passed else DANGER_FG
                customtkinter.CTkLabel(row, text=icon, font=font_mono(12, "bold"), text_color=color).grid(
                    row=0, column=0, padx=(0, 8)
                )

                txt = f"{name}: {msg}"
                if not is_passed and fix:
                    txt += f"  (Решение: {fix})"
                customtkinter.CTkLabel(
                    row,
                    text=txt,
                    text_color=color,
                    font=font_ui(12),
                    wraplength=820,
                    justify="left",
                    anchor="w",
                ).grid(row=0, column=1, sticky="w")

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
        upstream_cfg = cfg_data.setdefault("upstream", {})
        upstream_cfg["edt_url"] = f"http://127.0.0.1:{edt_port}/mcp"

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

        kw_cfg = cfg_data.setdefault("kwork", {})
        kw_cfg["username"] = self.kwork_login_entry.get().strip()

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
