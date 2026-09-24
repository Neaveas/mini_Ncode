"""日志配置，仅在启动入口调用。"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger() -> None:
    log_dir = Path(".logs")
    log_dir.mkdir(exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        log_dir / "agent.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logging.basicConfig(level=logging.DEBUG, handlers=[console, file_handler])
