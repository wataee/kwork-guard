from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest.mock import MagicMock, patch
import pytest

from guard.bsl_ls_runner import BslLanguageServerService, BslLsDiagnostic


def test_find_java_executable_via_which() -> None:
    svc = BslLanguageServerService()
    with patch("shutil.which", return_value="C:\\Java\\bin\\java.exe"):
        res = svc.find_java_executable()
        assert res == "C:\\Java\\bin\\java.exe"


def test_find_java_executable_via_java_home(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    java_file = bin_dir / "java.exe"
    java_file.write_text("dummy", encoding="utf-8")

    svc = BslLanguageServerService()
    with patch("shutil.which", return_value=None):
        with patch.dict("os.environ", {"JAVA_HOME": str(tmp_path)}):
            res = svc.find_java_executable()
            assert res == str(java_file)


def test_find_java_executable_fallback_search(tmp_path: Path) -> None:
    prog_dir = tmp_path / "ProgramFiles" / "Java" / "jdk-17" / "bin"
    prog_dir.mkdir(parents=True)
    java_file = prog_dir / "java.exe"
    java_file.write_text("dummy", encoding="utf-8")

    svc = BslLanguageServerService()
    with patch("shutil.which", return_value=None):
        with patch.dict("os.environ", {"JAVA_HOME": "", "ProgramFiles": str(tmp_path / "ProgramFiles")}, clear=False):
            with patch("sys.platform", "win32"):
                res = svc.find_java_executable()
                assert res is not None
                assert "java.exe" in res.lower()


def test_find_bsl_ls_jar_direct_and_env(tmp_path: Path) -> None:
    jar_file = tmp_path / "bsl-language-server.jar"
    jar_file.write_text("dummy", encoding="utf-8")

    svc = BslLanguageServerService(root_dir=tmp_path)
    res = svc.find_bsl_ls_jar()
    assert res == str(jar_file)

    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    svc_empty = BslLanguageServerService(root_dir=empty_root)
    with patch.dict("os.environ", {"BSL_LS_JAR": str(jar_file)}):
        res_env = svc_empty.find_bsl_ls_jar()
        assert res_env == str(jar_file)


def test_get_status_and_mcp_config() -> None:
    svc = BslLanguageServerService()
    with patch.object(svc, "find_java_executable", return_value="java"):
        with patch.object(svc, "find_bsl_ls_jar", return_value="bsl-ls.jar"):
            st = svc.get_status()
            assert st["available"] is True
            assert st["has_java"] is True
            assert st["has_jar"] is True

            cfg = svc.get_mcp_server_config()
            assert "bsl-language-server" in cfg
            assert cfg["bsl-language-server"]["command"] == "java"
            assert "--protocol" in cfg["bsl-language-server"]["args"]


def test_run_diagnostics_on_file(tmp_path: Path) -> None:
    svc = BslLanguageServerService()
    test_file = tmp_path / "Module.bsl"
    test_file.write_text("Процедура Тест()\nКонецПроцедуры", encoding="utf-8")

    with patch.object(svc, "get_status", return_value={"available": False}):
        assert svc.run_diagnostics_on_file(test_file) == []

    mock_status = {
        "available": True,
        "java_executable": "java",
        "bsl_ls_jar": "bsl-ls.jar",
    }
    with patch.object(svc, "get_status", return_value=mock_status):
        mock_output = json.dumps([
            {"code": "BSLLS_UNUSED_VAR", "line": 5, "severity": "WARNING", "message": "Неиспользуемая переменная"}
        ])
        mock_proc = MagicMock(returncode=0, stdout=mock_output, stderr="")
        with patch("subprocess.run", return_value=mock_proc):
            res = svc.run_diagnostics_on_file(test_file)
            assert len(res) == 1
            assert res[0].code == "BSLLS_UNUSED_VAR"
            assert res[0].line == 5

        bad_proc = MagicMock(returncode=1, stdout="", stderr="Error")
        with patch("subprocess.run", return_value=bad_proc):
            assert svc.run_diagnostics_on_file(test_file) == []

        corrupted_proc = MagicMock(returncode=0, stdout="NOT_A_JSON", stderr="")
        with patch("subprocess.run", return_value=corrupted_proc):
            assert svc.run_diagnostics_on_file(test_file) == []

        with patch("subprocess.run", side_effect=RuntimeError("Subprocess failed")):
            assert svc.run_diagnostics_on_file(test_file) == []
