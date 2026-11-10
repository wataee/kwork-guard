from __future__ import annotations

import atexit
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from typing import Any
import customtkinter

from cockpit.services.guard_runner import GuardRunnerService
from cockpit.services.intake_parser import IntakeParserService
from cockpit.services.onec_runner import OneCRunnerService
from cockpit.services.orders_facade import OrdersFacade
from cockpit.services.prompt_builder import PromptBuilderService
from cockpit.services.system_doctor import SystemDoctorService
from cockpit.services.zip_packager import DeliveryPackagerService
from cockpit.ui.top_bar import TopBar
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

        customtkinter.set_appearance_mode("Dark")
        customtkinter.set_default_color_theme("blue")

        self.title("Kwork Cockpit 2.0 (Zero-Defect Edition)")
        self.geometry("1280x840")
        self.minsize(1024, 680)

        icon_path = self.base_dir / "assets" / "cockpit.ico"
        if icon_path.exists():
            try:
                self.iconbitmap(str(icon_path))
            except Exception as error:
                LOGGER.warning("Could not set iconbitmap: %s", error)

        self.guard_runner = GuardRunnerService(self.base_dir)
        self.orders_facade = OrdersFacade(self.base_dir)
        self.intake_parser = IntakeParserService()
        self.prompt_builder = PromptBuilderService(self.base_dir)
        self.packager = DeliveryPackagerService(self.base_dir)
        self.onec_runner = OneCRunnerService(self.base_dir)
        self.system_doctor = SystemDoctorService(self.base_dir)

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.top_bar = TopBar(self, controller=self)
        self.top_bar.grid(row=0, column=0, columnspan=2, sticky="ew")

        self.sidebar = customtkinter.CTkFrame(self, width=210, corner_radius=0)
        self.sidebar.grid(row=1, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(7, weight=1)

        self.nav_buttons: dict[str, customtkinter.CTkButton] = {}
        self._build_sidebar_buttons()

        self.content_container = customtkinter.CTkFrame(self, fg_color="transparent")
        self.content_container.grid(row=1, column=1, sticky="nsew")
        self.content_container.grid_rowconfigure(0, weight=1)
        self.content_container.grid_columnconfigure(0, weight=1)

        self.views: dict[str, Any] = {
            "today": ViewToday(self.content_container, controller=self),
            "orders": ViewOrders(self.content_container, controller=self),
            "new_order": ViewNewOrder(self.content_container, controller=self),
            "order_detail": ViewOrderDetail(self.content_container, controller=self),
            "finance": ViewFinance(self.content_container, controller=self),
            "tools": ViewTools(self.content_container, controller=self),
            "settings": ViewSettings(self.content_container, controller=self),
        }

        for view in self.views.values():
            view.grid(row=0, column=0, sticky="nsew")

        self.bind_all("<Control-k>", lambda e: self.show_view("orders"))
        self.bind_all("<Control-n>", lambda e: self.show_view("new_order"))
        self.bind_all("<F5>", lambda e: self.refresh_active_view())

        self.current_view_name = "today"
        self.show_view("today")

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

    def _build_sidebar_buttons(self) -> None:
        items = [
            ("today", "📅 Сегодня"),
            ("orders", "📋 Все заказы"),
            ("new_order", "➕ Новый заказ"),
            ("finance", "📊 Финансы"),
            ("tools", "🧰 Инструменты"),
            ("settings", "⚙️ Настройки"),
        ]

        for index, (key, label) in enumerate(items):
            btn = customtkinter.CTkButton(
                self.sidebar,
                text=label,
                anchor="w",
                height=38,
                fg_color="transparent",
                hover_color="#1E293B",
                text_color="#CBD5E1",
                font=customtkinter.CTkFont(size=13, weight="bold"),
                command=lambda k=key: self.show_view(k),
            )
            btn.grid(row=index, column=0, padx=10, pady=4, sticky="ew")
            self.nav_buttons[key] = btn

        version_label = customtkinter.CTkLabel(
            self.sidebar,
            text="v2.0.0 (Zero-Defect)",
            text_color="#64748B",
            font=customtkinter.CTkFont(size=11),
        )
        version_label.grid(row=8, column=0, padx=10, pady=12, sticky="s")

    def show_view(self, view_name: str, **kwargs: Any) -> None:
        target_view = self.views.get(view_name)
        if not target_view:
            LOGGER.error("View %s not found", view_name)
            return

        self.current_view_name = view_name
        target_view.tkraise()

        for key, btn in self.nav_buttons.items():
            if key == view_name:
                btn.configure(fg_color="#2563EB", text_color="#FFFFFF")
            else:
                btn.configure(fg_color="transparent", text_color="#CBD5E1")

        if view_name == "today" and hasattr(target_view, "load_data"):
            target_view.load_data()
        elif view_name == "orders" and hasattr(target_view, "load_orders"):
            target_view.load_orders()
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

    def refresh_active_view(self) -> None:
        self.show_view(self.current_view_name)

    def show_notification(self, title: str, message: str, error: bool = False) -> None:
        dialog = customtkinter.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("420x180")
        dialog.attributes("-topmost", True)
        dialog.grid_columnconfigure(0, weight=1)

        border_color = "#EF4444" if error else "#10B981"
        frame = customtkinter.CTkFrame(dialog, fg_color="#1E293B", border_color=border_color, border_width=2)
        frame.pack(fill="both", expand=True, padx=10, pady=10)

        header_lbl = customtkinter.CTkLabel(
            frame,
            text=f"{'❌ ' if error else '✅ '}{title}",
            font=customtkinter.CTkFont(size=15, weight="bold"),
            text_color=border_color,
        )
        header_lbl.pack(anchor="w", padx=16, pady=(12, 6))

        body_lbl = customtkinter.CTkLabel(
            frame,
            text=message,
            wraplength=380,
            justify="left",
            font=customtkinter.CTkFont(size=12),
        )
        body_lbl.pack(anchor="w", padx=16, pady=4)

        ok_btn = customtkinter.CTkButton(
            frame,
            text="OK",
            width=80,
            command=dialog.destroy,
        )
        ok_btn.pack(side="bottom", anchor="e", padx=16, pady=(4, 12))


def run_cockpit() -> None:
    app = CockpitApp()
    app.mainloop()


if __name__ == "__main__":
    run_cockpit()
