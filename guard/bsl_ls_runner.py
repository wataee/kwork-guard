from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("kwork-guard.bsl-ls")
log_handler = RotatingFileHandler(
    LOGS_DIR / "cockpit.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
logger.addHandler(log_handler)
logger.setLevel(logging.INFO)


@dataclass
class BslLsDiagnostic:
    code: str
    line: int
    severity: str
    message: str


class BslLanguageServerService:
    def __init__(self, root_dir: Optional[Path] = None) -> None:
        self.root_dir = root_dir if root_dir else ROOT_DIR

    def find_java_executable(self) -> Optional[str]:
        direct_java = shutil.which("java")
        if direct_java:
            return direct_java

        java_home = os.getenv("JAVA_HOME")
        if java_home:
            cand = Path(java_home) / "bin" / ("java.exe" if sys.platform == "win32" else "java")
            if cand.is_file():
                return str(cand)

        if sys.platform == "win32":
            search_roots = [
                Path(os.getenv("ProgramFiles", "C:\\Program Files")),
                Path(os.getenv("ProgramFiles(x86)", "C:\\Program Files (x86)")),
            ]
            subfolders = ["Java", "Eclipse Adoptium", "BellSoft", "Microsoft", "Zulu"]
            for s_root in search_roots:
                if not s_root.is_dir():
                    continue
                for sub in subfolders:
                    target_dir = s_root / sub
                    if target_dir.is_dir():
                        for j_exe in target_dir.rglob("java.exe"):
                            if "bin" in j_exe.parts:
                                return str(j_exe)

        return None

    def find_bsl_ls_jar(self) -> Optional[str]:
        locations = [
            self.root_dir / "tools" / "bsl-language-server" / "bsl-language-server.jar",
            self.root_dir / "tools" / "bsl-language-server.jar",
            self.root_dir / "bsl-language-server.jar",
        ]
        for loc in locations:
            if loc.is_file():
                return str(loc)

        env_path = os.getenv("BSL_LS_JAR")
        if env_path and Path(env_path).is_file():
            return env_path

        return None

    def get_status(self) -> dict[str, Any]:
        java_exe = self.find_java_executable()
        jar_path = self.find_bsl_ls_jar()
        return {
            "available": bool(java_exe and jar_path),
            "java_executable": java_exe,
            "bsl_ls_jar": jar_path,
            "has_java": bool(java_exe),
            "has_jar": bool(jar_path),
        }

    def get_mcp_server_config(self) -> dict[str, Any]:
        java_exe = self.find_java_executable() or "java"
        jar_path = self.find_bsl_ls_jar() or "tools/bsl-language-server/bsl-language-server.jar"
        return {
            "bsl-language-server": {
                "command": java_exe,
                "args": [
                    "-jar",
                    jar_path,
                    "mcp",
                    "--protocol",
                    "stdio",
                ],
            }
        }

    def run_diagnostics_on_file(self, file_path: Path) -> list[BslLsDiagnostic]:
        status = self.get_status()
        if not status["available"]:
            logger.info("BSL LS not available for diagnostics on %s", file_path)
            return []

        java_exe = status["java_executable"]
        jar_path = status["bsl_ls_jar"]

        cmd = [
            java_exe,
            "-jar",
            jar_path,
            "analyze",
            "--src",
            str(file_path),
            "--format",
            "json",
        ]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if res.returncode != 0 and not res.stdout:
                logger.warning("BSL LS exited with code %s: %s", res.returncode, res.stderr)
                return []

            diagnostics: list[BslLsDiagnostic] = []
            if res.stdout.strip():
                try:
                    data = json.loads(res.stdout)
                    items = data if isinstance(data, list) else data.get("issues", [])
                    for item in items:
                        diagnostics.append(
                            BslLsDiagnostic(
                                code=item.get("code", "BSLLS_UNKNOWN"),
                                line=int(item.get("line", 1)),
                                severity=item.get("severity", "WARNING").upper(),
                                message=item.get("message", "Диагностика BSL Language Server"),
                            )
                        )
                except Exception as json_err:
                    logger.warning("Failed to parse BSL LS output: %s", json_err)

            return diagnostics
        except Exception as exc:
            logger.error("Exception during BSL LS execution: %s", exc)
            return []
