from __future__ import annotations

import datetime
import re
import time
from typing import Any, Optional
import customtkinter

BG_MAIN = "#09090B"
BG_SURFACE = "#121215"
BG_ELEVATED = "#18181C"
BORDER = "#27272A"
BORDER_HOVER = "#3F3F46"

TEXT_PRIMARY = "#FAFAFA"
TEXT_SECONDARY = "#A1A1AA"
TEXT_MUTED = "#52525B"

ACCENT_PRIMARY = "#6366F1"
ACCENT_PRIMARY_HOVER = "#4F46E5"
ACCENT_BLUE = "#3B82F6"
ACCENT_BLUE_HOVER = "#2563EB"
ACCENT_INDIGO_BG = "#1E1B4B"
ACCENT_INDIGO_FG = "#A5B4FC"

DANGER_FG = "#F87171"
DANGER_BG = "#2A1215"
WARNING_FG = "#FBBF24"
WARNING_BG = "#291F0F"
SUCCESS_FG = "#34D399"
SUCCESS_BG = "#0D281E"
NEUTRAL_FG = "#A1A1AA"
NEUTRAL_BG = "#18181C"

RADIUS_CARD = 8
RADIUS_CONTROL = 6
UI_FONT_FAMILY = "Segoe UI Variable"
MONO_FONT_FAMILY = "Cascadia Code"

STAGE_STYLES: dict[str, tuple[str, str, str]] = {
    "lead": ("Новый", ACCENT_INDIGO_FG, ACCENT_INDIGO_BG),
    "intake": ("Уточнение ТЗ", ACCENT_INDIGO_FG, ACCENT_INDIGO_BG),
    "in_progress": ("В работе", WARNING_FG, WARNING_BG),
    "development": ("В разработке", WARNING_FG, WARNING_BG),
    "testing": ("Тестирование", ACCENT_INDIGO_FG, ACCENT_INDIGO_BG),
    "review": ("На проверке", WARNING_FG, WARNING_BG),
    "delivered": ("Сдан", SUCCESS_FG, SUCCESS_BG),
    "warranty": ("Гарантия", SUCCESS_FG, SUCCESS_BG),
    "cancelled": ("Отменён", NEUTRAL_FG, NEUTRAL_BG),
    "archive": ("Архив", NEUTRAL_FG, NEUTRAL_BG),
}


def font_ui(size: int = 13, weight: str = "normal") -> customtkinter.CTkFont:
    return customtkinter.CTkFont(family=UI_FONT_FAMILY, size=size, weight=weight)


def font_mono(size: int = 12, weight: str = "normal") -> customtkinter.CTkFont:
    return customtkinter.CTkFont(family=MONO_FONT_FAMILY, size=size, weight=weight)


def parse_deadline_timestamp(deadline_ts: Optional[int], deadline_str: str = "") -> int:
    if deadline_ts and int(deadline_ts) > 0:
        return int(deadline_ts)
    raw = (deadline_str or "").strip()
    if not raw or raw == "—":
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            dt = datetime.datetime.strptime(raw, fmt)
            if fmt in ("%Y-%m-%d", "%d.%m.%Y"):
                dt = dt.replace(hour=23, minute=59)
            return int(dt.timestamp())
        except ValueError:
            continue
    rel_days = re.search(r"(\d+)\s*(?:дн|д|дня|дней)", raw.lower())
    if rel_days:
        return int(time.time()) + int(rel_days.group(1)) * 86400
    return 0


def format_deadline_countdown(
    deadline_ts: Optional[int],
    deadline_str: str = "",
    now_ts: Optional[int] = None,
) -> tuple[str, str, str, int]:
    now = int(now_ts if now_ts is not None else time.time())
    ts = parse_deadline_timestamp(deadline_ts, deadline_str)
    if ts <= 0:
        text = deadline_str.strip() if deadline_str and deadline_str.strip() != "—" else "Без срока"
        return text, NEUTRAL_FG, NEUTRAL_BG, 10**10

    remaining = ts - now
    if remaining <= 0:
        over = abs(remaining)
        hours = over // 3600
        mins = (over % 3600) // 60
        label = f"Просрочен {hours}ч {mins}м" if hours > 0 else f"Просрочен {mins}м"
        return label, DANGER_FG, DANGER_BG, remaining

    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    mins = (remaining % 3600) // 60
    if remaining < 6 * 3600:
        return f"Осталось {hours}ч {mins:02d}м", DANGER_FG, DANGER_BG, remaining
    if remaining < 24 * 3600:
        return f"Осталось {hours}ч {mins:02d}м", WARNING_FG, WARNING_BG, remaining
    return f"Осталось {days}д {hours}ч", SUCCESS_FG, SUCCESS_BG, remaining


def format_relative_time(ts: int, now_ts: Optional[int] = None) -> str:
    if ts <= 0:
        return "—"
    now = int(now_ts if now_ts is not None else time.time())
    diff = now - ts
    if diff < 60:
        return "только что"
    if diff < 3600:
        return f"{diff // 60}м назад"
    if diff < 86400:
        return f"{diff // 3600}ч назад"
    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%d.%m %H:%M")


def create_card(
    master: Any,
    fg_color: str = BG_SURFACE,
    border_color: str = BORDER,
    corner_radius: int = RADIUS_CARD,
    **kwargs: Any,
) -> customtkinter.CTkFrame:
    return customtkinter.CTkFrame(
        master,
        fg_color=fg_color,
        border_color=border_color,
        border_width=1,
        corner_radius=corner_radius,
        **kwargs,
    )


def create_badge(
    master: Any,
    text: str,
    fg_color: str = NEUTRAL_BG,
    text_color: str = NEUTRAL_FG,
    mono: bool = False,
    font_size: int = 11,
    padx: int = 8,
    pady: int = 3,
    **kwargs: Any,
) -> customtkinter.CTkFrame:
    pill = customtkinter.CTkFrame(
        master,
        fg_color=fg_color,
        corner_radius=RADIUS_CONTROL,
        **kwargs,
    )
    lbl = customtkinter.CTkLabel(
        pill,
        text=text,
        text_color=text_color,
        font=font_mono(font_size, "bold") if mono else font_ui(font_size, "bold"),
    )
    lbl.pack(padx=padx, pady=pady)
    pill.text_label = lbl
    return pill


def create_button(
    master: Any,
    text: str,
    variant: str = "secondary",
    height: int = 32,
    **kwargs: Any,
) -> customtkinter.CTkButton:
    styles: dict[str, dict[str, Any]] = {
        "primary": {
            "fg_color": ACCENT_PRIMARY,
            "hover_color": ACCENT_PRIMARY_HOVER,
            "text_color": "#FFFFFF",
            "border_width": 0,
        },
        "blue": {
            "fg_color": ACCENT_BLUE,
            "hover_color": ACCENT_BLUE_HOVER,
            "text_color": "#FFFFFF",
            "border_width": 0,
        },
        "success": {
            "fg_color": SUCCESS_BG,
            "hover_color": "#113B2B",
            "text_color": SUCCESS_FG,
            "border_width": 1,
            "border_color": "#14532D",
        },
        "danger": {
            "fg_color": DANGER_BG,
            "hover_color": "#3F171B",
            "text_color": DANGER_FG,
            "border_width": 1,
            "border_color": "#7F1D1D",
        },
        "secondary": {
            "fg_color": BG_ELEVATED,
            "hover_color": "#222228",
            "text_color": TEXT_PRIMARY,
            "border_width": 1,
            "border_color": BORDER,
        },
        "ghost": {
            "fg_color": "transparent",
            "hover_color": BG_ELEVATED,
            "text_color": TEXT_SECONDARY,
            "border_width": 0,
        },
    }
    style = dict(styles.get(variant, styles["secondary"]))
    font = kwargs.pop("font", font_ui(12, "bold" if variant in ("primary", "blue") else "normal"))
    style.update(kwargs)
    return customtkinter.CTkButton(
        master,
        text=text,
        height=height,
        corner_radius=RADIUS_CONTROL,
        font=font,
        **style,
    )


def create_entry(
    master: Any,
    placeholder_text: str = "",
    height: int = 34,
    mono: bool = False,
    **kwargs: Any,
) -> customtkinter.CTkEntry:
    return customtkinter.CTkEntry(
        master,
        placeholder_text=placeholder_text,
        height=height,
        corner_radius=RADIUS_CONTROL,
        fg_color=BG_ELEVATED,
        border_color=BORDER,
        border_width=1,
        text_color=TEXT_PRIMARY,
        placeholder_text_color=TEXT_MUTED,
        font=font_mono(12) if mono else font_ui(12),
        **kwargs,
    )


def create_textbox(
    master: Any,
    height: int = 120,
    mono: bool = False,
    **kwargs: Any,
) -> customtkinter.CTkTextbox:
    return customtkinter.CTkTextbox(
        master,
        height=height,
        corner_radius=RADIUS_CONTROL,
        fg_color=BG_ELEVATED,
        border_color=BORDER,
        border_width=1,
        text_color=TEXT_PRIMARY,
        font=font_mono(12) if mono else font_ui(12),
        **kwargs,
    )


def create_option_menu(
    master: Any,
    values: list[str],
    height: int = 32,
    **kwargs: Any,
) -> customtkinter.CTkOptionMenu:
    return customtkinter.CTkOptionMenu(
        master,
        values=values,
        height=height,
        corner_radius=RADIUS_CONTROL,
        fg_color=BG_ELEVATED,
        button_color="#23232A",
        button_hover_color="#2D2D36",
        dropdown_fg_color=BG_SURFACE,
        dropdown_hover_color=BG_ELEVATED,
        text_color=TEXT_PRIMARY,
        font=font_ui(12),
        **kwargs,
    )


def bind_card_hover(card: customtkinter.CTkFrame, on_click: Any = None) -> None:
    def on_enter(event: Any) -> None:
        if card.winfo_exists():
            card.configure(fg_color=BG_ELEVATED, border_color=BORDER_HOVER)

    def on_leave(event: Any) -> None:
        if card.winfo_exists():
            card.configure(fg_color=BG_SURFACE, border_color=BORDER)

    card.bind("<Enter>", on_enter)
    card.bind("<Leave>", on_leave)
    if on_click is not None:
        card.bind("<Button-1>", lambda e: on_click())
        for child in card.winfo_children():
            child.bind("<Button-1>", lambda e: on_click())
