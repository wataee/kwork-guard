from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
import urllib.error
import urllib.request

from guard.config import get_active_provider, load_config, set_active_provider
from guard.dual_backend import DualBackendClient
from tools.order import get_orders_dir, repair_order_mcp_config

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR = ROOT_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("cockpit.guard_runner")
if not logger.handlers:
    log_handler = RotatingFileHandler(
        LOGS_DIR / "cockpit.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    logger.addHandler(log_handler)
    logger.setLevel(logging.INFO)


class GuardRunnerService:
    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = root_dir if root_dir else ROOT_DIR
        self.logs_dir = self.root_dir / "logs"
        self.state_dir = self.root_dir / "state"
        self.pid_file = self.state_dir / "guard.pid"
        self.stdout_log = self.logs_dir / "guard_stdout.log"
        self.dual_client = DualBackendClient(root_dir=self.root_dir)

    def is_guard_running(self, host: str = "127.0.0.1", port: int = 8766) -> bool:
        url = f"http://{host}:{port}/health"
        req = urllib.request.Request(url, headers={"User-Agent": "cockpit"})
        try:
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return bool(data.get("ok"))
        except Exception as exc:
            logger.debug("Guard ping error: %s", exc)
            return False

    def is_edt_running(self, edt_url: str = "") -> bool:
        target_url = edt_url if edt_url else load_config().get("upstream", {}).get("edt_url", "http://127.0.0.1:8765/mcp")
        try:
            req = urllib.request.Request(target_url, headers={"User-Agent": "cockpit"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                return resp.status in (200, 400, 404, 405)
        except urllib.error.HTTPError as http_err:
            return http_err.code in (200, 400, 404, 405)
        except Exception as exc:
            logger.debug("EDT ping error: %s", exc)
            return False

    def check_antigravity_status(self) -> tuple[bool, str]:
        return self.dual_client.check_antigravity_health(timeout=0.8)

    def get_active_provider(self) -> str:
        return get_active_provider()

    def switch_provider(self, provider_name: str) -> tuple[bool, str]:
        ok, msg = set_active_provider(provider_name)
        if not ok:
            return False, msg

        port = int(os.getenv("GUARD_PORT", "8766"))
        if self.is_guard_running(port=port):
            url = f"http://127.0.0.1:{port}/admin/provider"
            req = urllib.request.Request(
                url,
                data=json.dumps({"provider": msg}).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "cockpit"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=1.5) as resp:
                    resp.read()
            except Exception as exc:
                logger.warning("Failed to hot-notify guard about provider change: %s", exc)
        return True, msg

    def start_guard_background(self) -> tuple[bool, str]:
        load_config()
        port = int(os.getenv("GUARD_PORT", "8766"))
        if self.is_guard_running(port=port):
            return True, "already_running"

        guard_script = self.root_dir / "edt_guard.py"
        if not guard_script.is_file():
            return False, f"Script not found: {guard_script}"

        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        stdout_handle = self.stdout_log.open("a", encoding="utf-8")

        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        try:
            proc = subprocess.Popen(
                [sys.executable, str(guard_script)],
                cwd=str(self.root_dir),
                stdout=stdout_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            self.pid_file.write_text(str(proc.pid), encoding="utf-8")
        except Exception as exc:
            stdout_handle.close()
            logger.error("Failed to start guard process: %s", exc)
            return False, str(exc)

        for _ in range(25):
            time.sleep(0.2)
            if self.is_guard_running(port=port):
                return True, f"started (PID: {proc.pid})"

        return False, "timeout waiting for guard health endpoint"

    def stop_guard(self) -> tuple[bool, str]:
        port = int(os.getenv("GUARD_PORT", "8766"))
        pid = None
        if self.pid_file.is_file():
            try:
                pid = int(self.pid_file.read_text(encoding="utf-8").strip())
            except Exception as exc:
                logger.warning("Could not parse guard.pid: %s", exc)

        if pid:
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
                else:
                    os.kill(pid, 9)
            except Exception as exc:
                logger.warning("Process termination error for PID %s: %s", pid, exc)

        if self.pid_file.is_file():
            try:
                self.pid_file.unlink()
            except Exception as exc:
                logger.warning("Unlink guard.pid error: %s", exc)

        for _ in range(15):
            time.sleep(0.2)
            if not self.is_guard_running(port=port):
                return True, "stopped"

        return False, "port still active after stop"

    def restart_guard(self) -> tuple[bool, str]:
        self.stop_guard()
        time.sleep(0.5)
        return self.start_guard_background()

    def prepare_workplace(self) -> dict[str, Any]:
        guard_ok, guard_msg = self.start_guard_background()
        edt_ok = self.is_edt_running()
        agy_ok, agy_url = self.check_antigravity_status()
        orders_dir = get_orders_dir()
        dir_accessible = orders_dir.is_dir()
        if not dir_accessible:
            try:
                orders_dir.mkdir(parents=True, exist_ok=True)
                dir_accessible = True
            except Exception as exc:
                logger.error("Orders directory creation error: %s", exc)

        repaired_orders: list[str] = []
        if dir_accessible:
            for item in sorted(orders_dir.iterdir()):
                if item.is_dir() and not item.name.startswith("."):
                    if repair_order_mcp_config(item):
                        repaired_orders.append(item.name)

        return {
            "guard_running": guard_ok,
            "guard_message": guard_msg,
            "edt_running": edt_ok,
            "antigravity_online": agy_ok,
            "antigravity_url": agy_url,
            "active_provider": self.get_active_provider(),
            "orders_dir": str(orders_dir),
            "orders_dir_ready": dir_accessible,
            "repaired_orders_count": len(repaired_orders),
            "repaired_orders": repaired_orders,
        }
