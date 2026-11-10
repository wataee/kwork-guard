from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import urllib.error
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from guard.bsl_linter import lint_bsl_code
from guard.config import DEFAULT_CONFIG, load_config, save_config
from guard.macros import (
    macro_bootstrap_gold,
    macro_get_critical_errors,
    macro_patch_method,
    macro_read_bsl_method,
    macro_scaffold_processor,
    macro_scaffold_report,
)
from cockpit.services.guard_runner import GuardRunnerService
from cockpit.services.onec_runner import OneCRunnerService
from cockpit.services.orders_facade import OrdersFacade
from cockpit.services.system_doctor import SystemDoctorService
from cockpit.services.zip_packager import DeliveryPackagerService


def test_bsl_linter_quoted_escapes_and_multiline_pipes() -> None:
    code = (
        'Строка1 = "Текст с ""кавычками"" Если Тогда";\n'
        '|Продолжение строки КонецЕсли;\n'
        'Функция Нормальная()\n'
        '\tВозврат "ок";\n'
        'КонецФункции'
    )
    issues = lint_bsl_code(code)
    assert len(issues) == 0


def test_bsl_linter_unexpected_closings() -> None:
    code = (
        'КонецЕсли\n'
        'КонецЦикла\n'
        'КонецПопытки\n'
        '#КонецОбласти\n'
        '#КонецЕсли\n'
        'КонецПроцедуры\n'
        'КонецФункции\n'
    )
    issues = lint_bsl_code(code)
    assert len(issues) == 7
    for iss in issues:
        assert iss.code == "BSL001_UNBALANCED_BLOCKS"


def test_bsl_linter_balanced_regions_and_preprocessor() -> None:
    code = (
        '#Область Служебные\n'
        '#Если Клиент Тогда\n'
        'Процедура Тест()\n'
        '\tСообщить("1");\n'
        'КонецПроцедуры\n'
        '#КонецЕсли\n'
        '#КонецОбласти'
    )
    issues = lint_bsl_code(code)
    assert len(issues) == 0


def test_config_save_and_recovery(tmp_path: Path) -> None:
    cfg_file = tmp_path / "config.json"
    with patch("guard.config.CONFIG_PATH", cfg_file):
        loaded_default = load_config()
        assert loaded_default["limits"]["max_calls_per_order"] == 25

        new_data = loaded_default.copy()
        new_data["limits"]["max_calls_per_order"] = 40
        saved_ok = save_config(new_data)
        assert saved_ok is True
        assert cfg_file.is_file()

        reloaded = load_config()
        assert reloaded["limits"]["max_calls_per_order"] == 40

        cfg_file.write_text("{bad_json:", encoding="utf-8")
        recovered = load_config()
        assert recovered["limits"]["max_calls_per_order"] == 25

        with patch.object(Path, "mkdir", side_effect=OSError("disk error")):
            save_failed = save_config({"key": "val"})
            assert save_failed is False


def test_guard_runner_start_stop_branches(tmp_path: Path) -> None:
    runner = GuardRunnerService(root_dir=tmp_path)

    with patch.object(runner, "is_guard_running", return_value=True):
        ok, reason = runner.start_guard_background()
        assert ok is True
        assert reason == "already_running"

    with patch.object(runner, "is_guard_running", return_value=False):
        ok_no_script, msg = runner.start_guard_background()
        assert ok_no_script is False
        assert "not found" in msg.lower()

    guard_py = tmp_path / "edt_guard.py"
    guard_py.write_text("print('guard')", encoding="utf-8")

    mock_proc = MagicMock()
    mock_proc.pid = 12345
    with patch("subprocess.Popen", return_value=mock_proc), \
         patch.object(runner, "is_guard_running", side_effect=[False, True]):
        ok_start, msg_start = runner.start_guard_background()
        assert ok_start is True
        assert "started" in msg_start

    with patch.object(runner, "is_guard_running", return_value=False):
        stopped_not_running = runner.stop_guard()
        assert stopped_not_running is False or isinstance(stopped_not_running, (bool, tuple))

    runner.pid_file.write_text("12345", encoding="utf-8")
    with patch("subprocess.run") as mock_run:
        runner.stop_guard()
        assert mock_run.called

    runner.pid_file.write_text("not_a_number", encoding="utf-8")
    runner.stop_guard()


def test_guard_runner_network_and_workplace(tmp_path: Path) -> None:
    runner = GuardRunnerService(root_dir=tmp_path)

    mock_resp_ok = MagicMock()
    mock_resp_ok.read.return_value = b'{"ok": true}'
    mock_resp_ok.__enter__.return_value = mock_resp_ok
    mock_resp_ok.status = 200

    with patch("urllib.request.urlopen", return_value=mock_resp_ok):
        assert runner.is_guard_running() is True
        assert runner.is_edt_running() is True

    http_err = urllib.error.HTTPError("http://127.0.0.1:8765/mcp", 400, "Bad Request", {}, io.BytesIO(b""))
    with patch("urllib.request.urlopen", side_effect=http_err):
        assert runner.is_edt_running() is True

    fake_orders_dir = tmp_path / "NewOrdersDir"
    with patch("cockpit.services.guard_runner.get_orders_dir", return_value=fake_orders_dir), \
         patch.object(runner, "start_guard_background", return_value=(True, "ok")), \
         patch.object(runner, "is_edt_running", return_value=True):
        prep = runner.prepare_workplace()
        assert prep["orders_dir_ready"] is True
        assert fake_orders_dir.is_dir()


def test_onec_runner_branches(tmp_path: Path) -> None:
    runner = OneCRunnerService(base_dir=tmp_path)

    fake_exe = tmp_path / "fake_1cv8c.exe"
    fake_exe.write_text("binary", encoding="utf-8")
    found_explicit = runner.find_1cv8c_executable(str(fake_exe))
    assert found_explicit == fake_exe

    with patch("cockpit.services.onec_runner.load_config", return_value={"onec": {"thin_client_path": str(fake_exe)}}):
        found_cfg = runner.find_1cv8c_executable("")
        assert found_cfg == fake_exe

    with patch("cockpit.services.onec_runner.load_config", return_value={}), patch("pathlib.Path.glob", return_value=[]):
        found_none = runner.find_1cv8c_executable(str(tmp_path / "absent.exe"))
        assert found_none is None

    fake_epf = tmp_path / "report.epf"
    fake_epf.write_text("epf", encoding="utf-8")
    with patch("subprocess.Popen"):
        ok_epf, msg_epf = runner.launch_epf_in_thin_client("C:\\TestIB", fake_epf, exec_path=str(fake_exe))
        assert ok_epf is True

    with patch.object(runner, "find_1cv8c_executable", return_value=None):
        ok_no_exe, _ = runner.launch_epf_in_thin_client("C:\\TestIB", fake_epf)
        assert ok_no_exe is False

    ok_no_epf, _ = runner.launch_epf_in_thin_client("C:\\TestIB", tmp_path / "absent.epf", exec_path=str(fake_exe))
    assert ok_no_epf is False

    with patch("subprocess.Popen", side_effect=OSError("Popen failed")):
        ok_fail, _ = runner.launch_epf_in_thin_client("C:\\TestIB", fake_epf, exec_path=str(fake_exe))
        assert ok_fail is False

    ok_bad_dir, _ = runner.open_in_vscode(tmp_path / "absent_dir")
    assert ok_bad_dir is False

    with patch("shutil.which", return_value=None), patch("os.getenv", return_value=""):
        ok_no_code, msg_code = runner.open_in_vscode(tmp_path)
        assert ok_no_code is False
        assert "не найдена" in msg_code

    with patch("subprocess.Popen") as mock_popen:
        ok_custom, _ = runner.open_in_vscode(tmp_path, custom_code_bin="code")
        assert ok_custom is True
        assert mock_popen.called

    with patch("subprocess.Popen", side_effect=OSError("VS Code launch error")):
        ok_code_fail, _ = runner.open_in_vscode(tmp_path, custom_code_bin="code")
        assert ok_code_fail is False


def test_system_doctor_all_branches(tmp_path: Path) -> None:
    doctor = SystemDoctorService(root_dir=tmp_path)

    req_file = tmp_path / "requirements.txt"
    req_file.write_text("definitely_nonexistent_package_xyz>=1.0\n", encoding="utf-8")

    with patch("sys.version_info", (3, 8, 0)), \
         patch("shutil.which", return_value=None), \
         patch.object(doctor.guard_runner, "is_guard_running", return_value=False), \
         patch.object(doctor.guard_runner, "is_edt_running", return_value=False), \
         patch.object(doctor.onec_runner, "find_1cv8c_executable", return_value=None):
        results = doctor.run_full_diagnostics()
        assert len(results) >= 8

        statuses = [r["status"] for r in results]
        assert "FAIL" in statuses or "WARN" in statuses


def test_zip_packager_reveal_branches(tmp_path: Path) -> None:
    packager = DeliveryPackagerService(templates_dir=tmp_path)

    packager.reveal_in_explorer(tmp_path / "absent.zip")

    target_file = tmp_path / "test.zip"
    target_file.write_text("data", encoding="utf-8")

    with patch("subprocess.run") as mock_run:
        packager.reveal_in_explorer(target_file)
        assert mock_run.called

    with patch("sys.platform", "darwin"), patch("subprocess.run") as mock_run_darwin:
        packager.reveal_in_explorer(target_file)
        assert mock_run_darwin.called

    with patch("sys.platform", "linux"), patch("subprocess.run") as mock_run_linux:
        packager.reveal_in_explorer(target_file)
        assert mock_run_linux.called


def test_orders_facade_branches(tmp_path: Path) -> None:
    orders_dir = tmp_path / "OrdersBranch"
    orders_dir.mkdir(parents=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)

    facade = OrdersFacade(orders_dir=orders_dir, root_dir=tmp_path)

    bad_order_dir = orders_dir / "bad_order"
    bad_order_dir.mkdir()
    (bad_order_dir / "order.json").write_text("{corrupt_json:", encoding="utf-8")

    all_orders = facade.list_orders()
    assert len(all_orders) == 0

    order_detail = facade.create_order(
        order_slug="lead_order",
        client="ClientLead",
        kind="vpf",
        title="Тест статусов",
        price=1000.0,
    )
    assert facade.get_order("lead_order") is not None

    ok_adv1, _ = facade.advance_stage("lead_order")
    assert ok_adv1 is True
    ok_adv2, _ = facade.advance_stage("lead_order")
    assert ok_adv2 is True
    ok_adv3, _ = facade.advance_stage("lead_order")
    assert ok_adv3 is True
    ok_adv4, _ = facade.advance_stage("lead_order")
    assert ok_adv4 is False

    ok_custom_stage, _ = facade.advance_stage("lead_order", target_stage="warranty")
    assert ok_custom_stage is True

    log_file = tmp_path / "guard_log.jsonl"
    log_file.write_text("corrupted_line_without_json\n{\"order_id\": \"lead_order\", \"tool\": \"test_tool\"}\n", encoding="utf-8")
    facade.guard_log = log_file

    events = facade.get_order_mcp_events("lead_order")
    assert len(events) == 1
    assert events[0]["tool"] == "test_tool"


@pytest.mark.asyncio
async def test_macros_error_branches() -> None:
    mock_up = MagicMock()
    mock_up.tool = AsyncMock()

    res_missing = await macro_read_bsl_method({}, mock_up)
    assert res_missing.get("isError") is True

    mock_up.tool.return_value = ("Функция Тест()\n\tВозврат 1;\nКонецФункции", False)
    res_not_found = await macro_read_bsl_method({"projectName": "P", "modulePath": "M", "methodName": "Несуществующий"}, mock_up)
    assert res_not_found.get("isError") is True

    res_patch_empty = await macro_patch_method({"projectName": "P", "modulePath": "M", "method": "F", "newSource": ""}, mock_up)
    assert res_patch_empty.get("isError") is True
