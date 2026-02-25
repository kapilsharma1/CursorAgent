"""
Centralized logging configuration. Call configure_logging() at app startup.
Uses LOG_LEVEL from config (env: LOG_LEVEL). Format: timestamp | level | logger | message.
"""

import logging
import sys
from typing import Optional


def configure_logging(log_level: Optional[str] = None) -> None:
    """
    Configure root logger and all library loggers.
    log_level: DEBUG, INFO, WARNING, ERROR. Default from config or INFO.
    """
    if log_level is None:
        try:
            from config import get_settings
            log_level = get_settings().log_level.upper()
        except Exception:
            log_level = "INFO"

    level = getattr(logging, log_level, logging.INFO)
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt, datefmt=date_fmt))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Reduce noise from third-party libs (optional)
    for name in ("httpx", "httpcore", "openai", "langchain", "langgraph", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)
