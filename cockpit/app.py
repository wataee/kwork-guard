from __future__ import annotations

import atexit
from concurrent.futures import Future, ThreadPoolExecutor
import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
from typing import Any, Callable, Optional
import customtkinter

from cockpit.services.guard_runner import GuardRunnerService
from cockpit.services.intake_parser import IntakeParserService
from cockpit.services.kwork_sync import KworkSyncService, SyncResult
from cockpit.services.onec_runner import OneCRunnerService
from cockpit.services.orders_facade import OrdersFacade
from cockpit.services.prompt_builder import PromptBuilderService
from cockpit.services.system_doctor import SystemDoctorService
from cockpit.services.zip_packager import DeliveryPackagerService
from cockpit.ui.theme import (
    ACCENT_PRIMARY,
    BG_ELEVATED,
    BG_MAIN,
    BG_SURFACE,
    BORDER,
    DANGER_FG,
    SUCCESS_FG,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    create_button,
    font_mono,
    font_ui,
)
from cockpit.ui.top_bar import TopBar
from cockpit.ui.view_chats import ViewChats
from cockpit.ui.view_finance import ViewFinance
from cockpit.ui.view_new_order import ViewNewOrder
from cockpit.ui.view_order_detail import ViewOrderDetail
from cockpit.ui.view_orders import ViewOrders
from cockpit.ui.view_settings import ViewSettings
from cockpit.ui.view_today import ViewToday
from cockpit.ui.view_tools import ViewTools

LOGGER = logging.getLogger("cockpit.app")
if not LOGGER.handlers:
    _log_dir = Path(__file__).resolve().parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handler = RotatingFileHandler(_log_dir / "cockpit.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)


def is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        synchronize = 0x00100000
        process_handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if process_handle:
            ctypes.windll.kernel32.CloseHandle(process_handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class CockpitApp(customtkinter.CTk):
    def __init__(self, base_dir: Path | None = None) -> None:
        super().__init__()

        self.base_dir = base_dir or Path(__file__).resolve().parent.parent
        self._enforce_single_instance()
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._active_futures: list[Future[Any]] = []
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._is_closing = False
        self._ui_poll_after_id: Optional[str] = None
        self._startup_sync_after_id: Optional[str] = None

        customtkinter.set_appearance_mode("Dark")
        customtkinter.set_default_color_theme("dark-blue")
        self.configure(fg_color=BG_MAIN)

        self.title("Kwork Cockpit — 1C:EDT & Antigravity Orchestrator")
        self.geometry("1360x860")
        self.minsize(1060, 700)

        icon_path = self.base_dir / "assets" / "cockpit.ico"
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except Exception as error:
                LOGGER.warning("Could not set iconbitmap: %s", error)

        self.guard_runner = GuardRunnerService(self.base_dir)
        self.orders_facade = OrdersFacade(base_dir=self.base_dir)
        self.intake_parser = IntakeParserService()
        self.prompt_builder = PromptBuilderService(self.base_dir)
        self.packager = DeliveryPackagerService(base_dir=self.base_dir)
        self.onec_runner = OneCRunnerService(self.base_dir)
        self.system_doctor = SystemDoctorService(self.base_dir)
        self.kwork_sync = KworkSyncService(
            base_dir=self.base_dir,
            orders_dir=self.orders_facade.orders_dir,
            intake_parser=self.intake_parser,
        )

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.top_bar = TopBar(self, controller=self)
        self.top_bar.grid(row=0, column=0, sticky="ew")

        self.nav_bar = customtkinter.CTkFrame(self, height=40, corner_radius=0, fg_color=BG_MAIN)
        self.nav_bar.grid(row=1, column=0, sticky="ew")
        self.nav_bar.grid_columnconfigure(10, weight=1)

        self.nav_buttons: dict[str, customtkinter.CTkButton] = {}
        self.nav_indicators: dict[str, customtkinter.CTkFrame] = {}
        self._build_nav_tabs()

        nav_divider = customtkinter.CTkFrame(self, width=10, height=1, corner_radius=0, fg_color=BORDER)
        nav_divider.grid(row=1, column=0, sticky="sew")

        self.content_container = customtkinter.CTkFrame(self, fg_color=BG_MAIN, corner_radius=0)
        self.content_container.grid(row=2, column=0, sticky="nsew")
        self.content_container.grid_rowconfigure(0, weight=1)
        self.content_container.grid_columnconfigure(0, weight=1)

        self.views: dict[str, Any] = {
            "today": ViewToday(self.content_container, controller=self, fg_color=BG_MAIN),
            "orders": ViewOrders(self.content_container, controller=self, fg_color=BG_MAIN),
            "chats": ViewChats(self.content_container, controller=self, fg_color=BG_MAIN),
            "new_order": ViewNewOrder(self.content_container, controller=self, fg_color=BG_MAIN),
            "order_detail": ViewOrderDetail(self.content_container, controller=self, fg_color=BG_MAIN),
            "finance": ViewFinance(self.content_container, controller=self, fg_color=BG_MAIN),
            "tools": ViewTools(self.content_container, controller=self, fg_color=BG_MAIN),
            "settings": ViewSettings(self.content_container, controller=self, fg_color=BG_MAIN),
        }

        for view in self.views.values():
            view.grid(row=0, column=0, sticky="nsew")

        self.bind_all("<Control-k>", lambda e: self.show_view("orders"))
        self.bind_all("<Control-n>", lambda e: self.show_view("new_order"))
        self.bind_all("<F5>", lambda e: self.refresh_active_view())

        self.current_view_name = "orders"
        self.show_view("orders")
        self._refresh_unread_chats_badge()
        self._ui_poll_after_id = self.after(25, self._drain_ui_queue_periodic)
        self._startup_sync_after_id = self.after(700, self._startup_kwork_sync)

    def _drain_ui_queue(self) -> None:
        if self._is_closing:
            return
        while True:
            try:
                cb = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                if not self._is_closing and self.winfo_exists():
                    cb()
            except Exception as ui_err:
                LOGGER.debug("Queued UI callback error: %s", ui_err)

    def _drain_ui_queue_periodic(self) -> None:
        self._drain_ui_queue()
        if not self._is_closing and self.winfo_exists():
            self._ui_poll_after_id = self.after(25, self._drain_ui_queue_periodic)

    def _startup_kwork_sync(self) -> None:
        if not self._is_closing and self.kwork_sync.client.has_session():
            self.trigger_kwork_sync_async(manual=False)

    def trigger_kwork_sync_async(self, manual: bool = False) -> None:
        if self._is_closing:
            return
        self.top_bar.set_kwork_sync_state("syncing")

        def worker() -> SyncResult:
            return self.kwork_sync.sync_all()

        def done(res: SyncResult) -> None:
            if not self.winfo_exists():
                return
            if res.ok:
                self.top_bar.set_kwork_sync_state("ok", f"Kwork: {res.synced_orders_count} зак.")
            else:
                self.top_bar.set_kwork_sync_state(res.status)
            self._refresh_unread_chats_badge()
            if self.current_view_name in ("orders", "today", "chats", "order_detail"):
                self.refresh_active_view()
            self.top_bar.refresh_status()
            if manual:
                self.show_notification(
                    "Синхронизация Kwork" if res.ok else "Kwork",
                    res.message,
                    error=not res.ok,
                )

        def on_err(exc: Exception) -> None:
            if self.winfo_exists():
                self.top_bar.set_kwork_sync_state("error")
                if manual:
                    self.show_notification("Ошибка Kwork", str(exc), error=True)

        self.run_in_background(worker, on_done=done, on_error=on_err)

    def _refresh_unread_chats_badge(self) -> None:
        try:
            dialogs = self.kwork_sync.list_recent_dialogs()
            unread_total = sum(int(d.get("unread_count") or 0) for d in dialogs)
            btn = self.nav_buttons.get("chats")
            if btn is not None and btn.winfo_exists():
                label = f"Переписки (7д) · {unread_total}" if unread_total > 0 else "Переписки (7д)"
                btn.configure(text=label)
        except Exception as exc:
            LOGGER.debug("Unread badge update skipped: %s", exc)

    def _enforce_single_instance(self) -> None:
        state_dir = self.base_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file = state_dir / "cockpit.lock"

        if self.lock_file.exists():
            try:
                old_pid_text = self.lock_file.read_text(encoding="utf-8").strip()
                if old_pid_text:
                    old_pid = int(old_pid_text)
                    if is_pid_alive(old_pid):
                        LOGGER.warning("Cockpit is already running with PID %d", old_pid)
            except Exception as error:
                LOGGER.error("Error reading lock file: %s", error)

        try:
            self.lock_file.write_text(str(os.getpid()), encoding="utf-8")
            atexit.register(self._cleanup_lock)
        except Exception as error:
            LOGGER.error("Failed to write lock file: %s", error)

    def _cleanup_lock(self) -> None:
        try:
            if hasattr(self, "lock_file") and self.lock_file.exists():
                self.lock_file.unlink()
        except Exception as error:
            LOGGER.error("Failed to remove lock file: %s", error)

    def _build_nav_tabs(self) -> None:
        items = [
            ("today", "Сегодня"),
            ("orders", "Заказы"),
            ("chats", "Переписки (7д)"),
            ("new_order", "+ Новый заказ"),
            ("finance", "Финансы"),
            ("tools", "Инструменты"),
            ("settings", "Настройки"),
        ]

        for index, (key, label) in enumerate(items):
            cell = customtkinter.CTkFrame(self.nav_bar, fg_color="transparent", corner_radius=0)
            cell.grid(row=0, column=index, padx=(10 if index == 0 else 2, 2), pady=0)

            btn = customtkinter.CTkButton(
                cell,
                text=f"  {label}  ",
                width=0,
                height=32,
                corner_radius=6,
                fg_color="transparent",
                hover_color=BG_ELEVATED,
                text_color=TEXT_SECONDARY,
                font=font_ui(12, "bold"),
                command=lambda k=key: self.show_view(k),
            )
            btn.pack(padx=1, pady=(4, 2))
            self.nav_buttons[key] = btn

            indicator = customtkinter.CTkFrame(cell, width=20, height=2, corner_radius=1, fg_color="transparent")
            indicator.pack(fill="x", padx=4)
            self.nav_indicators[key] = indicator

        hotkey_lbl = customtkinter.CTkLabel(
            self.nav_bar,
            text="Ctrl+K Заказы · Ctrl+N Новый · F5 Обновить",
            text_color=TEXT_MUTED,
            font=font_mono(10),
        )
        hotkey_lbl.grid(row=0, column=10, padx=10, sticky="e")

    def run_in_background(
        self,
        worker: Callable[[], Any],
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
    ) -> Future[Any]:
        def task_wrapper() -> Any:
            try:
                res = worker()
                if on_done and not self._is_closing:
                    self._ui_queue.put(lambda val=res: on_done(val))
                return res
            except Exception as exc:
                LOGGER.error("Background task error: %s", exc, exc_info=True)
                if on_error and not self._is_closing:
                    self._ui_queue.put(lambda err=exc: on_error(err))
                return None

        future = self._executor.submit(task_wrapper)
        self._active_futures = [f for f in self._active_futures if not f.done()]
        self._active_futures.append(future)
        return future

    def wait_for_background_tasks(self, timeout: float = 10.0) -> None:
        for fut in list(self._active_futures):
            try:
                fut.result(timeout=timeout)
            except Exception as exc:
                LOGGER.debug("Waiting future finished with exception: %s", exc)
        self._active_futures = [f for f in self._active_futures if not f.done()]
        self._drain_ui_queue()
        try:
            if self.winfo_exists():
                self.update()
        except Exception as exc:
            LOGGER.debug("Update after background tasks skipped: %s", exc)

    def show_view(self, view_name: str, **kwargs: Any) -> None:
        target_view = self.views.get(view_name)
        if not target_view:
            LOGGER.error("View %s not found", view_name)
            return

        self.current_view_name = view_name
        outer_frame = getattr(target_view, "_parent_frame", target_view)
        outer_frame.tkraise()
        target_view.lift()

        active_key = "orders" if view_name == "order_detail" else view_name
        for key, btn in self.nav_buttons.items():
            indicator = self.nav_indicators.get(key)
            if key == active_key:
                btn.configure(fg_color=BG_ELEVATED, text_color=TEXT_PRIMARY)
                if indicator is not None:
                    indicator.configure(fg_color=ACCENT_PRIMARY)
            else:
                btn.configure(fg_color="transparent", text_color=TEXT_SECONDARY)
                if indicator is not None:
                    indicator.configure(fg_color="transparent")

        if view_name == "today" and hasattr(target_view, "load_data"):
            target_view.load_data()
        elif view_name == "orders" and hasattr(target_view, "load_orders"):
            target_view.load_orders()
        elif view_name == "chats" and hasattr(target_view, "load_dialogs"):
            target_view.load_dialogs()
        elif view_name == "finance" and hasattr(target_view, "load_data"):
            target_view.load_data()
        elif view_name == "tools" and hasattr(target_view, "load_data"):
            target_view.load_data()
        elif view_name == "settings" and hasattr(target_view, "load_data"):
            target_view.load_data()
        elif view_name == "order_detail":
            order_id = kwargs.get("order_id")
            if order_id and hasattr(target_view, "load_order"):
                target_view.load_order(order_id)
            elif hasattr(target_view, "current_order_id") and target_view.current_order_id:
                target_view.load_order(target_view.current_order_id)

    def refresh_active_view(self) -> None:
        self.show_view(self.current_view_name)

    def show_notification(self, title: str, message: str, error: bool = False) -> None:
        dialog = customtkinter.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("480x230")
        dialog.minsize(420, 190)
        dialog.configure(fg_color=BG_MAIN)
        dialog.attributes("-topmost", True)
        dialog.grid_columnconfigure(0, weight=1)

        border_color = DANGER_FG if error else SUCCESS_FG
        frame = customtkinter.CTkFrame(
            dialog,
            fg_color=BG_SURFACE,
            border_color=BORDER,
            border_width=1,
            corner_radius=8,
        )
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        header_lbl = customtkinter.CTkLabel(
            frame,
            text=f"{'✕ ' if error else '✓ '}{title}",
            font=font_ui(15, "bold"),
            text_color=border_color,
        )
        header_lbl.pack(anchor="w", padx=16, pady=(12, 6))

        body_box = customtkinter.CTkScrollableFrame(frame, fg_color="transparent", height=85)
        body_box.pack(fill="both", expand=True, padx=12, pady=(0, 6))

        body_lbl = customtkinter.CTkLabel(
            body_box,
            text=message,
            wraplength=410,
            justify="left",
            text_color=TEXT_PRIMARY,
            font=font_ui(12),
        )
        body_lbl.pack(anchor="w", padx=4, pady=2)

        ok_btn = create_button(
            frame,
            text="Закрыть",
            variant="primary",
            width=96,
            command=dialog.destroy,
        )
        ok_btn.pack(side="bottom", pady=(4, 12))

    def destroy(self) -> None:
        self._is_closing = True
        for after_id in (self._ui_poll_after_id, self._startup_sync_after_id):
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except Exception as exc:
                    LOGGER.debug("After cancel skipped: %s", exc)
        self._cleanup_lock()
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception as exc:
            LOGGER.debug("Executor shutdown error: %s", exc)
        super().destroy()
