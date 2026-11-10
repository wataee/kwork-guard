from __future__ import annotations

import datetime
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
from typing import Any, Optional
import customtkinter
from PIL import Image

from cockpit.services.workspace_bridge import prepare_antigravity_workspace
from cockpit.ui.theme import (
    ACCENT_INDIGO_BG,
    ACCENT_INDIGO_FG,
    ACCENT_PRIMARY,
    BG_ELEVATED,
    BG_MAIN,
    BG_SURFACE,
    BORDER,
    BORDER_HOVER,
    NEUTRAL_BG,
    NEUTRAL_FG,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    create_badge,
    create_button,
    create_card,
    create_entry,
    font_mono,
    font_ui,
    format_relative_time,
)

LOGGER = logging.getLogger("cockpit.view_chats")
if not LOGGER.handlers:
    _log_dir = Path(__file__).resolve().parent.parent.parent / "logs"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _handler = RotatingFileHandler(_log_dir / "cockpit.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    LOGGER.addHandler(_handler)
    LOGGER.setLevel(logging.INFO)


class ViewChats(customtkinter.CTkFrame):
    def __init__(self, master: Any, controller: Any, **kwargs: Any) -> None:
        super().__init__(master, **kwargs)
        self.controller = controller
        self._dialogs: list[dict[str, Any]] = []
        self._selected_peer_id: Optional[str] = None
        self._thumb_refs: list[Any] = []

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)

        self.left_pane = create_card(self, fg_color=BG_SURFACE, width=330, corner_radius=8)
        self.left_pane.grid(row=0, column=0, sticky="nsew", padx=(20, 10), pady=14)
        self.left_pane.grid_propagate(False)
        self.left_pane.grid_rowconfigure(2, weight=1)
        self.left_pane.grid_columnconfigure(0, weight=1)

        left_hdr = customtkinter.CTkFrame(self.left_pane, fg_color="transparent")
        left_hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        left_hdr.grid_columnconfigure(0, weight=1)

        customtkinter.CTkLabel(
            left_hdr,
            text="Переписки · 7 дней",
            font=font_ui(15, "bold"),
            text_color=TEXT_PRIMARY,
        ).grid(row=0, column=0, sticky="w")

        self.refresh_btn = create_button(
            left_hdr,
            text="↻",
            variant="secondary",
            height=28,
            width=32,
            command=self.load_dialogs,
        )
        self.refresh_btn.grid(row=0, column=1, sticky="e")

        self.search_entry = create_entry(
            self.left_pane,
            placeholder_text="Фильтр по клиенту или тексту...",
            height=32,
        )
        self.search_entry.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self.search_entry.bind("<KeyRelease>", lambda e: self._render_dialog_list())

        self.dialog_list = customtkinter.CTkScrollableFrame(self.left_pane, fg_color="transparent")
        self.dialog_list.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 10))
        self.dialog_list.grid_columnconfigure(0, weight=1)

        self.right_pane = create_card(self, fg_color=BG_SURFACE, corner_radius=8)
        self.right_pane.grid(row=0, column=1, sticky="nsew", padx=(0, 20), pady=14)
        self.right_pane.grid_rowconfigure(2, weight=1)
        self.right_pane.grid_columnconfigure(0, weight=1)

        self.chat_header = customtkinter.CTkFrame(self.right_pane, fg_color="transparent")
        self.chat_header.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 8))
        self.chat_header.grid_columnconfigure(0, weight=1)

        header_info = customtkinter.CTkFrame(self.chat_header, fg_color="transparent")
        header_info.grid(row=0, column=0, sticky="w")

        self.peer_title_lbl = customtkinter.CTkLabel(
            header_info,
            text="Выберите диалог слева",
            font=font_ui(16, "bold"),
            text_color=TEXT_PRIMARY,
        )
        self.peer_title_lbl.pack(anchor="w")

        self.peer_sub_lbl = customtkinter.CTkLabel(
            header_info,
            text="Диалоги старше 7 дней скрываются автоматически",
            font=font_ui(12),
            text_color=TEXT_SECONDARY,
        )
        self.peer_sub_lbl.pack(anchor="w")

        self.take_work_btn = create_button(
            self.chat_header,
            text="Взять в разработку (Создать Workspace)",
            variant="primary",
            height=34,
            command=self.on_create_workspace_from_chat,
        )
        self.take_work_btn.grid(row=0, column=1, sticky="e")
        self.take_work_btn.configure(state="disabled")

        hdr_div = customtkinter.CTkFrame(self.right_pane, width=10, height=1, fg_color=BORDER)
        hdr_div.grid(row=1, column=0, sticky="ew")

        self.messages_scroll = customtkinter.CTkScrollableFrame(self.right_pane, fg_color=BG_MAIN)
        self.messages_scroll.grid(row=2, column=0, sticky="nsew", padx=12, pady=10)
        self.messages_scroll.grid_columnconfigure(0, weight=1)

        self.attachments_panel = create_card(self.right_pane, fg_color=BG_ELEVATED, corner_radius=6)
        self.attachments_panel.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
        self.attachments_panel.grid_columnconfigure(0, weight=1)

        self.attachments_title = customtkinter.CTkLabel(
            self.attachments_panel,
            text="Вложения и скриншоты диалога",
            font=font_ui(12, "bold"),
            text_color=TEXT_SECONDARY,
        )
        self.attachments_title.grid(row=0, column=0, sticky="w", padx=12, pady=(8, 4))

        self.attachments_strip = customtkinter.CTkScrollableFrame(
            self.attachments_panel,
            orientation="horizontal",
            height=96,
            fg_color="transparent",
        )
        self.attachments_strip.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

    def load_dialogs(self) -> None:
        try:
            self._dialogs = self.controller.kwork_sync.list_recent_dialogs()
            self._render_dialog_list()
            if self._selected_peer_id:
                still_exists = any(str(d.get("peer_id")) == self._selected_peer_id for d in self._dialogs)
                if still_exists:
                    self.select_dialog(self._selected_peer_id)
                else:
                    self._selected_peer_id = None
                    self._clear_detail()
        except Exception as error:
            LOGGER.error("Failed to load chats: %s", error, exc_info=True)

    def _clear_detail(self) -> None:
        self.peer_title_lbl.configure(text="Выберите диалог слева")
        self.peer_sub_lbl.configure(text="Диалоги старше 7 дней скрываются автоматически")
        self.take_work_btn.configure(state="disabled")
        for child in self.messages_scroll.winfo_children():
            child.destroy()
        for child in self.attachments_strip.winfo_children():
            child.destroy()
        self._thumb_refs.clear()

    def _render_dialog_list(self) -> None:
        for child in self.dialog_list.winfo_children():
            child.destroy()

        query = self.search_entry.get().strip().lower()
        filtered = []
        for dlg in self._dialogs:
            if query:
                hay = f"{dlg.get('peer_name', '')} {dlg.get('last_message_text', '')}".lower()
                if query not in hay:
                    continue
            filtered.append(dlg)

        if not filtered:
            customtkinter.CTkLabel(
                self.dialog_list,
                text="Нет активных переписок\nза последние 7 дней.",
                text_color=TEXT_SECONDARY,
                font=font_ui(12),
                justify="center",
            ).grid(row=0, column=0, pady=(24, 10))
            if hasattr(self.controller, "kwork_sync") and not self.controller.kwork_sync.client.has_session():
                create_button(
                    self.dialog_list,
                    text="Настроить вход Kwork →",
                    variant="primary",
                    height=30,
                    command=lambda: self.controller.show_view("settings"),
                ).grid(row=1, column=0, pady=4)
            return

        for idx, dlg in enumerate(filtered):
            peer_id = str(dlg.get("peer_id") or "")
            is_selected = peer_id == self._selected_peer_id
            card = create_card(
                self.dialog_list,
                fg_color=BG_ELEVATED if is_selected else BG_SURFACE,
                border_color=ACCENT_PRIMARY if is_selected else BORDER,
                corner_radius=6,
            )
            card.grid(row=idx, column=0, sticky="ew", pady=3)
            card.grid_columnconfigure(1, weight=1)

            peer_name = str(dlg.get("peer_name") or peer_id)
            initial = peer_name[:1].upper() or "?"
            avatar = customtkinter.CTkLabel(
                card,
                text=initial,
                width=32,
                height=32,
                corner_radius=6,
                fg_color=ACCENT_INDIGO_BG,
                text_color=ACCENT_INDIGO_FG,
                font=font_ui(13, "bold"),
            )
            avatar.grid(row=0, column=0, rowspan=2, padx=(10, 8), pady=10, sticky="n")

            top_meta = customtkinter.CTkFrame(card, fg_color="transparent")
            top_meta.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(8, 0))
            top_meta.grid_columnconfigure(0, weight=1)

            name_lbl = customtkinter.CTkLabel(
                top_meta,
                text=peer_name,
                font=font_ui(13, "bold"),
                text_color=TEXT_PRIMARY,
            )
            name_lbl.grid(row=0, column=0, sticky="w")

            right_meta = customtkinter.CTkFrame(top_meta, fg_color="transparent")
            right_meta.grid(row=0, column=1, sticky="e")

            if dlg.get("has_attachments"):
                customtkinter.CTkLabel(
                    right_meta,
                    text="📎",
                    font=font_ui(11),
                    text_color=TEXT_SECONDARY,
                ).pack(side="left", padx=(0, 4))

            unread = int(dlg.get("unread_count") or 0)
            if unread > 0:
                dot = customtkinter.CTkLabel(
                    right_meta,
                    text=str(unread),
                    width=18,
                    height=18,
                    corner_radius=9,
                    fg_color=ACCENT_PRIMARY,
                    text_color="#FFFFFF",
                    font=font_mono(10, "bold"),
                )
                dot.pack(side="left", padx=(0, 6))

            ts_lbl = customtkinter.CTkLabel(
                right_meta,
                text=format_relative_time(int(dlg.get("last_message_at") or 0)),
                font=font_mono(10),
                text_color=TEXT_MUTED,
            )
            ts_lbl.pack(side="left")

            preview = str(dlg.get("last_message_text") or "").replace("\n", " ")[:68]
            preview_lbl = customtkinter.CTkLabel(
                card,
                text=preview or "—",
                font=font_ui(11),
                text_color=TEXT_SECONDARY,
                anchor="w",
            )
            preview_lbl.grid(row=1, column=1, sticky="ew", padx=(0, 10), pady=(2, 8))

            def bind_card_clicks(widget: Any, pid: str = peer_id) -> None:
                widget.bind("<Button-1>", lambda e: self.select_dialog(pid))
                for child in widget.winfo_children():
                    bind_card_clicks(child, pid)

            bind_card_clicks(card)

    def select_dialog(self, peer_id: str) -> None:
        self._selected_peer_id = peer_id
        self._render_dialog_list()
        detail = self.controller.kwork_sync.get_dialog_detail(peer_id)
        if not detail:
            return

        peer_name = str(detail.get("peer_name") or peer_id)
        messages = detail.get("messages") or []
        self.peer_title_lbl.configure(text=f"@{peer_name}")
        self.peer_sub_lbl.configure(text=f"Сообщений: {len(messages)}  ·  {detail.get('kwork_url') or ''}")
        self.take_work_btn.configure(state="normal")

        for child in self.messages_scroll.winfo_children():
            child.destroy()

        if not messages:
            customtkinter.CTkLabel(
                self.messages_scroll,
                text=str(detail.get("last_message_text") or "Нет загруженных сообщений."),
                text_color=TEXT_SECONDARY,
                font=font_ui(12),
                wraplength=560,
                justify="left",
            ).grid(row=0, column=0, sticky="w", padx=12, pady=12)
        else:
            for idx, msg in enumerate(messages):
                is_client = bool(msg.get("is_client", True))
                bubble_fg = BG_ELEVATED if is_client else ACCENT_INDIGO_BG
                bubble_border = BORDER if is_client else "#4338CA"
                align = "w" if is_client else "e"

                bubble = create_card(
                    self.messages_scroll,
                    fg_color=bubble_fg,
                    border_color=bubble_border,
                    corner_radius=8,
                )
                bubble.grid(row=idx, column=0, sticky=align, padx=10, pady=4)

                ts = int(msg.get("timestamp") or 0)
                ts_txt = datetime.datetime.fromtimestamp(ts).strftime("%d.%m %H:%M") if ts > 0 else ""
                sender = str(msg.get("sender") or (peer_name if is_client else "Вы"))
                customtkinter.CTkLabel(
                    bubble,
                    text=f"{sender}  ·  {ts_txt}",
                    font=font_mono(10),
                    text_color=TEXT_SECONDARY if is_client else ACCENT_INDIGO_FG,
                ).pack(anchor="w", padx=10, pady=(6, 2))

                msg_text = str(msg.get("text") or "").strip()
                if msg_text:
                    customtkinter.CTkLabel(
                        bubble,
                        text=msg_text,
                        font=font_ui(12),
                        text_color=TEXT_PRIMARY,
                        wraplength=520,
                        justify="left",
                    ).pack(anchor="w", padx=10, pady=(0, 6))

                for att in msg.get("attachments") or []:
                    fname = str(att.get("filename") or "файл")
                    customtkinter.CTkLabel(
                        bubble,
                        text=f"📎 {fname}",
                        font=font_mono(11),
                        text_color=ACCENT_INDIGO_FG,
                    ).pack(anchor="w", padx=10, pady=(0, 6))

        self._render_dialog_attachments(detail)

    def _render_dialog_attachments(self, detail: dict[str, Any]) -> None:
        for child in self.attachments_strip.winfo_children():
            child.destroy()
        self._thumb_refs.clear()

        peer_dir = Path(str(detail.get("peer_dir") or ""))
        manifest = detail.get("manifest") or {}
        files = list(manifest.get("files") or [])
        self.attachments_title.configure(text=f"Вложения и скриншоты ({len(files)})")

        if not files:
            customtkinter.CTkLabel(
                self.attachments_strip,
                text="Вложений в этом диалоге нет",
                text_color=TEXT_MUTED,
                font=font_ui(11),
            ).pack(side="left", padx=8, pady=18)
            return

        for entry in files:
            rel = str(entry.get("relative_path") or "")
            fpath = peer_dir / rel
            fname = str(entry.get("filename") or fpath.name)
            cat = str(entry.get("category") or "documents")

            item_card = create_card(self.attachments_strip, fg_color=BG_SURFACE, corner_radius=6)
            item_card.pack(side="left", padx=4, pady=4, fill="y")

            if cat == "screenshots" and fpath.is_file():
                try:
                    img = Image.open(fpath)
                    ctk_img = customtkinter.CTkImage(light_image=img, dark_image=img, size=(64, 48))
                    self._thumb_refs.append(ctk_img)
                    btn = customtkinter.CTkButton(
                        item_card,
                        image=ctk_img,
                        text="",
                        width=70,
                        height=52,
                        fg_color="transparent",
                        hover_color=BG_ELEVATED,
                        command=lambda p=fpath: self._open_image_modal(p),
                    )
                    btn.pack(padx=6, pady=(6, 2))
                except Exception as exc:
                    LOGGER.debug("Thumb skipped %s: %s", fpath, exc)

            lbl_btn = create_button(
                item_card,
                text=fname[:20],
                variant="ghost",
                height=22,
                command=lambda p=fpath: self._open_file(p),
            )
            lbl_btn.pack(padx=6, pady=(0, 6))

    def _open_file(self, target: Path) -> None:
        if target.exists() and sys.platform == "win32":
            os.startfile(str(target))

    def _open_image_modal(self, img_path: Path) -> None:
        if not img_path.is_file():
            return
        modal = customtkinter.CTkToplevel(self)
        modal.title(img_path.name)
        modal.geometry("820x620")
        modal.configure(fg_color=BG_MAIN)
        modal.attributes("-topmost", True)

        top = customtkinter.CTkFrame(modal, fg_color=BG_SURFACE)
        top.pack(fill="x", padx=12, pady=(12, 6))
        customtkinter.CTkLabel(top, text=str(img_path), font=font_mono(11), text_color=TEXT_SECONDARY).pack(side="left", padx=10, pady=8)
        create_button(
            top,
            text="Копировать путь",
            variant="primary",
            height=28,
            command=lambda: self._copy_text(str(img_path)),
        ).pack(side="right", padx=10, pady=6)

        pil_img = Image.open(img_path)
        w, h = pil_img.size
        ratio = min(760 / max(1, w), 500 / max(1, h), 1.0)
        ctk_img = customtkinter.CTkImage(
            light_image=pil_img,
            dark_image=pil_img,
            size=(int(w * ratio), int(h * ratio)),
        )
        img_lbl = customtkinter.CTkLabel(modal, image=ctk_img, text="")
        img_lbl._modal_ref = ctk_img
        img_lbl.pack(expand=True, padx=12, pady=12)

    def _copy_text(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.controller.show_notification("Скопировано", text)

    def on_create_workspace_from_chat(self) -> None:
        if not self._selected_peer_id:
            return
        peer_id = self._selected_peer_id

        def worker() -> tuple[Any, int]:
            order, copied = self.controller.kwork_sync.create_order_workspace_from_dialog(
                peer_id=peer_id,
                orders_facade=self.controller.orders_facade,
            )
            prepare_antigravity_workspace(order.order_dir, base_dir=self.controller.base_dir)
            return order, copied

        def done(res: tuple[Any, int]) -> None:
            order, copied = res
            self.controller.show_view("order_detail", order_id=order.order_id)
            self.controller.show_notification(
                "Workspace готов",
                f"Создан заказ {order.order_id}. Скопировано вложений: {copied}.",
            )

        self.controller.run_in_background(worker, on_done=done)
