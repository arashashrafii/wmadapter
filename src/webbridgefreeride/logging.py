from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .security import redact


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.msg = redact(str(record.msg))
        if record.args:
            record.args = redact(record.args)
        return super().format(record)


def configure_logging(config: dict) -> None:
    level_name = str(config.get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    log_file = config.get("file", "webbridgefreeride.log")
    max_bytes = int(config.get("max_bytes", 1_000_000))
    backup_count = int(config.get("backup_count", 3))

    formatter = RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        path = Path(log_file)
        if path.parent != Path('.'):
            path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count))

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    for handler in handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)
        root.addHandler(handler)
