"""Engine log file: <library>/logs/sidecar.log, rotated at 1 MB, 3 old files kept.

stdout is reserved for the one-line readiness message read by the desktop shell, so all
diagnostics go to this file. Do not log personal data (OCR text, phone numbers, e-mails);
log asset ids instead.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup(log_dir: Path, level: int = logging.INFO) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "sidecar.log"
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(FORMAT))
    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if not isinstance(h, RotatingFileHandler)]
    root.addHandler(handler)
    root.setLevel(level)
    # pyvips logs every libvips operation at INFO; keep only its warnings.
    logging.getLogger("pyvips").setLevel(logging.WARNING)

    def log_uncaught(exc_type, exc, tb):
        logging.getLogger("ivsidecar").critical("uncaught exception", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = log_uncaught
    return path
