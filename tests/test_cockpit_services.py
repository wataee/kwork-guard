from __future__ import annotations

import json
from pathlib import Path
import shutil
from unittest.mock import MagicMock, patch
import urllib.error
import zipfile

import pytest

from cockpit.services.guard_runner import GuardRunnerService
from cockpit.services.intake_parser import IntakeParserService, transliterate
from cockpit.services.onec_runner import OneCRunnerService
from cockpit.services.orders_facade import OrdersFacade, OrderSummary, OrderDetail, DashboardMetrics
from cockpit.services.prompt_builder import PromptBuilderService
from cockpit.services.system_doctor import SystemDoctorService
from cockpit.services.zip_packager import DeliveryPackagerService


def test_transliterate_cyrillic_and_spaces():
    assert transliterate("Иван Иванов") == "Ivan_Ivanov"
    assert transliterate("ООО 'Металл'") == "OOO_Metall"
    assert transliterate("!!!") == "Order"


def test_intake_parser_regex_extracts_all_fields():
    parser = IntakeParserService()
    sample = (
        "Добрый день! Заказ № 49086577 от клиента комплекто (https://kwork.ru/user/komplekto).\n"
        "Нужна печатная форма счета-фактуры и акта в 1С:УТ 11 без снятия с поддержки.\n"
        "Бюджет 4 000 руб. Срок 2 дня."
    )
    data = parser.parse_raw_kwork_text_regex(sample)
    assert "49086577" in data["order_id"]
    assert data["client_name"] == "komplekto"
    assert data["price_rub"] == 4000.0
    assert data["config_name"] == "1С:УТ 11"
    assert data["kind"] == "vpf"


def test_intake_parser_regex_configurations_and_kinds():
    parser = IntakeParserService()
    text_bp = "Доработать отчет СКД по продажам в 1С Бухгалтерия 3.0 за 5000 ₽"
    res_bp = parser.parse_raw_kwork_text_regex(text_bp)
    assert res_bp["config_name"] == "1С:БП 3.0"
    assert res_bp["kind"] == "report"
    assert res_bp["price_rub"] == 5000.0

    text_unf = "Создать расширение cfe для учета авторов строк в 1С:УНФ за 3500 руб"
    res_unf = parser.parse_raw_kwork_text_regex(text_unf)
    assert res_unf["config_name"] == "1С:УНФ 3.0"
    assert res_unf["kind"] == "ext"

    text_proc = "Обработка загрузки прайс-листов Excel в 1С за 2000 р"
    res_proc = parser.parse_raw_kwork_text_regex(text_proc)
    assert res_proc["kind"] == "processor"


def test_intake_parser_llm_success_and_fallback():
    parser = IntakeParserService()
    mock_llm_json = json.dumps({
        "choices": [{
            "message": {
                "content": json.dumps({
                    "order_id": "llm_order_123",
                    "client_name": "ООО Металл",
                    "title": "Печать счета с автоуслугой",
                    "kind": "vpf",
                    "price_rub": 4500.0,
                    "config_name": "1С:УТ 11",
                })
            }
        }]
    }).encode("utf-8")

    mock_resp = MagicMock()
    mock_resp.read.return_value = mock_llm_json
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res = parser.parse_raw_kwork_text_llm("Сделай форму", "api_key_valid")
        assert res["order_id"] == "llm_order_123"
        assert res["client_name"] == "ООО Металл"
        assert res["price_rub"] == 4500.0

    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 500, "Server Error", {}, None)):
        res_fallback = parser.parse_raw_kwork_text_llm("Текст с ценой 3000 руб", "api_key_valid")
        assert res_fallback["price_rub"] == 3000.0

    res_no_key = parser.parse_raw_kwork_text_llm("Текст 1000 руб", "")
    assert res_no_key["price_rub"] == 1000.0


def test_orders_facade_full_lifecycle(tmp_path: Path):
    orders_dir = tmp_path / "Заказ 999 Тест Кириллицы"
    facade = OrdersFacade(orders_dir=orders_dir, root_dir=tmp_path)

    ok_create, slug = facade.create_order({
        "order_id": "cyrillic_test_999",
        "client_name": "ООО Вектор",
        "title": "Разработка ВПФ акта",
        "kind": "vpf",
        "price_rub": 4000.0,
        "prepaid_rub": 1000.0,
        "config_name": "1С:УТ 11",
        "platform_ver": "8.3.24",
    })
    assert ok_create is True
    assert slug == "cyrillic_test_999"

    mcp_file = orders_dir / slug / ".roo" / "mcp.json"
    assert mcp_file.is_file()
    mcp_data = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert slug in mcp_data["mcpServers"]["edt"]["url"]

    order = facade.get_order(slug)
    assert order is not None
    assert order.status == "lead"
    assert order.price_rub == 4000.0

    ok_adv1, _ = facade.advance_stage(slug)
    assert ok_adv1 is True
    assert facade.get_order(slug).status == "in_progress"

    ok_adv2, _ = facade.advance_stage(slug)
    assert ok_adv2 is True
    assert facade.get_order(slug).status == "review"

    ok_adv3, _ = facade.advance_stage(slug)
    assert ok_adv3 is True
    assert facade.get_order(slug).status == "delivered"

    ok_adv_end, _ = facade.advance_stage(slug)
    assert ok_adv_end is False

    ok_hours, _ = facade.add_hours(slug, 2.5)
    assert ok_hours is True
    assert facade.get_order(slug).hours_spent == 2.5

    chk_saved = facade.save_checklist(slug, {"check_print": True, "check_save_pdf": False})
    assert chk_saved is True
    assert facade.get_order(slug).checklist.get("check_print") is True


def test_orders_facade_get_absent_order(tmp_path: Path):
    facade = OrdersFacade(orders_dir=tmp_path / "OrdersAbsent", root_dir=tmp_path)
    assert facade.get_order("absent_order_id") is None
    ok, _ = facade.add_hours("absent_order_id", 1.0)
    assert ok is False
    assert facade.save_checklist("absent_order_id", {}) is False


def test_orders_facade_search_and_filter(tmp_path: Path):
    facade = OrdersFacade(orders_dir=tmp_path / "OrdersSearch", root_dir=tmp_path)
    facade.create_order({"order_id": "vpf_alpha", "client_name": "Альфа", "kind": "vpf", "status": "lead"})
    facade.create_order({"order_id": "report_beta", "client_name": "Бета", "kind": "report", "status": "in_progress"})
    facade.create_order({"order_id": "ext_gamma", "client_name": "Гамма", "kind": "ext", "status": "delivered"})

    all_orders = facade.list_orders()
    assert len(all_orders) == 3

    vpf_only = facade.list_orders(kind_filter="vpf")
    assert len(vpf_only) == 1
    assert vpf_only[0].order_id == "vpf_alpha"

    lead_only = facade.list_orders(status_filter="lead")
    assert len(lead_only) == 1
    assert lead_only[0].order_id == "vpf_alpha"

    searched = facade.list_orders(search_query="бета")
    assert len(searched) == 1
    assert searched[0].order_id == "report_beta"


def test_orders_facade_budget_and_metrics(tmp_path: Path):
    facade = OrdersFacade(orders_dir=tmp_path / "OrdersMetrics", root_dir=tmp_path)
    facade.create_order({"order_id": "order_metric", "client_name": "Клиент", "price_rub": 5000.0, "status": "in_progress"})

    budget = facade.get_budget("order_metric")
    assert budget.calls_count == 0
    assert budget.max_calls >= 25

    ok_ext, _ = facade.extend_budget("order_metric", 15)
    assert ok_ext is True
    assert facade.get_budget("order_metric").max_calls >= 40

    ok_rst, _ = facade.reset_budget("order_metric")
    assert ok_rst is True
    assert facade.get_budget("order_metric").calls_count == 0

    metrics = facade.get_financial_summary()
    assert metrics.total_count == 1
    assert metrics.in_progress_count == 1
    assert metrics.in_progress_price == 5000.0


def test_orders_facade_mcp_events_parsing(tmp_path: Path):
    facade = OrdersFacade(orders_dir=tmp_path / "OrdersEvt", root_dir=tmp_path)
    log_file = tmp_path / "guard_log.jsonl"
    facade.guard_log = log_file

    line1 = json.dumps({"order_id": "evt_order", "tool": "scaffold_vpf", "ok": True, "ms": 150})
    line2 = json.dumps({"order_id": "other_order", "tool": "build_and_check", "ok": True, "ms": 200})
    line3 = json.dumps({"order_id": "evt_order", "tool": "patch_method", "ok": False, "ms": 80})
    log_file.write_text(f"{line1}\n{line2}\n{line3}\n", encoding="utf-8")

    events = facade.get_order_mcp_events("evt_order")
    assert len(events) == 2
    assert events[0]["tool"] == "patch_method"
    assert events[1]["tool"] == "scaffold_vpf"


def test_prompt_builder_generates_valid_prompt(tmp_path: Path):
    service = PromptBuilderService(root_dir=tmp_path)
    order_dir = tmp_path / "vpf_prompt_order"
    order_dir.mkdir()
    (order_dir / "order.json").write_text(
        json.dumps({
            "order_id": "vpf_prompt_order",
            "client_name": "ООО Снабжение",
            "kind": "vpf",
            "title": "Счет на оплату с НДС",
            "config_info": {"name": "1С:УТ 11", "platform": "8.3.24"},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (order_dir / "brief.md").write_text("Нужно рассчитать ставку ндс и вывести итог.", encoding="utf-8")

    prompt = service.build_roo_starter_prompt(order_dir)
    assert "kwork-vpf" in prompt
    assert "scaffold_vpf" in prompt
    assert "complete_order" in prompt
    assert "ООО Снабжение" in prompt


def test_prompt_builder_other_kinds(tmp_path: Path):
    service = PromptBuilderService(root_dir=tmp_path)
    for kind, expected_macro in [("report", "scaffold_report"), ("processor", "scaffold_processor"), ("ext", "create_metadata")]:
        d = tmp_path / f"test_{kind}"
        d.mkdir()
        (d / "order.json").write_text(json.dumps({"kind": kind, "title": f"Задача {kind}"}, ensure_ascii=False), encoding="utf-8")
        prompt = service.build_roo_starter_prompt(d)
        assert expected_macro in prompt


def test_zip_packager_renders_and_packs(tmp_path: Path):
    templates_dir = tmp_path / "tmpl"
    templates_dir.mkdir()
    (templates_dir / "instructions_client.md").write_text("Инструкция для {{client_name}} по {{object_name}}", encoding="utf-8")
    (templates_dir / "message_kwork.md").write_text("Здравствуйте, {{client_name}}! Заказ {{order_id}} готов.", encoding="utf-8")
    (templates_dir / "checklist_manual.md").write_text("Чеклист для {{configuration}}", encoding="utf-8")

    packager = DeliveryPackagerService(templates_dir=templates_dir)
    order_dir = tmp_path / "order_pack"
    order_dir.mkdir()
    (order_dir / "order.json").write_text(
        json.dumps({
            "order_id": "pack_123",
            "client_name": "ИП Иванов",
            "title": "АктУслуг",
            "config_info": {"name": "1С:УТ 11"},
            "kind": "vpf",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    errs = packager.render_delivery_templates(order_dir)
    assert len(errs) == 0

    instr_text = (order_dir / "delivery" / "ИНСТРУКЦИЯ_ДЛЯ_КЛИЕНТА.md").read_text(encoding="utf-8")
    assert "ИП Иванов" in instr_text
    assert "АктУслуг" in instr_text

    fake_epf = order_dir / "delivery" / "АктУслуг.epf"
    fake_epf.write_bytes(b"1C_BINARY_DATA")

    zip_file = packager.build_delivery_zip(order_dir)
    assert zip_file.is_file()

    with zipfile.ZipFile(zip_file, "r") as z:
        names = z.namelist()
        assert "АктУслуг.epf" in names
        assert "ИНСТРУКЦИЯ_ДЛЯ_КЛИЕНТА.md" in names
        assert "ЧЕК_ЛИСТ_ПРОВЕРКИ.md" in names


def test_zip_packager_unresolved_templates_detection(tmp_path: Path):
    templates_dir = tmp_path / "tmpl_bad"
    templates_dir.mkdir()
    (templates_dir / "instructions_client.md").write_text("Текст с незаполненным {{unknown_custom_placeholder}}", encoding="utf-8")
    packager = DeliveryPackagerService(templates_dir=templates_dir)
    order_dir = tmp_path / "order_bad"
    order_dir.mkdir()
    (order_dir / "order.json").write_text(json.dumps({"order_id": "bad_order"}), encoding="utf-8")
    errs = packager.render_delivery_templates(order_dir)
    assert len(errs) > 0
    assert "unknown_custom_placeholder" in errs[0]


def test_onec_runner_version_sorting(tmp_path: Path):
    runner = OneCRunnerService()
    fake_base = tmp_path / "ProgramFiles" / "1cv8"
    v1 = fake_base / "8.3.22.1704" / "bin"
    v2 = fake_base / "8.3.25.1257" / "bin"
    v1.mkdir(parents=True)
    v2.mkdir(parents=True)
    (v1 / "1cv8c.exe").write_text("", encoding="utf-8")
    (v2 / "1cv8c.exe").write_text("", encoding="utf-8")

    with patch("cockpit.services.onec_runner.Path", side_effect=lambda p: (fake_base if "1cv8" in str(p) else Path(p))):
        found = runner.find_1cv8c_executable()
        assert found is not None
        assert "8.3.25.1257" in str(found)


def test_onec_runner_launch_validation(tmp_path: Path):
    runner = OneCRunnerService()
    non_existent = tmp_path / "absent.epf"
    ok, msg = runner.launch_epf_in_thin_client("C:\\Base", non_existent)
    assert ok is False
    assert "не найден" in msg or "не существует" in msg

    fake_epf = tmp_path / "valid.epf"
    fake_epf.write_text("data", encoding="utf-8")
    fake_exe = tmp_path / "1cv8c.exe"
    fake_exe.write_text("bin", encoding="utf-8")

    with patch("subprocess.Popen") as mock_popen:
        ok_launch, msg_launch = runner.launch_epf_in_thin_client("C:\\Base", fake_epf, str(fake_exe))
        assert ok_launch is True
        assert "Запущено" in msg_launch
        mock_popen.assert_called_once()


def test_onec_runner_vscode_launch(tmp_path: Path):
    runner = OneCRunnerService()
    non_dir = tmp_path / "not_a_dir"
    ok_fail, _ = runner.open_in_vscode(non_dir)
    assert ok_fail is False

    valid_dir = tmp_path / "my_order"
    valid_dir.mkdir()
    with patch("shutil.which", return_value="C:\\fake\\code.cmd"):
        with patch("subprocess.Popen") as mock_popen:
            ok, msg = runner.open_in_vscode(valid_dir)
            assert ok is True
            assert "открыта в VS Code" in msg
            mock_popen.assert_called_once()


def test_guard_runner_status_checks(tmp_path: Path):
    runner = GuardRunnerService(root_dir=tmp_path)
    assert runner.is_guard_running(port=59999) is False
    assert runner.is_edt_running("http://127.0.0.1:59998/mcp") is False


def test_guard_runner_restart(tmp_path: Path):
    runner = GuardRunnerService(root_dir=tmp_path)
    with patch.object(runner, "stop_guard", return_value=(True, "stopped")):
        with patch.object(runner, "start_guard_background", return_value=(True, "started")):
            ok, msg = runner.restart_guard()
            assert ok is True
            assert "started" in msg


def test_guard_runner_prepare_workplace(tmp_path: Path):
    runner = GuardRunnerService(root_dir=tmp_path)
    orders_folder = tmp_path / "OrdersWorkplace"
    order_item = orders_folder / "ord_1"
    order_item.mkdir(parents=True)
    (order_item / "order.json").write_text(json.dumps({"order_id": "ord_1", "kind": "vpf"}), encoding="utf-8")

    with patch("cockpit.services.guard_runner.get_orders_dir", return_value=orders_folder):
        with patch.object(runner, "start_guard_background", return_value=(True, "mock_started")):
            with patch.object(runner, "is_edt_running", return_value=True):
                rep = runner.prepare_workplace()
                assert rep["guard_running"] is True
                assert rep["edt_running"] is True
                assert rep["repaired_orders_count"] == 1
                assert (order_item / ".roo" / "mcp.json").is_file()


def test_system_doctor_all_checks(tmp_path: Path):
    doctor = SystemDoctorService(root_dir=tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"limits": {}, "upstream": {}}), encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest>=7.0.0\n", encoding="utf-8")

    checks = doctor.run_full_diagnostics()
    assert len(checks) == 11
    check_names = [c["check"] for c in checks]
    assert "Версия Python" in check_names
    assert "Python-зависимости" in check_names
    assert "Git-клиент" in check_names
    assert "Каталог заказов" in check_names
    assert "Файл конфигурации config.json" in check_names
    assert "BSL Language Server (LSP/MCP)" in check_names
