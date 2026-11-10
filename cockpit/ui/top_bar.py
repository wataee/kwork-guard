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

        self.grid_columnconfigure(5, weight=1)

        self.title_label = customtkinter.CTkLabel(
            self,
            text="⚡ COCKPIT 2.1",
            font=customtkinter.CTkFont(size=16, weight="bold"),
            text_color="#38BDF8",
        )
        self.title_label.grid(row=0, column=0, padx=(14, 14), pady=10, sticky="w")

        self.edt_badge = customtkinter.CTkLabel(
            self,
            text="EDT ●",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            text_color="#94A3B8",
        )
        self.edt_badge.grid(row=0, column=1, padx=6, pady=10)

        self.guard_badge = customtkinter.CTkLabel(
            self,
            text="GUARD ●",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            text_color="#94A3B8",
        )
        self.guard_badge.grid(row=0, column=2, padx=6, pady=10)

        self.agy_badge = customtkinter.CTkLabel(
            self,
            text="AGY :3000 ●",
            font=customtkinter.CTkFont(size=12, weight="bold"),
            text_color="#94A3B8",
        )
        self.agy_badge.grid(row=0, column=3, padx=6, pady=10)

        self.active_badge = customtkinter.CTkLabel(
            self,
            text="В работе: 0",
            font=customtkinter.CTkFont(size=12),
            text_color="#E2E8F0",
        )
        self.active_badge.grid(row=0, column=4, padx=10, pady=10)

        provider_box = customtkinter.CTkFrame(self, fg_color="transparent")
        provider_box.grid(row=0, column=5, padx=6, pady=10, sticky="e")

        customtkinter.CTkLabel(
            provider_box,
            text="Provider:",
            font=customtkinter.CTkFont(size=12),
            text_color="#94A3B8",
        ).pack(side="left", padx=(0, 6))

        self.provider_switch = customtkinter.CTkSegmentedButton(
            provider_box,
            values=["Antigravity", "OpenRouter"],
            command=self.on_switch_provider,
        )
        current_prov = self.controller.guard_runner.get_active_provider()
        self.provider_switch.set("Antigravity" if current_prov == "antigravity" else "OpenRouter")
        self.provider_switch.pack(side="left")

        self.prepare_btn = customtkinter.CTkButton(
            self,
            text="▶ Подготовить рабочее место",
            fg_color="#059669",
            hover_color="#047857",
            font=customtkinter.CTkFont(weight="bold"),
            command=self.on_prepare_workplace,
        )
        self.prepare_btn.grid(row=0, column=6, padx=6, pady=10)

        self.start_btn = customtkinter.CTkButton(
            self,
            text="Старт Guard",
            width=90,
            fg_color="#2563EB",
            hover_color="#1D4ED8",
            command=self.on_start_guard,
        )
        self.start_btn.grid(row=0, column=7, padx=3, pady=10)

        self.stop_btn = customtkinter.CTkButton(
            self,
            text="Стоп",
            width=55,
            fg_color="#475569",
            hover_color="#334155",
            command=self.on_stop_guard,
        )
        self.stop_btn.grid(row=0, column=8, padx=3, pady=10)

        self.restart_btn = customtkinter.CTkButton(
            self,
            text="Рестарт",
            width=65,
            fg_color="#D97706",
            hover_color="#B45309",
            command=self.on_restart_guard,
        )
        self.restart_btn.grid(row=0, column=9, padx=(3, 14), pady=10)

        self._polling_active = True
        self.after(600, self.poll_status)

    def _collect_status_snapshot(self) -> dict[str, Any]:
        edt_ok = self.controller.guard_runner.is_edt_running()
        guard_ok = self.controller.guard_runner.is_guard_running()
        agy_ok, _ = self.controller.guard_runner.check_antigravity_status()
        active_prov = self.controller.guard_runner.get_active_provider()
        active_count = 0
        try:
            active_orders = self.controller.orders_facade.list_orders(stage_filter="in_progress")
            active_count = len(active_orders)
        except Exception as error:
            LOGGER.warning("Could not refresh active orders badge: %s", error)

        return {
            "edt_ok": edt_ok,
            "guard_ok": guard_ok,
            "agy_ok": agy_ok,
            "active_provider": active_prov,
            "active_count": active_count,
        }

    def _apply_status_snapshot(self, snap: dict[str, Any]) -> None:
        if not self.winfo_exists():
            return
        if snap["edt_ok"]:
            self.edt_badge.configure(text="EDT :8765 ●", text_color="#10B981")
        else:
            self.edt_badge.configure(text="EDT :8765 ○", text_color="#EF4444")

        if snap["guard_ok"]:
            self.guard_badge.configure(text="GUARD :8766 ●", text_color="#10B981")
        else:
            self.guard_badge.configure(text="GUARD :8766 ○", text_color="#EF4444")

        if snap["agy_ok"]:
            self.agy_badge.configure(text="AGY :3000 ●", text_color="#10B981")
        else:
            self.agy_badge.configure(text="AGY :3000 ○", text_color="#F59E0B")

        self.active_badge.configure(text=f"В работе: {snap['active_count']}")
        expected_label = "Antigravity" if snap["active_provider"] == "antigravity" else "OpenRouter"
        if self.provider_switch.get() != expected_label:
            self.provider_switch.set(expected_label)

    def poll_status(self) -> None:
        if not self._polling_active:
            return
        self.controller.run_in_background(
            self._collect_status_snapshot,
            on_done=self._on_poll_done,
        )

    def _on_poll_done(self, snap: dict[str, Any]) -> None:
        try:
            self._apply_status_snapshot(snap)
        except Exception as error:
            LOGGER.error("Status apply failed: %s", error, exc_info=True)
        finally:
            if self._polling_active:
                self.after(5000, self.poll_status)

    def refresh_status(self) -> None:
        snap = self._collect_status_snapshot()
        self._apply_status_snapshot(snap)

    def on_switch_provider(self, selected: str) -> None:
        target = "antigravity" if selected.lower() == "antigravity" else "openrouter"

        def worker() -> tuple[bool, str]:
            return self.controller.guard_runner.switch_provider(target)

        def done(res: tuple[bool, str]) -> None:
            ok, msg = res
            self.controller.show_notification(
                "Провайдер LLM",
                f"Активный провайдер переключен на: {msg}" if ok else msg,
                error=not ok,
            )
            self.refresh_status()

        self.controller.run_in_background(worker, on_done=done)

    def on_prepare_workplace(self) -> None:
        def worker() -> Any:
            return self.controller.guard_runner.prepare_workplace()

        def done(res: Any) -> None:
            if isinstance(res, tuple):
                success, message = res
            elif isinstance(res, dict):
                success = bool(res.get("guard_running"))
                edt_state = "OK" if res.get("edt_running") else "Ожидание EDT"
                agy_state = "OK" if res.get("antigravity_online") else "Fallback OpenRouter"
                message = (
                    f"Guard: {res.get('guard_message')}\n"
                    f"EDT MCP: {edt_state}\n"
                    f"Antigravity: {agy_state}\n"
                    f"Обновлено заказов: {res.get('repaired_orders_count', 0)}"
                )
            else:
                success, message = bool(res), "Выполнено"

            if success:
                self.controller.show_notification("Рабочее место", message)
            else:
                self.controller.show_notification("Предупреждение", message, error=True)
            self.refresh_status()

        self.controller.run_in_background(worker, on_done=done)

    def on_start_guard(self) -> None:
        def worker() -> Any:
            return self.controller.guard_runner.start_guard_background()

        def done(res: Any) -> None:
            ok = res[0] if isinstance(res, tuple) else bool(res)
            msg = res[1] if isinstance(res, tuple) and len(res) > 1 else ("Guard запущен" if ok else "Не удалось запустить Guard")
            self.controller.show_notification("Guard", msg, error=not ok)
            self.refresh_status()

        self.controller.run_in_background(worker, on_done=done)

    def on_stop_guard(self) -> None:
        def worker() -> Any:
            return self.controller.guard_runner.stop_guard()

        def done(res: Any) -> None:
            ok = res[0] if isinstance(res, tuple) else bool(res)
            self.controller.show_notification(
                "Guard",
                "Guard остановлен" if ok else "Guard уже был остановлен",
            )
            self.refresh_status()

        self.controller.run_in_background(worker, on_done=done)

    def on_restart_guard(self) -> None:
        def worker() -> Any:
            return self.controller.guard_runner.restart_guard()

        def done(res: Any) -> None:
            ok = res[0] if isinstance(res, tuple) else bool(res)
            self.controller.show_notification(
                "Guard",
                "Guard перезапущен" if ok else "Ошибка перезапуска Guard",
                error=not ok,
            )
            self.refresh_status()

        self.controller.run_in_background(worker, on_done=done)

    def destroy(self) -> None:
        self._polling_active = False
        super().destroy()
