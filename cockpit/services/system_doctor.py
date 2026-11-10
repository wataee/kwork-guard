from __future__ import annotations

import importlib.util
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import socket
import sys
from typing import Any

from guard.config import is_antigravity_mcp_configured, load_config
from tools.order import get_orders_dir
from cockpit.services.onec_runner import OneCRunnerService
from cockpit.services.guard_runner import GuardRunnerService

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = ROOT_DIR / "config.json"
REQ_FILE = ROOT_DIR / "requirements.txt"

logger = logging.getLogger("cockpit.system_doctor")
log_handler = RotatingFileHandler(
    LOGS_DIR / "cockpit.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
logger.addHandler(log_handler)
logger.setLevel(logging.INFO)


class SystemDoctorService:
    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir if root_dir else ROOT_DIR
        self.guard_runner = GuardRunnerService(self.root_dir)
        self.onec_runner = OneCRunnerService()

    def run_full_diagnostics(self) -> list[dict[str, str]]:
        checks: list[dict[str, str]] = []

        py_major, py_minor = sys.version_info[:2]
        if py_major == 3 and py_minor >= 10:
            checks.append({
                "check": "Версия Python",
                "status": "OK",
                "message": f"Python {sys.version.split()[0]} удовлетворяет требованиям (>=3.10)",
                "fix": "",
            })
        else:
            checks.append({
                "check": "Версия Python",
                "status": "FAIL",
                "message": f"Python {sys.version.split()[0]} устарел",
                "fix": "Установите Python 3.11 или новее",
            })

        req_path = self.root_dir / "requirements.txt"
        missing_pkgs: list[str] = []
        if req_path.is_file():
            for line in req_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    pkg_name = line.split(">=")[0].split("==")[0].strip().replace("-", "_")
                    if pkg_name and importlib.util.find_spec(pkg_name) is None:
                        missing_pkgs.append(pkg_name)

        if not missing_pkgs:
            checks.append({
                "check": "Python-зависимости",
                "status": "OK",
                "message": "Все требуемые пакеты установлены",
                "fix": "",
            })
        else:
            checks.append({
                "check": "Python-зависимости",
                "status": "FAIL",
                "message": f"Отсутствуют пакеты: {', '.join(missing_pkgs)}",
                "fix": "Выполните: pip install -r requirements.txt",
            })

        git_path = shutil.which("git")
        if git_path:
            checks.append({
                "check": "Git-клиент",
                "status": "OK",
                "message": f"Git доступен: {git_path}",
                "fix": "",
            })
        else:
            checks.append({
                "check": "Git-клиент",
                "status": "FAIL",
                "message": "Git не найден в PATH",
                "fix": "Установите Git for Windows и добавьте в PATH",
            })

        code_path = shutil.which("code") or shutil.which("code.cmd")
        if not code_path and sys.platform == "win32":
            local_appdata = os.getenv("LOCALAPPDATA", "")
            if local_appdata:
                cand = Path(local_appdata) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd"
                if cand.is_file():
                    code_path = str(cand)

        if code_path:
            checks.append({
                "check": "VS Code",
                "status": "OK",
                "message": f"VS Code обнаружен: {code_path}",
                "fix": "",
            })
        else:
            checks.append({
                "check": "VS Code",
                "status": "WARN",
                "message": "Команда 'code' не найдена в системе",
                "fix": "Установите VS Code и включите команду в PATH",
            })

        roo_installed = False
        extensions_dir = Path.home() / ".vscode" / "extensions"
        if extensions_dir.is_dir():
            for ext_dir in extensions_dir.iterdir():
                if "roo-cline" in ext_dir.name.lower():
                    roo_installed = True
                    break

        if roo_installed:
            checks.append({
                "check": "Roo Code (расширение)",
                "status": "OK",
                "message": "Расширение Roo Code установлено в VS Code",
                "fix": "",
            })
        else:
            checks.append({
                "check": "Roo Code (расширение)",
                "status": "WARN",
                "message": "Roo Code не обнаружен в каталоге расширений",
                "fix": "Установите расширение Roo Code (Roo Cline) из маркетплейса VS Code",
            })

        orders_dir = get_orders_dir()
        can_write_orders = False
        try:
            orders_dir.mkdir(parents=True, exist_ok=True)
            test_file = orders_dir / ".test_write"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
            can_write_orders = True
        except Exception as exc:
            logger.warning("Orders dir test write failure: %s", exc)

        if can_write_orders:
            checks.append({
                "check": "Каталог заказов",
                "status": "OK",
                "message": f"Доступен на чтение и запись: {orders_dir}",
                "fix": "",
            })
        else:
            checks.append({
                "check": "Каталог заказов",
                "status": "FAIL",
                "message": f"Нет прав на запись в {orders_dir}",
                "fix": "Проверьте права доступа к директории C:\\Orders",
            })

        cfg = load_config()
        if cfg and "limits" in cfg and "upstream" in cfg:
            checks.append({
                "check": "Файл конфигурации config.json",
                "status": "OK",
                "message": "Конфигурация валидна",
                "fix": "",
            })
        else:
            checks.append({
                "check": "Файл конфигурации config.json",
                "status": "FAIL",
                "message": "Файл config.json поврежден или неполный",
                "fix": "Пересоздайте config.json со стандартной структурой",
            })

        guard_online = self.guard_runner.is_guard_running(port=8766)
        if guard_online:
            checks.append({
                "check": "Шлюз kwork-guard (порт 8766)",
                "status": "OK",
                "message": "Шлюз запущен и отвечает на /health",
                "fix": "",
            })
        else:
            checks.append({
                "check": "Шлюз kwork-guard (порт 8766)",
                "status": "WARN",
                "message": "Шлюз сейчас остановлен",
                "fix": "Запустите шлюз кнопкой 'Запустить Guard' в шапке Cockpit",
            })

        edt_online = self.guard_runner.is_edt_running()
        if edt_online:
            checks.append({
                "check": "Сервер 1C:EDT MCP (порт 8765)",
                "status": "OK",
                "message": "EDT-MCP сервер активен и отвечает",
                "fix": "",
            })
        else:
            checks.append({
                "check": "Сервер 1C:EDT MCP (порт 8765)",
                "status": "WARN",
                "message": "Порт 8765 (EDT MCP) не отвечает",
                "fix": "Запустите 1C:EDT и включите MCP-сервер через меню Настройки -> EDT MCP",
            })

        exe_1c = self.onec_runner.find_1cv8c_executable()
        if exe_1c:
            checks.append({
                "check": "1С:Предприятие (тонкий клиент)",
                "status": "OK",
                "message": f"Найден клиент: {exe_1c}",
                "fix": "",
            })
        else:
            checks.append({
                "check": "1С:Предприятие (тонкий клиент)",
                "status": "WARN",
                "message": "1cv8c.exe не найден в стандартных каталогах",
                "fix": "Укажите путь к 1cv8c.exe во вкладке Настройки Cockpit",
            })

        from guard.bsl_ls_runner import BslLanguageServerService
        bsl_ls_svc = BslLanguageServerService(self.root_dir)
        ls_status = bsl_ls_svc.get_status()
        if ls_status["available"]:
            checks.append({
                "check": "BSL Language Server (LSP/MCP)",
                "status": "OK",
                "message": f"Java и BSL LS готовы: {ls_status['bsl_ls_jar']}",
                "fix": "",
            })
        elif ls_status["has_java"]:
            checks.append({
                "check": "BSL Language Server (LSP/MCP)",
                "status": "WARN",
                "message": "Java обнаружена, но bsl-language-server.jar не найден в tools/",
                "fix": "Поместите bsl-language-server.jar в каталог tools/bsl-language-server/",
            })
        else:
            checks.append({
                "check": "BSL Language Server (LSP/MCP)",
                "status": "WARN",
                "message": "Java (JRE/JDK) не найдена в системе",
                "fix": "Установите OpenJDK/Temurin 17+ для поддержки BSL Language Server в режиме MCP",
            })

        agy_online, agy_url = self.guard_runner.check_antigravity_status()
        active_prov = self.guard_runner.get_active_provider()
        mcp_ok, mcp_url = is_antigravity_mcp_configured()
        if mcp_ok:
            extra = f"Шлюз {agy_url} онлайн" if agy_online else "локальный шлюз :3000 не требуется для чата Antigravity"
            checks.append({
                "check": "Antigravity Gateway (LLM Dual-Backend)",
                "status": "OK",
                "message": f"MCP Antigravity настроен ({mcp_url}); {extra}; провайдер: {active_prov}",
                "fix": "",
            })
        elif agy_online:
            checks.append({
                "check": "Antigravity Gateway (LLM Dual-Backend)",
                "status": "OK",
                "message": f"Шлюз доступен ({agy_url}), активен провайдер: {active_prov}",
                "fix": "",
            })
        else:
            checks.append({
                "check": "Antigravity Gateway (LLM Dual-Backend)",
                "status": "WARN",
                "message": f"MCP edt-guard не найден в ~/.gemini/config/mcp_config.json (провайдер: {active_prov})",
                "fix": "Нажмите 'Подготовить рабочее место' в шапке Cockpit",
            })

        return checks
