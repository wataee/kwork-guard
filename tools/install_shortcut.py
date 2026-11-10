from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.generate_icon import generate_cockpit_icon
LOGS_DIR = ROOT_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("cockpit.installer")
log_handler = RotatingFileHandler(
    LOGS_DIR / "cockpit.log",
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
logger.addHandler(log_handler)
logger.setLevel(logging.INFO)


def get_desktop_directory() -> Path:
    if sys.platform == "win32":
        try:
            cmd = ["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
            res = proc.stdout.strip()
            if res and Path(res).is_dir():
                return Path(res)
        except Exception as exc:
            logger.warning("PowerShell Desktop path resolve error: %s", exc)

    candidate = Path.home() / "Desktop"
    if candidate.is_dir():
        return candidate
    onedrive_desktop = Path.home() / "OneDrive" / "Desktop"
    if onedrive_desktop.is_dir():
        return onedrive_desktop
    return candidate


def create_windows_shortcut(
    shortcut_path: Path,
    target_path: Path,
    arguments: str,
    working_dir: Path,
    icon_path: Path,
    description: str = "Kwork Cockpit & Guard 2.0",
) -> bool:
    ps_script = f"""
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut('{shortcut_path}')
$s.TargetPath = '{target_path}'
$s.Arguments = '{arguments}'
$s.WorkingDirectory = '{working_dir}'
$s.IconLocation = '{icon_path}, 0'
$s.Description = '{description}'
$s.Save()
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.returncode == 0
    except Exception as exc:
        logger.error("Shortcut creation failed via PowerShell: %s", exc)
        return False


def install_shortcut(target_dir: Path | None = None) -> tuple[bool, Path]:
    dest_folder = target_dir if target_dir else get_desktop_directory()
    dest_folder.mkdir(parents=True, exist_ok=True)
    shortcut_file = dest_folder / "Kwork Cockpit.lnk"

    icon_file = ROOT_DIR / "assets" / "cockpit.ico"
    if not icon_file.is_file():
        generate_cockpit_icon(icon_file)

    launcher_file = ROOT_DIR / "launch_cockpit.pyw"

    py_exe = Path(sys.executable)
    pyw_candidate = py_exe.with_name("pythonw.exe")
    pythonw_path = pyw_candidate if pyw_candidate.is_file() else py_exe

    ok = create_windows_shortcut(
        shortcut_path=shortcut_file,
        target_path=pythonw_path,
        arguments=str(launcher_file),
        working_dir=ROOT_DIR,
        icon_path=icon_file,
    )
    return ok, shortcut_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Установка ярлыка Kwork Cockpit на рабочий стол")
    parser.add_argument("--target-dir", type=str, default=None, help="Каталог размещения ярлыка")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    target_path = Path(args.target_dir) if args.target_dir else None
    success, lnk = install_shortcut(target_path)
    if success:
        sys.stdout.write(f"[OK] Shortcut created: {lnk}\n")
        sys.exit(0)
    else:
        sys.stderr.write(f"[FAIL] Shortcut creation error: {lnk}\n")
        sys.exit(1)
