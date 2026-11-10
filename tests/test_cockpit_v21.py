from __future__ import annotations

import json
from pathlib import Path
import shutil
from unittest.mock import patch
import zipfile

import pytest

from cockpit.app import CockpitApp
from cockpit.services.intake_parser import IntakeParserService, generate_clarifying_questions
from cockpit.services.orders_facade import OrdersFacade
from cockpit.services.zip_packager import DeliveryPackagerService
from guard.config import DEFAULT_CONFIG
from guard.fs_utils import write_bsl_with_bom
from guard.library import build_context_attachment, save_snippet, verify_library
from guard.macros import macro_bootstrap_gold
from guard.openrouter_billing import record_llm_token_usage
from tools.verify_zero_defect import verify_bsl_directory


def test_gold_ext_standard_and_bootstrap(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    ext_dir = repo_root / "gold_templates" / "ext_standard"
    assert ext_dir.is_dir()
    assert (ext_dir / ".project").is_file()
    assert (ext_dir / "src" / "Configuration" / "Configuration.mdo").is_file()

    ok_bsl, notes = verify_bsl_directory(ext_dir / "src", final_validation=False)
    assert ok_bsl is True, f"BSL issues in ext_standard: {notes}"

    target = tmp_path / "МоеРасширение"
    res = macro_bootstrap_gold({
        "template_name": "ext_standard",
        "target_dir": str(target),
        "new_name": "МоеРасширение",
    })
    assert res["isError"] is False
    bsl_files = list(target.rglob("*.bsl"))
    assert len(bsl_files) >= 1
    assert bsl_files[0].read_bytes().startswith(b"\xef\xbb\xbf")


def test_orders_facade_start_order_work_all_kinds(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    shutil.copytree(repo_root / "gold_templates", tmp_path / "gold_templates")
    shutil.copytree(repo_root / "mcp_configs", tmp_path / "mcp_configs")
    shutil.copytree(repo_root / "templates", tmp_path / "templates")
    shutil.copytree(repo_root / ".roo", tmp_path / ".roo")

    facade = OrdersFacade(orders_dir=tmp_path / "orders", root_dir=tmp_path)
    facade.create_order({
        "order_id": "ext_client_01",
        "client_name": "Иван",
        "title": "Доработка печати документов",
        "kind": "ext",
        "price_rub": 6000.0,
        "status": "lead",
    })

    start_res = facade.start_order_work("ext_client_01")
    assert start_res["ok"] is True
    assert start_res["template"] == "ext_standard"
    assert start_res["copied_files"] >= 3
    assert "Задание на разработку" in start_res["prompt"]

    ord_dir = tmp_path / "orders" / "ext_client_01"
    assert (ord_dir / ".mcp.json").is_file()
    assert (ord_dir / ".roo" / "mcp.json").is_file()
    assert (ord_dir / "CLAUDE.md").is_file()
    assert facade.get_order("ext_client_01").status == "in_progress"

    mcp_root_data = json.loads((ord_dir / ".mcp.json").read_text(encoding="utf-8"))
    assert "ext_client_01" in mcp_root_data["mcpServers"]["edt"]["url"]
    bsl_files = list((ord_dir / "src").rglob("*.bsl"))
    assert len(bsl_files) >= 1
    assert bsl_files[0].read_bytes().startswith(b"\xef\xbb\xbf")


def test_smart_intake_clarifying_questions() -> None:
    parser = IntakeParserService()
    vague_res = parser.parse_raw_kwork_text_regex("Нужно доработать справочник в 1С за 3000 руб")
    assert len(vague_res["clarifying_questions"]) == 4
    assert "релиза" in vague_res["clarifying_questions_text"]

    detailed = (
        "Нужна внешняя печатная форма (.epf) в формате БСП (макет) для 1С:УТ 11.5.17, "
        "база клиент-серверная, для тестирования пришлю выгрузку .dt."
    )
    det_questions = generate_clarifying_questions({"config_name": "1С:УТ 11"}, detailed)
    assert len(det_questions) == 0


def test_profitability_margin_and_economy_threshold(tmp_path: Path) -> None:
    facade = OrdersFacade(orders_dir=tmp_path / "orders", root_dir=tmp_path)
    facade.create_order({
        "order_id": "margin_ord",
        "client_name": "КлиентМаржа",
        "title": "Отчет",
        "kind": "report",
        "price_rub": 1000.0,
        "status": "in_progress",
    })

    state = facade.order_mgr.get_order_sync("margin_ord")
    record_llm_token_usage(
        state=state,
        provider="openrouter",
        model="anthropic/claude-3-7-sonnet",
        input_tokens=200000,
        output_tokens=20000,
        usd_to_rub=100.0,
    )

    budget = facade.get_budget("margin_ord")
    assert budget.real_cost_rub > 50.0
    assert budget.is_api_over_threshold is True
    assert budget.economy_mode is True
    assert budget.kwork_fee_rub == 200.0
    assert budget.net_margin_rub < 800.0

    summary = facade.get_financial_summary()
    assert summary.total_kwork_fee_rub == 200.0
    assert summary.total_net_margin_rub == budget.net_margin_rub


def test_library_save_verify_and_context_attachment(tmp_path: Path) -> None:
    lib_dir = tmp_path / "library"
    ok_bad, _ = save_snippet("   ", "Title", "code", library_dir=lib_dir)
    assert ok_bad is False

    ok, msg = save_snippet(
        snippet_id="fast_sum_calc",
        title="Подсчет суммы таблицы",
        code="Функция Посчитать()\n\tВозврат 100;\nКонецФункции",
        snippet_type="bsl",
        tags=["таблица", "расчет"],
        library_dir=lib_dir,
    )
    assert ok is True
    assert (lib_dir / "bsl" / "fast_sum_calc.bsl").read_bytes().startswith(b"\xef\xbb\xbf")

    v_ok, v_rep = verify_library(tmp_path)
    assert v_ok is True
    assert "fast_sum_calc" in v_rep

    ctx = build_context_attachment("Нужен расчет таблицы", library_dir=lib_dir)
    assert "fast_sum_calc" in ctx
    assert "Посчитать" in ctx

    save_snippet(
        snippet_id="fast_sum_calc",
        title="Обновленный расчет",
        code="Процедура Плохо()\n\tДля И = 1 По 2 Цикл\n\t\tЗапрос.Выполнить();\n\tКонецЦикла;\nКонецПроцедуры",
        snippet_type="bsl",
        library_dir=lib_dir,
    )
    save_snippet(
        snippet_id="missing_query",
        title="Отсутствующий запрос",
        code="ВЫБРАТЬ 1",
        snippet_type="query",
        library_dir=lib_dir,
    )
    (lib_dir / "query" / "missing_query.txt").unlink()
    v_fail, rep_fail = verify_library(tmp_path)
    assert v_fail is False
    assert "[FAIL] fast_sum_calc" in rep_fail
    assert "[FAIL] missing_query" in rep_fail


def test_batch_delivery_pipeline_and_kind_instructions(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    packager = DeliveryPackagerService(templates_dir=repo_root / "templates")

    order_dir = tmp_path / "order_cfe"
    (order_dir / "src").mkdir(parents=True)
    write_bsl_with_bom(order_dir / "src" / "Mod.bsl", "Функция Тест()\n\tВозврат 1;\nКонецФункции")
    (order_dir / "order.json").write_text(
        json.dumps({
            "order_id": "cfe_99",
            "client_name": "ИП Сидоров",
            "title": "РасширениеУчета",
            "kind": "ext",
            "config_info": {"name": "1С:УНФ 3.0"},
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    fake_cfe = order_dir / "РасширениеУчета.cfe"
    fake_cfe.write_bytes(b"CFE_DATA")

    res = packager.compile_and_package_order(order_dir)
    assert res["ok"] is True
    assert res["bsl_clean"] is True
    assert "ИП Сидоров" in res["kwork_message"]
    assert res["zip_path"].is_file()

    instr = (order_dir / "delivery" / "ИНСТРУКЦИЯ_ДЛЯ_КЛИЕНТА.md").read_text(encoding="utf-8")
    assert ".cfe" in instr
    assert "Вариант 1" not in instr

    with zipfile.ZipFile(res["zip_path"], "r") as z:
        names = z.namelist()
        assert "РасширениеУчета.cfe" in names
        assert "src/Mod.bsl" in names

    vpf_dir = tmp_path / "order_vpf"
    (vpf_dir / "src").mkdir(parents=True)
    (vpf_dir / "order.json").write_text(
        json.dumps({"order_id": "vpf_1", "client_name": "А", "title": "Форма", "kind": "vpf"}),
        encoding="utf-8",
    )
    packager.render_delivery_templates(vpf_dir)
    vpf_instr = (vpf_dir / "delivery" / "ИНСТРУКЦИЯ_ДЛЯ_КЛИЕНТА.md").read_text(encoding="utf-8")
    assert ".epf" in vpf_instr
    assert "Вариант 2" not in vpf_instr


def test_cockpit_ui_v21_workflows(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    shutil.copytree(repo_root / "gold_templates", tmp_path / "gold_templates")
    shutil.copytree(repo_root / "mcp_configs", tmp_path / "mcp_configs")
    shutil.copytree(repo_root / "templates", tmp_path / "templates")
    shutil.copytree(repo_root / ".roo", tmp_path / ".roo")
    (tmp_path / "library").mkdir(parents=True)
    (tmp_path / "library" / "index.json").write_text('{"version": "1.0", "snippets": []}', encoding="utf-8")
    (tmp_path / "config.json").write_text(json.dumps(DEFAULT_CONFIG), encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest>=7.0.0\n", encoding="utf-8")

    cfg_file = tmp_path / "config.json"
    with patch("guard.config.CONFIG_PATH", cfg_file):
        facade = OrdersFacade(base_dir=tmp_path)
        order = facade.create_order(
            order_slug="ui_v21_ord",
            client="client_v21",
            title="Тест v2.1 UI",
            kind="ext",
            price=4000.0,
        )

        app = CockpitApp(base_dir=tmp_path)
        try:
            app.update_idletasks()
            app.show_view("order_detail", order_id=order.order_id)
            app.update_idletasks()

            detail_view = app.views["order_detail"]
            with patch.object(app.onec_runner, "open_in_vscode", return_value=(True, "OK")):
                detail_view.on_start_work_1click()
                app.wait_for_background_tasks()

            assert (order.order_dir / "CLAUDE.md").is_file()
            assert (order.order_dir / "AGENTS.md").is_file()
            assert (order.order_dir / ".mcp.json").is_file()
            assert (tmp_path / "state" / "antigravity_mcp_config.json").is_file()

            detail_view.bsl_editor.delete("1.0", "end")
            detail_view.bsl_editor.insert("1.0", "Функция Демо()\n\tВозврат 1;\nКонецФункции")
            detail_view.snippet_id_entry.insert(0, "ui_snip")
            detail_view.on_save_snippet_to_library()
            assert (tmp_path / "library" / "bsl" / "ui_snip.bsl").is_file()

            app.show_view("tools")
            app.update_idletasks()
            tools_view = app.views["tools"]
            tools_view.on_verify_library()
            assert "ui_snip" in tools_view.preview_box.get("1.0", "end")
            tools_view.on_generate_lessons()
            assert "Уроки недели" in tools_view.preview_box.get("1.0", "end")

            app.show_view("settings")
            app.update_idletasks()
            sett_view = app.views["settings"]
            sett_view.agy_key_entry.insert(0, "agy-secret-123")
            sett_view.on_save_settings()
            env_text = (tmp_path / ".env").read_text(encoding="utf-8")
            assert "ANTIGRAVITY_API_KEY=" in env_text

            app.top_bar.on_switch_provider("OpenRouter")
            app.wait_for_background_tasks()
            assert "openrouter" in cfg_file.read_text(encoding="utf-8")
        finally:
            app.destroy()


def test_guard_runner_error_and_switch_branches(tmp_path: Path) -> None:
    from unittest.mock import MagicMock
    from cockpit.services.guard_runner import GuardRunnerService

    (tmp_path / "config.json").write_text(json.dumps(DEFAULT_CONFIG), encoding="utf-8")
    (tmp_path / "edt_guard.py").write_text("print('ok')\n", encoding="utf-8")
    svc = GuardRunnerService(root_dir=tmp_path)

    with patch("guard.config.CONFIG_PATH", tmp_path / "config.json"):
        ok_bad, _ = svc.switch_provider("unknown_provider")
        assert ok_bad is False

        class _OkHttp:
            def read(self) -> bytes:
                return b'{"ok":true}'

            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                return None

        with patch.object(svc, "is_guard_running", return_value=True):
            with patch("urllib.request.urlopen", return_value=_OkHttp()):
                ok_sw, msg_sw = svc.switch_provider("antigravity")
                assert ok_sw is True
                assert msg_sw == "antigravity"
            with patch("urllib.request.urlopen", side_effect=OSError("notify failed")):
                ok_warn, _ = svc.switch_provider("openrouter")
                assert ok_warn is True

        with patch.object(svc, "is_guard_running", return_value=False):
            with patch("subprocess.Popen", side_effect=OSError("no python")):
                ok_pop, msg_pop = svc.start_guard_background()
                assert ok_pop is False
                assert "no python" in msg_pop

            fake_proc = MagicMock(pid=43210)
            with patch("subprocess.Popen", return_value=fake_proc), patch("time.sleep"):
                ok_to, msg_to = svc.start_guard_background()
                assert ok_to is False
                assert "timeout" in msg_to

        svc.state_dir.mkdir(parents=True, exist_ok=True)
        svc.pid_file.write_text("12345", encoding="utf-8")
        with patch("cockpit.services.guard_runner.sys.platform", "linux"), \
             patch("cockpit.services.guard_runner.os.kill", side_effect=OSError("no such pid"), create=True), \
             patch.object(Path, "unlink", side_effect=OSError("locked pid")), \
             patch.object(svc, "is_guard_running", return_value=True), \
             patch("time.sleep"):
            ok_st, msg_st = svc.stop_guard()
            assert ok_st is False
            assert "still active" in msg_st

        bad_orders_dir = MagicMock(spec=Path)
        bad_orders_dir.is_dir.return_value = False
        bad_orders_dir.mkdir.side_effect = PermissionError("denied")
        with patch.object(svc, "start_guard_background", return_value=(True, "ok")), \
             patch.object(svc, "is_edt_running", return_value=False), \
             patch.object(svc, "check_antigravity_status", return_value=(False, "http://127.0.0.1:3000/v1")), \
             patch("cockpit.services.guard_runner.get_orders_dir", return_value=bad_orders_dir):
            prep = svc.prepare_workplace()
            assert prep["orders_dir_ready"] is False


@pytest.mark.asyncio
async def test_antigravity_mcp_config_sync_and_root_mcp_route(tmp_path: Path) -> None:
    import httpx
    import edt_guard
    from cockpit.services.system_doctor import SystemDoctorService
    from guard.config import (
        is_antigravity_mcp_configured,
        sync_antigravity_mcp_config,
    )

    mcp_cfg_path = tmp_path / "mcp_config.json"
    mcp_cfg_path.write_text('{"mcpServers": {"other": {"url": "http://localhost:9999/mcp"}}}', encoding="utf-8")

    ok_before, _ = is_antigravity_mcp_configured(mcp_cfg_path)
    assert ok_before is False

    sync_antigravity_mcp_config(
        profile="ext",
        order_id="demo_1c",
        target_path=mcp_cfg_path,
        overwrite_existing_url=True,
    )
    ok_after, url_after = is_antigravity_mcp_configured(mcp_cfg_path)
    assert ok_after is True
    assert url_after == "http://127.0.0.1:8766/mcp/ext/demo_1c"
    raw = json.loads(mcp_cfg_path.read_text(encoding="utf-8"))
    assert "other" in raw["mcpServers"]

    sync_antigravity_mcp_config(
        profile="full",
        order_id="default",
        target_path=mcp_cfg_path,
        overwrite_existing_url=False,
    )
    _, preserved = is_antigravity_mcp_configured(mcp_cfg_path)
    assert preserved == "http://127.0.0.1:8766/mcp/ext/demo_1c"

    bad_cfg = tmp_path / "bad_mcp_config.json"
    bad_cfg.write_text("{not json", encoding="utf-8")
    assert is_antigravity_mcp_configured(bad_cfg) == (False, "")
    sync_antigravity_mcp_config(target_path=bad_cfg)
    assert is_antigravity_mcp_configured(bad_cfg)[0] is True

    doctor = SystemDoctorService(root_dir=tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(DEFAULT_CONFIG), encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("pytest>=7.0.0\n", encoding="utf-8")
    with patch("guard.config.ANTIGRAVITY_MCP_CONFIG_PATH", mcp_cfg_path):
        checks = doctor.run_full_diagnostics()
        agy_chk = [c for c in checks if "Antigravity Gateway" in c["check"]][0]
        assert agy_chk["status"] == "OK"
        assert "demo_1c" in agy_chk["message"]

    transport = httpx.ASGITransport(app=edt_guard.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r_get = await client.get("/mcp")
        assert r_get.status_code == 405
        r_del = await client.delete("/mcp")
        assert r_del.status_code == 200
        r_init = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
        )
        assert r_init.status_code == 200
        assert "Mcp-Session-Id" in r_init.headers
        assert r_init.json()["result"]["serverInfo"]["name"] == "edt-guard/full"


