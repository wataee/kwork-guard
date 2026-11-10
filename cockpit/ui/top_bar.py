from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
import customtkinter

from cockpit.ui.theme import (
    ACCENT_BLUE,
    ACCENT_PRIMARY,
    ACCENT_PRIMARY_HOVER,
    BG_ELEVATED,
    BG_MAIN,
    BG_SURFACE,
    BORDER,
    DANGER_BG,
    DANGER_FG,
    NEUTRAL_BG,
    NEUTRAL_FG,
    RADIUS_CONTROL,
    SUCCESS_BG,
    SUCCESS_FG,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    WARNING_BG,
    WARNING_FG,
    create_button,
    font_mono,
    font_ui,
)

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
        super().__init__(
            master,
            height=48,
            corner_radius=0,
            fg_color=BG_SURFACE,
            border_width=0,
            **kwargs,
        )
        self.controller = controller
        self._syncing_kwork = False

        self.grid_columnconfigure(6, weight=1)

        brand_box = customtkinter.CTkFrame(self, fg_color="transparent")
        brand_box.grid(row=0, column=0, padx=(12, 6), pady=8, sticky="w")

        self.title_label = customtkinter.CTkLabel(
            brand_box,
            text="COCKPIT",
            font=font_mono(13, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.title_label.pack(side="left")

        ver_lbl = customtkinter.CTkLabel(
            brand_box,
            text="v3",
            font=font_mono(10, "bold"),
            text_color=ACCENT_PRIMARY,
        )
        ver_lbl.pack(side="left", padx=(4, 0))

        self.edt_badge = customtkinter.CTkLabel(
            self,
            text="EDT :8765 ○",
            font=font_mono(10, "bold"),
            text_color=TEXT_SECONDARY,
            fg_color=BG_ELEVATED,
            corner_radius=RADIUS_CONTROL,
            padx=6,
            pady=3,
        )
        self.edt_badge.grid(row=0, column=1, padx=2, pady=8)

        self.guard_badge = customtkinter.CTkLabel(
            self,
            text="GUARD :8766 ○",
            font=font_mono(10, "bold"),
            text_color=TEXT_SECONDARY,
            fg_color=BG_ELEVATED,
            corner_radius=RADIUS_CONTROL,
            padx=6,
            pady=3,
        )
        self.guard_badge.grid(row=0, column=2, padx=2, pady=8)

        self.agy_badge = customtkinter.CTkLabel(
            self,
            text="AGY :3000 ○",
            font=font_mono(10, "bold"),
            text_color=TEXT_SECONDARY,
            fg_color=BG_ELEVATED,
            corner_radius=RADIUS_CONTROL,
            padx=6,
            pady=3,
        )
        self.agy_badge.grid(row=0, column=3, padx=2, pady=8)

        self.kwork_status_badge = customtkinter.CTkLabel(
            self,
            text="Kwork: Нет",
            font=font_ui(10, "bold"),
            text_color=NEUTRAL_FG,
            fg_color=NEUTRAL_BG,
            corner_radius=RADIUS_CONTROL,
            padx=6,
            pady=3,
        )
        self.kwork_status_badge.grid(row=0, column=4, padx=2, pady=8)

        self.active_badge = customtkinter.CTkLabel(
            self,
            text="В работе: 0",
            font=font_mono(10),
            text_color=TEXT_SECONDARY,
        )
        self.active_badge.grid(row=0, column=5, padx=6, pady=8)

        controls_box = customtkinter.CTkFrame(self, fg_color="transparent")
        controls_box.grid(row=0, column=6, padx=(4, 12), pady=8, sticky="e")

        self.kwork_sync_btn = create_button(
            controls_box,
            text="↻ Kwork",
            variant="primary",
            height=28,
            width=78,
            command=self.on_sync_kwork,
        )
        self.kwork_sync_btn.pack(side="left", padx=(0, 6))

        self.provider_switch = customtkinter.CTkSegmentedButton(
            controls_box,
            values=["Antigravity", "OpenRouter"],
            height=28,
            corner_radius=RADIUS_CONTROL,
            fg_color=BG_MAIN,
            selected_color=BG_ELEVATED,
            selected_hover_color="#23232A",
            unselected_color=BG_MAIN,
            unselected_hover_color=BG_ELEVATED,
            text_color=TEXT_PRIMARY,
            font=font_ui(10),
            command=self.on_switch_provider,
        )
        current_prov = self.controller.guard_runner.get_active_provider()
        self.provider_switch.set("Antigravity" if current_prov == "antigravity" else "OpenRouter")
        self.provider_switch.pack(side="left", padx=(0, 6))

        self.prepare_btn = create_button(
            controls_box,
            text="Подготовить",
            variant="success",
            height=28,
            width=96,
            command=self.on_prepare_workplace,
        )
        self.prepare_btn.pack(side="left", padx=2)

        self.start_btn = create_button(
            controls_box,
            text="Старт",
            variant="blue",
            height=28,
            width=54,
            command=self.on_start_guard,
        )
        self.start_btn.pack(side="left", padx=2)

        self.stop_btn = create_button(
            controls_box,
            text="Стоп",
            variant="secondary",
            height=28,
            width=48,
            command=self.on_stop_guard,
        )
        self.stop_btn.pack(side="left", padx=2)

        self.restart_btn = create_button(
            controls_box,
            text="Рестарт",
            variant="secondary",
            height=28,
            width=64,
            command=self.on_restart_guard,
        )
        self.restart_btn.pack(side="left", padx=(2, 0))

        self.divider = customtkinter.CTkFrame(self, width=10, height=1, fg_color=BORDER, corner_radius=0)
        self.divider.grid(row=1, column=0, columnspan=7, sticky="ew")

        self._polling_active = True
        self._poll_after_id: Any = self.after(600, self.poll_status)

    def set_kwork_sync_state(self, state: str, text: str = "") -> None:
        if not self.winfo_exists():
            return
        if state == "syncing":
            self._syncing_kwork = True
            self.kwork_sync_btn.configure(text="⏳ Синхр...", state="disabled")
            self.kwork_status_badge.configure(
                text=text or "Kwork: Синхронизация...",
                text_color=WARNING_FG,
                fg_color=WARNING_BG,
            )
            return

        self._syncing_kwork = False
        self.kwork_sync_btn.configure(text="↻ Kwork", state="normal")
        if state == "ok":
            self.kwork_status_badge.configure(
                text=text or "Kwork: ОК",
                text_color=SUCCESS_FG,
                fg_color=SUCCESS_BG,
            )
        elif state == "auth_required":
            self.kwork_status_badge.configure(
                text=text or "Kwork: Вход",
                text_color=WARNING_FG,
                fg_color=WARNING_BG,
            )
        elif state == "error":
            self.kwork_status_badge.configure(
                text=text or "Kwork: Ошибка",
                text_color=DANGER_FG,
                fg_color=DANGER_BG,
            )
        else:
            self.kwork_status_badge.configure(
                text=text or "Kwork: Офлайн",
                text_color=NEUTRAL_FG,
                fg_color=NEUTRAL_BG,
            )

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

        has_kwork_session = False
        kwork_last = None
        if hasattr(self.controller, "kwork_sync"):
            has_kwork_session = self.controller.kwork_sync.client.has_session()
            kwork_last = self.controller.kwork_sync.last_sync_result

        return {
            "edt_ok": edt_ok,
            "guard_ok": guard_ok,
            "agy_ok": agy_ok,
            "active_provider": active_prov,
            "active_count": active_count,
            "has_kwork_session": has_kwork_session,
            "kwork_last": kwork_last,
        }

    def _apply_status_snapshot(self, snap: dict[str, Any]) -> None:
        if not self.winfo_exists():
            return
        if snap["edt_ok"]:
            self.edt_badge.configure(text="EDT :8765 ●", text_color=SUCCESS_FG, fg_color=SUCCESS_BG)
        else:
            self.edt_badge.configure(text="EDT :8765 ○", text_color=DANGER_FG, fg_color=DANGER_BG)

        if snap["guard_ok"]:
            self.guard_badge.configure(text="GUARD :8766 ●", text_color=SUCCESS_FG, fg_color=SUCCESS_BG)
        else:
            self.guard_badge.configure(text="GUARD :8766 ○", text_color=DANGER_FG, fg_color=DANGER_BG)

        if snap["agy_ok"]:
            self.agy_badge.configure(text="AGY :3000 ●", text_color=SUCCESS_FG, fg_color=SUCCESS_BG)
        else:
            self.agy_badge.configure(text="AGY :3000 ○", text_color=WARNING_FG, fg_color=WARNING_BG)

        self.active_badge.configure(text=f"В работе: {snap['active_count']}")
        expected_label = "Antigravity" if snap["active_provider"] == "antigravity" else "OpenRouter"
        if self.provider_switch.get() != expected_label:
            self.provider_switch.set(expected_label)

        if not self._syncing_kwork:
            last_res = snap.get("kwork_last")
            if last_res is not None:
                if last_res.ok:
                    self.set_kwork_sync_state("ok", f"Kwork: {last_res.synced_orders_count} зак.")
                else:
                    self.set_kwork_sync_state(last_res.status)
            elif snap.get("has_kwork_session"):
                self.set_kwork_sync_state("ok", "Kwork: Сессия")
            else:
                self.set_kwork_sync_state("neutral", "Kwork: Нет сессии")

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
            if self._polling_active and self.winfo_exists():
                self._poll_after_id = self.after(5000, self.poll_status)

    def refresh_status(self) -> None:
        snap = self._collect_status_snapshot()
        self._apply_status_snapshot(snap)

    def on_sync_kwork(self) -> None:
        if hasattr(self.controller, "trigger_kwork_sync_async"):
            self.controller.trigger_kwork_sync_async(manual=True)

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
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except Exception as exc:
                LOGGER.debug("TopBar after_cancel skipped: %s", exc)
        super().destroy()
