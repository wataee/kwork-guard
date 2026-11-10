from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any

logger = logging.getLogger("kwork-guard.fs")
_IO_LOCK = threading.RLock()


def atomic_write_text(
    target_path: Path,
    content: str,
    encoding: str = "utf-8",
    max_retries: int = 5,
    base_delay: float = 0.02,
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with _IO_LOCK:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding=encoding,
                    dir=str(target_path.parent),
                    prefix=f".{target_path.stem}_",
                    suffix=".tmp",
                    delete=False,
                ) as tmp_file:
                    tmp_path = Path(tmp_file.name)
                    tmp_file.write(content)
                    tmp_file.flush()
                    os.fsync(tmp_file.fileno())
                os.replace(tmp_path, target_path)
                return
            except (PermissionError, OSError) as exc:
                last_error = exc
                if tmp_path and tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError as cleanup_err:
                        logger.debug("Temp file cleanup failed: %s", cleanup_err)
                time.sleep(base_delay * (2 ** attempt))
            except Exception as exc:
                if tmp_path and tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError as cleanup_err:
                        logger.debug("Temp file cleanup failed: %s", cleanup_err)
                raise exc

        if last_error:
            raise last_error


def atomic_write_json(
    target_path: Path,
    data: Any,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> None:
    serialized = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent)
    atomic_write_text(target_path, serialized, encoding="utf-8")


def write_bsl_with_bom(target_path: Path, code: str) -> None:
    clean_code = code.lstrip("\ufeff")
    atomic_write_text(target_path, clean_code, encoding="utf-8-sig")
