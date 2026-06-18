"""Request-id propagation and lightweight structured metrics logging.

A per-request id flows from the ``X-Request-ID`` header (or is generated) into a
context var, the response body/header, and every log line, so a single request can
be traced end to end. ``log_metrics`` emits one structured line per request with
stage latencies and candidate/seam counters. No external metrics backend — stdlib
``logging`` only.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar

LOGGER_NAME = "seamless"

_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_REQUEST_ID_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")
_MAX_REQUEST_ID_LEN = 128


def new_request_id() -> str:
    return uuid.uuid4().hex


def set_request_id(request_id: str) -> str:
    clean = _REQUEST_ID_PATTERN.sub("-", request_id)[:_MAX_REQUEST_ID_LEN].strip("-_")
    if not clean:
        clean = new_request_id()
    _request_id.set(clean)
    return clean


def get_request_id() -> str:
    return _request_id.get()


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


_configured = False


def configure_logging() -> None:
    """Idempotently attach a request-id-aware handler to the app logger."""
    global _configured
    if _configured:
        return
    logger = logging.getLogger(LOGGER_NAME)
    logger.addFilter(_RequestIdFilter())
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [req=%(request_id)s] %(name)s: %(message)s"
            )
        )
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _configured = True


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def log_metrics(event: str, **fields) -> None:
    """Emit one structured ``key=value`` metrics line, tagged with the request id."""
    parts = " ".join(f"{key}={value}" for key, value in fields.items())
    get_logger().info("%s %s", event, parts)
