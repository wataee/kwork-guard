from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import customtkinter

LOGGER = logging.getLogger("cockpit.top_bar")
if not LOGGER.handlers:
    _log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handler = RotatingFileHandler(_log_dir / "cockpit.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)


class TopBar(customtkinter.CTkFrame):
    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, height=54, corner_radius=0, **kwargs)
        self.controller = controller

        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=0)
        self.grid_columnconfigure(2, weight=0)
        self.grid_columnconfigure(3, weight=0)
        self.grid_columnconfigure(4, weight=1)
        self.grid_columnconfigure(5, weight=0)
        self.grid_columnconfigure(6, weight=0)
        self.grid_columnconfigure(7, weight=0)
        self.grid_columnconfigure(8, weight=0)

        self.title_label = customtkinter.CTkLabel(
            self,
            text="⚡ COCKPIT 2.0",
            font=customtkinter.CTkFont(size=16, weight="bold"),
            text_color="#38BDF8",
        )
        self.title_label.grid(row=0, column=0, padx=(16, 20), pady=10, sticky="w")

        self.edt_badge = customtkinter.CTkLabel(
            self,
            text="EDT ●",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            text_color="#94A3B8",
        )
        self.edt_badge.grid(row=0, column=1, padx=8, pady=10)

        self.guard_badge = customtkinter.CTkLabel(
            self,
            text="GUARD ●",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            text_color="#94A3B8",
        )
        self.guard_badge.grid(row=0, column=2, padx=8, pady=10)

        self.active_badge = customtkinter.CTkLabel(
            self,
            text="В работе: 0",
            font=customtkinter.CTkFont(size=12),
            text_color="#E2E8F0",
        )
        self.active_badge.grid(row=0, column=3, padx=12, pady=10)

        self.prepare_btn = customtkinter.CTkButton(
            self,
            text="▶ Подготовить рабочее место",
            fg_color="#059669",
            hover_color="#047857",
            font=customtkinter.CTkFont(weight="bold"),
            command=self.on_prepare_workplace,
        )
        self.prepare_btn.grid(row=0, column=5, padx=6, pady=10)

        self.start_btn = customtkinter.CTkButton(
            self,
            text="Старт Guard",
            width=90,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            command=self.on_start_guard,
        )
        self.start_btn.grid(row=0, column=6, padx=4, pady=10)

        self.stop_btn = customtkinter.CTkButton(
            self,
            text="Стоп",
            width=60,
            fg_color="#475569",
            hover_color="#334155",
            command=self.on_stop_guard,
        )
        self.stop_btn.grid(row=0, column=7, padx=4, pady=10)

        self.restart_btn = customtkinter.CTkButton(
            self,
            text="Рестарт",
            width=70,
            fg_color="#D97706",
            hover_color="#B45309",
            command=self.on_restart_guard,
        )
        self.restart_btn.grid(row=0, column=8, padx=(4, 16), pady=10)

        self._polling_active = True
        self.after(500, self.poll_status)

    def poll_status(self) -> None:
        if not self._polling_active:
            return
        try:
            self.refresh_status()
        except Exception as error:
            LOGGER.error("Status poll failed: %s", error, exc_info=True)
        finally:
            self.after(5000, self.poll_status)

    def refresh_status(self) -> None:
        edt_ok = self.controller.guard_runner.is_edt_running()
        guard_ok = self.controller.guard_runner.is_guard_running()

        if edt_ok:
            self.edt_badge.configure(text="EDT :8765 ●", text_color="#10B981")
        else:
            self.edt_badge.configure(text="EDT :8765 ○", text_color="#EF4444")

        if guard_ok:
            self.guard_badge.configure(text="GUARD :8766 ●", text_color="#10B981")
        else:
            self.guard_badge.configure(text="GUARD :8766 ○", text_color="#EF4444")

        try:
            active_orders = self.controller.orders_facade.list_orders(stage_filter="in_progress")
            self.active_badge.configure(text=f"В работе: {len(active_orders)}")
        except Exception as error:
            LOGGER.warning("Could not refresh active orders badge: %s", error)

    def on_prepare_workplace(self) -> None:
        try:
            success, message = self.controller.guard_runner.prepare_workplace()
            if success:
                self.controller.show_notification("Рабочее место", message)
            else:
                self.controller.show_notification("Предупреждение", message, error=True)
            self.refresh_status()
        except Exception as error:
            LOGGER.error("Prepare workplace error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка", str(error), error=True)

    def on_start_guard(self) -> None:
        try:
            ok = self.controller.guard_runner.start_guard_background()
            self.controller.show_notification(
                "Guard",
                "Guard запущен" if ok else "Не удалось запустить Guard",
                error=not ok,
            )
            self.refresh_status()
        except Exception as error:
            LOGGER.error("Start guard error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка", str(error), error=True)

    def on_stop_guard(self) -> None:
        try:
            ok = self.controller.guard_runner.stop_guard()
            self.controller.show_notification(
                "Guard",
                "Guard остановлен" if ok else "Guard уже был остановлен",
            )
            self.refresh_status()
        except Exception as error:
            LOGGER.error("Stop guard error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка", str(error), error=True)

    def on_restart_guard(self) -> None:
        try:
            ok = self.controller.guard_runner.restart_guard()
            self.controller.show_notification(
                "Guard",
                "Guard перезапущен" if ok else "Ошибка перезапуска Guard",
                error=not ok,
            )
            self.refresh_status()
        except Exception as error:
            LOGGER.error("Restart guard error: %s", error, exc_info=True)
            self.controller.show_notification("Ошибка", str(error), error=True)

    def destroy(self) -> None:
        self._polling_active = False
        super().destroy()
