"""Independent SQLite catalog for API operations and run discovery.

The catalog deliberately does not inspect or depend on LangGraph checkpoint
tables. It stores identities and lifecycle metadata only: never prompts,
checkpoint state, credentials, event payloads, or tool output.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.biodiversity.api.schemas import OperationKind, OperationStatus, OperationView


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RunCatalog(Protocol):
    def create_operation(
        self,
        *,
        operation_id: str,
        thread_id: str,
        kind: OperationKind,
        data_mode: str,
        model_mode: str,
    ) -> OperationView: ...

    def operation(self, operation_id: str) -> OperationView | None: ...

    def operations_for_thread(self, thread_id: str) -> list[OperationView]: ...

    def list_operations(self) -> list[OperationView]: ...

    def thread_exists(self, thread_id: str) -> bool: ...

    def has_active_mutation(self, thread_id: str) -> bool: ...

    def thread_count(self) -> int: ...

    def expired_threads(self, *, before: datetime) -> list[str]: ...

    def report_directories_for_thread(self, thread_id: str) -> list[str]: ...

    def delete_thread(self, thread_id: str) -> int: ...

    def update_operation(
        self,
        operation_id: str,
        *,
        status: OperationStatus | None = None,
        branch_id: str | None = None,
        execution_id: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        last_event_sequence: int | None = None,
        current_checkpoint_id: str | None = None,
        report_directory: str | None = None,
        interrupt_kind: str | None = None,
        error_code: str | None = None,
    ) -> OperationView: ...

    def report_directory(self, operation_id: str) -> str | None: ...

    def recover_incomplete(self) -> int: ...

    def close(self) -> None: ...


class SQLiteRunCatalog:
    """Thread-safe local catalog with a schema owned by the application."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=30
        )
        self._connection.row_factory = sqlite3.Row
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA busy_timeout=30000")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS phase4_operations (
                    operation_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    branch_id TEXT,
                    execution_id TEXT,
                    operation_kind TEXT NOT NULL CHECK (
                        operation_kind IN ('start', 'resume', 'replay', 'fork')
                    ),
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'queued', 'running', 'waiting_for_input',
                            'completed', 'failed'
                        )
                    ),
                    data_mode TEXT NOT NULL CHECK (data_mode IN ('fixture', 'live')),
                    model_mode TEXT NOT NULL CHECK (model_mode IN ('scripted', 'live')),
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    last_event_sequence INTEGER NOT NULL DEFAULT 0,
                    current_checkpoint_id TEXT,
                    report_directory TEXT,
                    interrupt_kind TEXT,
                    error_code TEXT
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS phase4_operations_thread_created
                ON phase4_operations(thread_id, created_at DESC)
                """
            )
            self._connection.commit()

    @staticmethod
    def _view(row: sqlite3.Row) -> OperationView:
        return OperationView(
            operation_id=row["operation_id"],
            thread_id=row["thread_id"],
            branch_id=row["branch_id"],
            execution_id=row["execution_id"],
            kind=row["operation_kind"],
            status=row["status"],
            data_mode=row["data_mode"],
            model_mode=row["model_mode"],
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=(
                datetime.fromisoformat(row["started_at"])
                if row["started_at"]
                else None
            ),
            finished_at=(
                datetime.fromisoformat(row["finished_at"])
                if row["finished_at"]
                else None
            ),
            last_event_sequence=row["last_event_sequence"],
            current_checkpoint_id=row["current_checkpoint_id"],
            interrupt_kind=row["interrupt_kind"],
            error_code=row["error_code"],
        )

    def _rows(self, sql: str, parameters: Sequence[object] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._connection.execute(sql, parameters).fetchall())

    def create_operation(
        self,
        *,
        operation_id: str,
        thread_id: str,
        kind: OperationKind,
        data_mode: str,
        model_mode: str,
    ) -> OperationView:
        created_at = _now()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO phase4_operations (
                    operation_id, thread_id, operation_kind, status,
                    data_mode, model_mode, created_at
                ) VALUES (?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    operation_id,
                    thread_id,
                    kind,
                    data_mode,
                    model_mode,
                    created_at,
                ),
            )
            self._connection.commit()
        operation = self.operation(operation_id)
        if operation is None:  # pragma: no cover - SQLite insert/read invariant
            raise RuntimeError("Catalog operation was not persisted")
        return operation

    def operation(self, operation_id: str) -> OperationView | None:
        rows = self._rows(
            "SELECT * FROM phase4_operations WHERE operation_id = ?",
            (operation_id,),
        )
        return self._view(rows[0]) if rows else None

    def operations_for_thread(self, thread_id: str) -> list[OperationView]:
        return [
            self._view(row)
            for row in self._rows(
                """
                SELECT * FROM phase4_operations
                WHERE thread_id = ? ORDER BY created_at DESC
                """,
                (thread_id,),
            )
        ]

    def list_operations(self) -> list[OperationView]:
        return [
            self._view(row)
            for row in self._rows(
                "SELECT * FROM phase4_operations ORDER BY created_at DESC"
            )
        ]

    def thread_exists(self, thread_id: str) -> bool:
        return bool(
            self._rows(
                "SELECT 1 FROM phase4_operations WHERE thread_id = ? LIMIT 1",
                (thread_id,),
            )
        )

    def has_active_mutation(self, thread_id: str) -> bool:
        return bool(
            self._rows(
                """
                SELECT 1 FROM phase4_operations
                WHERE thread_id = ? AND status IN ('queued', 'running') LIMIT 1
                """,
                (thread_id,),
            )
        )

    def thread_count(self) -> int:
        rows = self._rows("SELECT COUNT(DISTINCT thread_id) AS total FROM phase4_operations")
        return int(rows[0]["total"])

    def expired_threads(self, *, before: datetime) -> list[str]:
        return [
            str(row["thread_id"])
            for row in self._rows(
                """
                SELECT thread_id
                FROM phase4_operations
                GROUP BY thread_id
                HAVING MAX(COALESCE(finished_at, started_at, created_at)) < ?
                   AND SUM(CASE WHEN status IN ('queued', 'running') THEN 1 ELSE 0 END) = 0
                ORDER BY MAX(COALESCE(finished_at, started_at, created_at)) ASC
                """,
                (before.isoformat(),),
            )
        ]

    def report_directories_for_thread(self, thread_id: str) -> list[str]:
        return [
            str(row["report_directory"])
            for row in self._rows(
                """
                SELECT report_directory FROM phase4_operations
                WHERE thread_id = ? AND report_directory IS NOT NULL
                """,
                (thread_id,),
            )
        ]

    def delete_thread(self, thread_id: str) -> int:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM phase4_operations WHERE thread_id = ?",
                (thread_id,),
            )
            self._connection.commit()
            return cursor.rowcount

    def update_operation(
        self,
        operation_id: str,
        *,
        status: OperationStatus | None = None,
        branch_id: str | None = None,
        execution_id: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        last_event_sequence: int | None = None,
        current_checkpoint_id: str | None = None,
        report_directory: str | None = None,
        interrupt_kind: str | None = None,
        error_code: str | None = None,
    ) -> OperationView:
        fields: dict[str, object] = {}
        for key, value in {
            "status": status,
            "branch_id": branch_id,
            "execution_id": execution_id,
            "started_at": started_at.isoformat() if started_at else None,
            "finished_at": finished_at.isoformat() if finished_at else None,
            "last_event_sequence": last_event_sequence,
            "current_checkpoint_id": current_checkpoint_id,
            "report_directory": report_directory,
            "interrupt_kind": interrupt_kind,
            "error_code": error_code,
        }.items():
            if value is not None:
                fields[key] = value
        if not fields:
            operation = self.operation(operation_id)
            if operation is None:
                raise KeyError(operation_id)
            return operation
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._lock:
            cursor = self._connection.execute(
                f"UPDATE phase4_operations SET {assignments} WHERE operation_id = ?",
                (*fields.values(), operation_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(operation_id)
            self._connection.commit()
        operation = self.operation(operation_id)
        if operation is None:  # pragma: no cover - SQLite update/read invariant
            raise KeyError(operation_id)
        return operation

    def report_directory(self, operation_id: str) -> str | None:
        rows = self._rows(
            """
            SELECT report_directory FROM phase4_operations
            WHERE operation_id = ?
            """,
            (operation_id,),
        )
        return rows[0]["report_directory"] if rows else None

    def recover_incomplete(self) -> int:
        """Make interrupted process-local work explicit after an app restart."""

        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE phase4_operations
                SET status = 'failed', finished_at = ?, error_code = 'service_restarted'
                WHERE status IN ('queued', 'running')
                """,
                (_now(),),
            )
            self._connection.commit()
            return cursor.rowcount

    def close(self) -> None:
        with self._lock:
            self._connection.commit()
            self._connection.close()
