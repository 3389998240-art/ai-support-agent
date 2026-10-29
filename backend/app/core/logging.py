from __future__ import annotations

import logging
import sys

from app.core.config import get_settings
from app.core.context import get_request_id

_CONFIGURED = False


class RequestIdFilter(logging.Filter):
    """把当前请求的 request_id 注入日志记录，方便 grep 一次调用的全链路。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def configure_logging() -> None:
    """统一的日志格式，方便在 Docker 里 grep 一次请求的全链路。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(request_id)s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.handlers = [handler]
    # 第三方库静音，避免淹没业务日志
    for noisy in ("httpx", "httpcore", "urllib3", "faiss"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
