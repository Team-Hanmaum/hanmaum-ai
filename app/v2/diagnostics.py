"""Bounded, process-local failure counters and logs with no external error details."""

import logging
import sqlite3
from collections import Counter
from enum import StrEnum
from threading import Lock


class FailureEvent(StrEnum):
    HTTP_SEND_TIMEOUT = "http.send_timeout"
    HTTP_DISCONNECT = "http.disconnect"
    COMPARISON_CONFIGURATION = "comparison.configuration"
    COMPARISON_RULE_MISSING = "comparison.rule_missing"
    COMPARISON_KEY_MISSING = "comparison.key_missing"
    COMPARISON_FAILED = "comparison.failed"
    COMPARISON_METADATA = "comparison.invalid_metadata"
    RECEIPT_SCHEMA = "receipt.invalid_schema"
    RECEIPT_DURABILITY = "receipt.unsafe_durability"
    RECEIPT_INPUT = "receipt.invalid_input"
    RECEIPT_INITIALIZE = "receipt.initialize_failed"
    RECEIPT_READ = "receipt.read_failed"
    RECEIPT_REGISTER = "receipt.register_failed"
    PROVIDER_CONFIGURATION = "provider.not_configured"
    PROVIDER_TIMEOUT = "provider.timeout"
    PROVIDER_FAILED = "provider.failed"
    PROVIDER_LATE_FAILURE = "provider.late_failure"
    PROVIDER_INVALID_RESPONSE = "provider.invalid_response"


_logger = logging.getLogger(__name__)
_lock = Lock()
_counts: Counter[tuple[str, str]] = Counter()
_sqlite_kinds = {
    sqlite3.SQLITE_BUSY: "sqlite_busy",
    sqlite3.SQLITE_LOCKED: "sqlite_locked",
    sqlite3.SQLITE_READONLY: "sqlite_readonly",
    sqlite3.SQLITE_IOERR: "sqlite_io",
    sqlite3.SQLITE_CORRUPT: "sqlite_corrupt",
    sqlite3.SQLITE_NOTADB: "sqlite_not_database",
    sqlite3.SQLITE_CANTOPEN: "sqlite_cannot_open",
    sqlite3.SQLITE_CONSTRAINT: "sqlite_constraint",
}


def _failure_kind(error: Exception | None) -> str:
    # Never use str/repr, arbitrary class names, paths, SQL or traceback locals.
    if error is None:
        return "rejected"
    if isinstance(error, sqlite3.Error):
        code = getattr(error, "sqlite_errorcode", None)
        return (
            _sqlite_kinds.get(code & 255, "sqlite_other") if type(code) is int else "sqlite_other"
        )
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, OSError):
        return "os_error"
    if isinstance(error, ValueError):
        return "value_error"
    return "other"


def record_failure(event: FailureEvent, error: Exception | None = None) -> None:
    if not isinstance(event, FailureEvent):
        raise TypeError("A fixed failure event is required")
    kind = _failure_kind(error)
    with _lock:
        _counts[(event.value, kind)] += 1
    _logger.warning(
        "v2 failure event=%s kind=%s",
        event.value,
        kind,
        extra={"failure_event": event.value, "failure_kind": kind},
    )


def failure_counts() -> dict[tuple[str, str], int]:
    """Snapshot for a future metrics exporter; resets on process restart."""
    with _lock:
        return dict(_counts)
