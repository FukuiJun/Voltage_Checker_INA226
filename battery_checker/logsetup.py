"""エラー・警告を app.log に出す（最大 1 MB、3 世代）。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 2  # app.log + app.log.1 + app.log.2 = 3 世代


def setup_logging(log_file: Path) -> None:
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
        )
    except OSError:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(fmt)
    root.addHandler(handler)

    def excepthook(exc_type, exc, tb):
        logging.getLogger("unhandled").error("未処理の例外", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook
