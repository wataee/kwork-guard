from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import tkinter.messagebox

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
BOOT_LOG = LOG_DIR / "cockpit_boot.log"

LOGGER = logging.getLogger("cockpit_boot")
HANDLER = RotatingFileHandler(BOOT_LOG, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
HANDLER.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s"))
LOGGER.addHandler(HANDLER)
LOGGER.setLevel(logging.INFO)


def main() -> None:
    LOGGER.info("Starting Kwork Cockpit 2.0...")
    try:
        from cockpit.app import CockpitApp

        app = CockpitApp(base_dir=BASE_DIR)
        app.mainloop()
        LOGGER.info("Cockpit finished normally.")
    except Exception as error:
        LOGGER.critical("Unhandled error during Cockpit execution: %s", error, exc_info=True)
        try:
            tkinter.messagebox.showerror(
                "Ошибка запуска Kwork Cockpit",
                f"Произошла критическая ошибка при запуске:\n{error}\n\nПодробности в logs/cockpit_boot.log",
            )
        except Exception:
            LOGGER.error("Failed to show error dialog", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
