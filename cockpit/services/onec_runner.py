from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Optional

from guard.config import load_config

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("cockpit.onec_runner")
log_handler = RotatingFileHandler(
    LOGS_DIR / "cockpit.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
logger.addHandler(log_handler)
logger.setLevel(logging.INFO)


class OneCRunnerService:
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = base_dir

    def find_1cv8c_executable(self, configured_path: str = "") -> Optional[Path]:
        if configured_path:
            p = Path(configured_path)
            if p.is_file():
                return p

        cfg = load_config()
        cfg_path = cfg.get("onec", {}).get("thin_client_path", "")
        if cfg_path:
            cp = Path(cfg_path)
            if cp.is_file():
                return cp

        candidates: list[Path] = []
        search_dirs = [
            Path("C:/Program Files/1cv8"),
            Path("C:/Program Files (x86)/1cv8"),
        ]
        for base in search_dirs:
            if base.is_dir():
                for bin_file in base.glob("*/bin/1cv8c.exe"):
                    if bin_file.is_file():
                        candidates.append(bin_file)

        if not candidates:
            return None

        def version_key(path: Path) -> tuple[int, ...]:
            parts = path.parent.parent.name.split(".")
            nums: list[int] = []
            for item in parts:
                if item.isdigit():
                    nums.append(int(item))
            return tuple(nums)

        candidates.sort(key=version_key, reverse=True)
        return candidates[0]

    def launch_epf_in_thin_client(
        self,
        ib_path: str,
        epf_path: Path,
        exec_path: str = "",
    ) -> tuple[bool, str]:
        exe = self.find_1cv8c_executable(exec_path)
        if not exe:
            return False, "Исполняемый файл 1cv8c.exe не найден на компьютере"

        if not epf_path.is_file():
            return False, f"Файл обработки {epf_path} не существует"

        cmd = [str(exe), "ENTERPRISE", "/F", str(ib_path), "/Execute", str(epf_path)]
        try:
            subprocess.Popen(cmd)
            return True, f"Запущено в 1С:Предприятие ({exe.name})"
        except Exception as exc:
            logger.error("1C launch error: %s", exc)
            return False, str(exc)

    def open_in_vscode(self, order_dir: Path, custom_code_bin: str = "") -> tuple[bool, str]:
        if not order_dir.is_dir():
            return False, f"Каталог {order_dir} не найден"

        code_bin = custom_code_bin or shutil.which("code") or shutil.which("code.cmd")

        if not code_bin and sys.platform == "win32":
            local_appdata = os.getenv("LOCALAPPDATA", "")
            if local_appdata:
                candidate = Path(local_appdata) / "Programs" / "Microsoft VS Code" / "bin" / "code.cmd"
                if candidate.is_file():
                    code_bin = str(candidate)

        if not code_bin:
            return False, "Команда 'code' (VS Code) не найдена в PATH или стандартном каталоге"

        try:
            subprocess.Popen([code_bin, str(order_dir)], shell=(sys.platform == "win32"))
            return True, f"Папка {order_dir.name} открыта в VS Code"
        except Exception as exc:
            logger.error("VS Code launch error: %s", exc)
            return False, str(exc)
