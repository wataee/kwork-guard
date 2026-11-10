from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch
import pytest

from cockpit.app import CockpitApp, is_pid_alive
from cockpit.services.orders_facade import OrdersFacade


def test_is_pid_alive_self() -> None:
    assert is_pid_alive(os.getpid()) is True
    assert is_pid_alive(-999) is False


def test_cockpit_app_lifecycle_and_views(tmp_path: Path) -> None:
    orders_dir = tmp_path / "orders"
    orders_dir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)
    library_dir = tmp_path / "library"
    library_dir.mkdir(parents=True)

    lib_index = library_dir / "index.json"
    lib_index.write_text(
        json.dumps([{"id": "snip1", "title": "Сниппет 1", "description": "Описание", "code": "Сообщить(1);"}], ensure_ascii=False),
        encoding="utf-8",
    )

    facade = OrdersFacade(base_dir=tmp_path)
    order = facade.create_order(
        order_slug="test_vpf",
        client="client_alpha",
        title="Тестовая печатная форма",
        kind="vpf",
        price=3500.0,
        configuration="1С:УНФ 3.0",
        deadline="2026-11-20",
        object_name="ЗаказПокупателя",
    )

    app = CockpitApp(base_dir=tmp_path)
    try:
        app.update_idletasks()

        for view_name in ["today", "orders", "new_order", "finance", "tools", "settings"]:
            app.show_view(view_name)
            app.update_idletasks()

        app.show_view("order_detail", order_id=order.order_id)
        app.update_idletasks()

        order_view = app.views["order_detail"]
        assert order_view.current_order_id == order.order_id

        order_view.on_change_stage("development")
        order_view.on_add_hours(1.5)
        order_view.ov_title_entry.delete(0, "end")
        order_view.ov_title_entry.insert(0, "Обновленный заголовок")
        order_view.on_save_overview()

        order_view.bsl_editor.delete("1.0", "end")
        order_view.bsl_editor.insert("1.0", "Функция Тест()\n\tВозврат Истина;\nКонецФункции")
        order_view.on_dev_run_linter()
        assert "✅" in order_view.linter_results.get("1.0", "end")

        order_view.brief_editor.delete("1.0", "end")
        order_view.brief_editor.insert("1.0", "Конфигурация: 1С:УНФ\nОжидаемый результат: печатная форма")
        order_view.on_validate_brief()
        order_view.on_save_brief()

        order_view.on_budget_extend()
        order_view.on_budget_reset()

        order_view.checklist_vars["bsl_lint_clean"].set(True)
        order_view.on_toggle_checklist()

        order_view.on_create_warranty_branch()
        order_view.decisions_editor.insert("1.0", "Архитектурное решение зафиксировано")
        order_view.on_save_decisions()

        with patch.object(app.packager, "reveal_in_explorer"):
            order_view.on_deliver_order()
            app.wait_for_background_tasks()

        today_view = app.views["today"]
        app.show_view("today")
        app.update_idletasks()
        today_view.on_copy_prompt(order)

        orders_view = app.views["orders"]
        app.show_view("orders")
        app.update_idletasks()
        orders_view.search_entry.insert(0, "client_alpha")
        orders_view.filter_orders()

        new_view = app.views["new_order"]
        app.show_view("new_order")
        app.update_idletasks()
        sample_raw = "Добрый день, нужен отчет СКД по продажам для 1С:УТ 11.5. Бюджет 5000 руб, клиент petrov."
        new_view.raw_text_box.insert("1.0", sample_raw)
        new_view.on_parse_intake()
        assert "petrov" in new_view.slug_entry.get().lower()
        assert len(new_view.questions_box.get("1.0", "end").strip()) > 10
        new_view.on_copy_questions()
        new_view.on_submit_create(open_vscode=False)

        fin_view = app.views["finance"]
        app.show_view("finance")
        app.update_idletasks()
        fin_view.on_export_csv()
        export_csv = tmp_path / "reports" / "finance_orders.csv"
        assert export_csv.exists()

        tools_view = app.views["tools"]
        app.show_view("tools")
        app.update_idletasks()
        tools_view.search_snip.insert(0, "Сниппет")
        tools_view.filter_snippets()
        assert len(tools_view._snippets_data) > 0
        tools_view.select_snippet(tools_view._snippets_data[0])
        tools_view.on_copy_selected_snippet()

        sett_view = app.views["settings"]
        app.show_view("settings")
        app.update_idletasks()
        sett_view.on_run_diagnostics()
        app.wait_for_background_tasks()
        sett_view.on_save_settings()

        top = app.top_bar
        with patch.object(app.guard_runner, "is_edt_running", return_value=True), \
             patch.object(app.guard_runner, "is_guard_running", return_value=True), \
             patch.object(app.guard_runner, "check_antigravity_status", return_value=(True, "http://127.0.0.1:3000/v1")):
            top.refresh_status()
            assert "8765 ●" in top.edt_badge.cget("text")
            assert "8766 ●" in top.guard_badge.cget("text")
            assert "3000 ●" in top.agy_badge.cget("text")

        with patch.object(app.guard_runner, "prepare_workplace", return_value=(True, "Окружение готово")):
            top.on_prepare_workplace()
            app.wait_for_background_tasks()

        with patch.object(app.guard_runner, "start_guard_background", return_value=True):
            top.on_start_guard()
            app.wait_for_background_tasks()

        with patch.object(app.guard_runner, "stop_guard", return_value=True):
            top.on_stop_guard()
            app.wait_for_background_tasks()

        with patch.object(app.guard_runner, "restart_guard", return_value=True):
            top.on_restart_guard()
            app.wait_for_background_tasks()

        app.show_notification("Инфо", "Тестовое сообщение")

    finally:
        app.destroy()


def test_order_detail_view_negative_and_linter_issues(tmp_path: Path) -> None:
    orders_dir = tmp_path / "orders"
    orders_dir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    facade = OrdersFacade(base_dir=tmp_path)
    order = facade.create_order(
        order_slug="order_linter_neg",
        client="client_neg",
        title="Тест линтера",
        kind="vpf",
        price=1000.0,
    )

    app = CockpitApp(base_dir=tmp_path)
    try:
        app.show_view("order_detail", order_id=order.order_id)
        app.update_idletasks()

        order_view = app.views["order_detail"]

        order_view.bsl_editor.delete("1.0", "end")
        order_view.bsl_editor.insert("1.0", "Если Истина Тогда\n\tСообщить(1);")
        order_view.on_dev_run_linter()
        assert "BSL001_UNBALANCED_BLOCKS" in order_view.linter_results.get("1.0", "end")

        order_view.brief_editor.delete("1.0", "end")
        order_view.brief_editor.insert("1.0", "Короткий текст")
        order_view.on_validate_brief()

        with patch.object(app.onec_runner, "open_in_vscode", return_value=(True, "OK")):
            order_view.on_dev_open_vscode()
            app.wait_for_background_tasks()

        with patch.object(app.onec_runner, "launch_epf_in_thin_client", return_value=(True, "OK")):
            fake_epf = order.order_dir / "test.epf"
            fake_epf.write_text("epf", encoding="utf-8")
            order_view.on_dev_launch_1c()
            app.wait_for_background_tasks()

        order_view.load_order("absent_order_999")

    finally:
        app.destroy()


def test_new_order_view_edge_cases(tmp_path: Path) -> None:
    orders_dir = tmp_path / "orders"
    orders_dir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    app = CockpitApp(base_dir=tmp_path)
    try:
        new_view = app.views["new_order"]
        app.show_view("new_order")
        app.update_idletasks()

        new_view.raw_text_box.delete("1.0", "end")
        new_view.on_parse_intake()

        new_view.slug_entry.delete(0, "end")
        new_view.client_entry.delete(0, "end")
        new_view.client_entry.insert(0, "client_new")
        new_view.price_entry.delete(0, "end")
        new_view.price_entry.insert(0, "bad_price")

        with patch.object(app.onec_runner, "open_in_vscode", return_value=(True, "OK")):
            new_view.on_submit_create(open_vscode=True)
            app.wait_for_background_tasks()

        created = app.orders_facade.list_orders()
        assert len(created) > 0

    finally:
        app.destroy()


def test_tools_view_pytest_thread(tmp_path: Path) -> None:
    orders_dir = tmp_path / "orders"
    orders_dir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True)

    app = CockpitApp(base_dir=tmp_path)
    try:
        tools_view = app.views["tools"]
        app.show_view("tools")
        app.update_idletasks()

        tools_view._on_pytest_done(True, "5 passed in 0.1s")
        assert "5 passed" in tools_view.preview_box.get("1.0", "end")

        tools_view._on_pytest_done(False, "1 failed")
        assert "1 failed" in tools_view.preview_box.get("1.0", "end")

        with patch("subprocess.run") as mock_run:
            mock_proc = MagicMock()
            mock_proc.returncode = 0
            mock_proc.stdout = "All tests passed"
            mock_proc.stderr = ""
            mock_run.return_value = mock_proc
            tools_view.on_run_pytest()
            app.wait_for_background_tasks()

    finally:
        app.destroy()


def test_lock_file_dead_pid_recovery(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    lock_file = state_dir / "cockpit.lock"
    lock_file.write_text("999999", encoding="utf-8")

    app = CockpitApp(base_dir=tmp_path)
    try:
        assert lock_file.exists()
        current_pid = str(os.getpid())
        assert lock_file.read_text(encoding="utf-8") == current_pid
    finally:
        app.destroy()
