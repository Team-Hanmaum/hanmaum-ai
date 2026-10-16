"""Durable registration on one host's shared local SQLite file."""

import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.errors import AnalysisFailure
from app.v2.comparison import Fingerprint, RequestDigester
from app.v2.schemas import AnalysisRequest

SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE receipts (
    contract_version TEXT NOT NULL,
    request_id TEXT NOT NULL,
    canonicalization_version TEXT NOT NULL,
    hmac_key_version TEXT NOT NULL,
    digest BLOB NOT NULL CHECK(typeof(digest) = 'blob' AND length(digest) = 32),
    PRIMARY KEY (contract_version, request_id)
) WITHOUT ROWID
"""


def store_unavailable() -> AnalysisFailure:
    return AnalysisFailure(
        503, "RECEIPT_STORE_UNAVAILABLE", "수신 기록 저장소를 사용할 수 없습니다."
    )


@dataclass(frozen=True)
class Receipt:
    contract_version: str
    request_id: UUID
    fingerprint: Fingerprint


class ReceiptStore(Protocol):
    def get(self, contract_version: str, request_id: UUID) -> Receipt | None: ...

    def register(self, receipt: Receipt) -> Receipt | None:
        """Commit a new receipt and return None, or return the existing winner. Never overwrite."""
        ...


class SQLiteReceiptStore:
    def __init__(self, path: Path, *, lock_timeout_seconds: float):
        if not path.is_absolute() or not path.name or str(path).startswith("\\\\"):
            raise ValueError("An absolute local SQLite file path is required")
        if not math.isfinite(lock_timeout_seconds) or lock_timeout_seconds < 0:
            raise ValueError("An explicit finite nonnegative SQLite lock timeout is required")
        self.path = path.resolve()
        self.lock_timeout_seconds = lock_timeout_seconds

    def _connect(self, *, create: bool = False) -> sqlite3.Connection:
        # Missing runtime files must fail closed, not silently recreate an empty store.
        connection = sqlite3.connect(
            self.path.as_uri() + ("?mode=rwc" if create else "?mode=rw"),
            uri=True,
            timeout=self.lock_timeout_seconds,
            isolation_level=None,
            autocommit=sqlite3.LEGACY_TRANSACTION_CONTROL,
        )
        try:
            connection.execute("PRAGMA synchronous = FULL")
            if connection.execute("PRAGMA journal_mode").fetchone()[0] in {"off", "memory"}:
                raise store_unavailable()
            if not create:
                self._check_schema(connection)
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _check_schema(connection: sqlite3.Connection) -> None:
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise store_unavailable()
        columns = connection.execute("PRAGMA table_info(receipts)").fetchall()
        if [(col[1], col[2], col[3], col[5]) for col in columns] != [
            ("contract_version", "TEXT", 1, 1),
            ("request_id", "TEXT", 1, 2),
            ("canonicalization_version", "TEXT", 1, 0),
            ("hmac_key_version", "TEXT", 1, 0),
            ("digest", "BLOB", 1, 0),
        ]:
            raise store_unavailable()

    def initialize(self) -> None:
        """Explicit provisioning only; never use at runtime to repair lost receipts."""
        try:
            with closing(self._connect(create=True)) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
                ).fetchall()
                if version == 0 and not tables:
                    connection.execute(SCHEMA)
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._check_schema(connection)
        except (sqlite3.Error, OSError):
            raise store_unavailable() from None

    @staticmethod
    def _read(
        connection: sqlite3.Connection, contract_version: str, request_id: UUID
    ) -> Receipt | None:
        row = connection.execute(
            "SELECT canonicalization_version, hmac_key_version, digest FROM receipts "
            "WHERE contract_version = ? AND request_id = ?",
            (contract_version, str(request_id)),
        ).fetchone()
        return Receipt(contract_version, request_id, Fingerprint(*row)) if row else None

    def get(self, contract_version: str, request_id: UUID) -> Receipt | None:
        try:
            with closing(self._connect()) as connection:
                return self._read(connection, contract_version, request_id)
        except (sqlite3.Error, OSError):
            raise store_unavailable() from None

    def register(self, receipt: Receipt) -> Receipt | None:
        try:
            with closing(self._connect()) as connection, connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = self._read(connection, receipt.contract_version, receipt.request_id)
                if existing is not None:
                    return existing
                connection.execute(
                    "INSERT INTO receipts VALUES (?, ?, ?, ?, ?)",
                    (
                        receipt.contract_version,
                        str(receipt.request_id),
                        receipt.fingerprint.canonicalization_version,
                        receipt.fingerprint.hmac_key_version,
                        receipt.fingerprint.digest,
                    ),
                )
                # The context commits before returning; commit failure cannot authorize a call.
            return None
        except (sqlite3.Error, OSError):
            raise store_unavailable() from None


class ReceiptGate:
    def __init__(self, store: ReceiptStore, digester: RequestDigester):
        self.store = store
        self.digester = digester

    def claim(self, request: AnalysisRequest) -> None:
        existing = self.store.get("v2", request.request_id)
        if existing is None:
            receipt = Receipt("v2", request.request_id, self.digester.fingerprint(request))
            existing = self.store.register(receipt)
        if existing is not None:
            if self.digester.matches(request, existing.fingerprint):
                raise AnalysisFailure(409, "REQUEST_ALREADY_RECEIVED", "이미 접수한 요청입니다.")
            raise AnalysisFailure(409, "REQUEST_ID_REUSED", "다른 본문에 사용된 요청 ID입니다.")
