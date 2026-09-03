"""SQLite-backed enrollment persistence."""

from __future__ import annotations

import math
import sqlite3
import struct
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator, Sequence

from ..identity import validate_user_id
from .errors import StorageError

if TYPE_CHECKING:
    from ..enrollment_data import AuthenticationLockout, EnrollmentRecord


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EnrollmentRepository:
    SCHEMA_VERSION = 3
    MAX_AUTH_FAILURES = 3
    AUTH_LOCKOUT_DURATION = timedelta(seconds=60)

    def __init__(
        self,
        data_dir: Path,
        *,
        embedding_dimension: int,
        busy_timeout_ms: int = 5_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if embedding_dimension <= 0:
            raise ValueError("embedding_dimension must be positive")
        if busy_timeout_ms <= 0:
            raise ValueError("busy_timeout_ms must be positive")

        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._database_path = self._data_dir / "authentication.db"
        self._embedding_dimension = embedding_dimension
        self._busy_timeout_ms = busy_timeout_ms
        self._clock = clock or _utc_now
        self._initialize()

    @property
    def database_path(self) -> Path:
        return self._database_path

    @property
    def embedding_dimension(self) -> int:
        return self._embedding_dimension

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self._database_path,
                timeout=self._busy_timeout_ms / 1_000,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {self._busy_timeout_ms}")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        finally:
            if connection is not None:
                connection.close()
            self._secure_database_files()

    def _initialize(self) -> None:
        try:
            with self._connection() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                table_exists = connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'enrollments'
                    """
                ).fetchone() is not None

                if version == 0 and not table_exists:
                    with connection:
                        self._create_enrollments_table(connection, "enrollments")
                        self._create_authentication_lockouts_table(connection)
                        connection.execute(
                            f"PRAGMA user_version = {self.SCHEMA_VERSION}"
                        )
                elif version in {0, 1}:
                    self._migrate_v1_to_v2(connection)
                    self._migrate_v2_to_v3(connection)
                elif version == 2:
                    self._migrate_v2_to_v3(connection)
                elif version == self.SCHEMA_VERSION:
                    connection.execute("SELECT 1 FROM enrollments LIMIT 1")
                    connection.execute("SELECT 1 FROM authentication_lockouts LIMIT 1")
                else:
                    raise StorageError(
                        f"unsupported enrollment database schema version: {version}"
                    )
        except sqlite3.Error as exc:
            raise StorageError(f"failed to initialize enrollment database: {exc}") from exc

    @staticmethod
    def _create_enrollments_table(
        connection: sqlite3.Connection,
        table_name: str,
    ) -> None:
        if table_name not in {"enrollments", "enrollments_v2"}:
            raise ValueError("unsupported enrollment table name")
        connection.execute(
            f"""
            CREATE TABLE {table_name} (
                user_id TEXT NOT NULL PRIMARY KEY
                    CHECK (
                        length(user_id) BETWEEN 1 AND 10
                        AND user_id NOT GLOB '*[^0-9]*'
                    ),
                name TEXT NOT NULL,
                model_version TEXT NOT NULL,
                embedding BLOB NOT NULL,
                embedding_dimension INTEGER NOT NULL
                    CHECK (embedding_dimension > 0),
                updated_at TEXT NOT NULL
            )
            """
        )

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            user_ids = [
                row[0]
                for row in connection.execute("SELECT user_id FROM enrollments")
            ]
            invalid_count = 0
            for user_id in user_ids:
                try:
                    validate_user_id(user_id)
                except ValueError:
                    invalid_count += 1
            if invalid_count:
                raise StorageError(
                    "schema migration blocked by invalid user ID records: "
                    f"{invalid_count}"
                )

            self._create_enrollments_table(connection, "enrollments_v2")
            connection.execute(
                """
                INSERT INTO enrollments_v2 (
                    user_id, name, model_version, embedding,
                    embedding_dimension, updated_at
                )
                SELECT
                    user_id, name, model_version, embedding,
                    embedding_dimension, updated_at
                FROM enrollments
                """
            )
            connection.execute("DROP TABLE enrollments")
            connection.execute("ALTER TABLE enrollments_v2 RENAME TO enrollments")
            connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

    @staticmethod
    def _create_authentication_lockouts_table(
        connection: sqlite3.Connection,
    ) -> None:
        connection.execute(
            """
            CREATE TABLE authentication_lockouts (
                user_id TEXT NOT NULL PRIMARY KEY
                    REFERENCES enrollments(user_id)
                    ON UPDATE CASCADE
                    ON DELETE CASCADE,
                failure_count INTEGER NOT NULL
                    CHECK (failure_count BETWEEN 1 AND 3),
                locked_until TEXT,
                CHECK (
                    (failure_count < 3 AND locked_until IS NULL)
                    OR (failure_count = 3 AND locked_until IS NOT NULL)
                )
            )
            """
        )

    def _migrate_v2_to_v3(self, connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._create_authentication_lockouts_table(connection)
            connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()

    def save(self, record: EnrollmentRecord) -> None:
        validated = self._validate_record(record)
        try:
            with self._connection() as connection:
                with connection:
                    self._upsert(connection, validated)
        except sqlite3.Error as exc:
            raise StorageError(f"failed to save enrollment {record.user_id!r}: {exc}") from exc

    def save_all(self, records: Sequence[EnrollmentRecord]) -> None:
        validated = [self._validate_record(record) for record in records]
        try:
            with self._connection() as connection:
                with connection:
                    for record in validated:
                        self._upsert(connection, record)
        except sqlite3.Error as exc:
            raise StorageError(f"failed to save enrollments: {exc}") from exc

    def import_all(self, records: Sequence[EnrollmentRecord]) -> None:
        """Import records into an empty database in one transaction."""
        validated = [self._validate_record(record) for record in records]
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    existing = connection.execute(
                        "SELECT COUNT(*) FROM enrollments"
                    ).fetchone()[0]
                    if existing:
                        raise StorageError(
                            "마이그레이션 대상 SQLite 등록 테이블이 비어 있지 않습니다."
                        )
                    for record in validated:
                        self._insert(connection, record)
                except Exception:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
        except sqlite3.Error as exc:
            raise StorageError(f"failed to import enrollments: {exc}") from exc

    def _upsert(self, connection: sqlite3.Connection, record: EnrollmentRecord) -> None:
        connection.execute(
            """
            INSERT INTO enrollments (
                user_id,
                name,
                model_version,
                embedding,
                embedding_dimension,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                name = excluded.name,
                model_version = excluded.model_version,
                embedding = excluded.embedding,
                embedding_dimension = excluded.embedding_dimension,
                updated_at = excluded.updated_at
            """,
            (
                record.user_id,
                record.name,
                record.model_version,
                self._encode_embedding(record.embedding),
                self._embedding_dimension,
                record.updated_at,
            ),
        )

    def _insert(self, connection: sqlite3.Connection, record: EnrollmentRecord) -> None:
        connection.execute(
            """
            INSERT INTO enrollments (
                user_id,
                name,
                model_version,
                embedding,
                embedding_dimension,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                record.user_id,
                record.name,
                record.model_version,
                self._encode_embedding(record.embedding),
                self._embedding_dimension,
                record.updated_at,
            ),
        )

    def get(self, user_id: str) -> EnrollmentRecord | None:
        validate_user_id(user_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    """
                    SELECT user_id, name, model_version, embedding,
                           embedding_dimension, updated_at
                    FROM enrollments
                    WHERE user_id = ?
                    """,
                    (user_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"failed to load enrollment {user_id!r}: {exc}") from exc
        return None if row is None else self._record_from_row(row)

    def exists(self, user_id: str) -> bool:
        return self.get(user_id) is not None

    def all(self) -> list[EnrollmentRecord]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT user_id, name, model_version, embedding,
                           embedding_dimension, updated_at
                    FROM enrollments
                    ORDER BY user_id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"failed to list enrollments: {exc}") from exc
        return [self._record_from_row(row) for row in rows]

    def delete(self, user_id: str) -> bool:
        validate_user_id(user_id)
        try:
            with self._connection() as connection:
                with connection:
                    cursor = connection.execute(
                        "DELETE FROM enrollments WHERE user_id = ?",
                        (user_id,),
                    )
                    return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise StorageError(f"failed to delete enrollment {user_id!r}: {exc}") from exc

    def rename_user_id(
        self,
        old_user_id: str,
        new_user_id: str,
        *,
        updated_at: str,
    ) -> bool:
        validate_user_id(old_user_id)
        validate_user_id(new_user_id)
        self._validate_text("updated_at", updated_at)
        if old_user_id == new_user_id:
            raise ValueError("new user ID must differ from the current user ID")

        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    cursor = connection.execute(
                        """
                        UPDATE enrollments
                        SET user_id = ?, updated_at = ?
                        WHERE user_id = ?
                        """,
                        (new_user_id, updated_at, old_user_id),
                    )
                except Exception:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
                    return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise StorageError(
                f"failed to rename enrollment {old_user_id!r} to {new_user_id!r}: {exc}"
            ) from exc

    def update_user(
        self,
        old_user_id: str,
        new_user_id: str,
        name: str,
        *,
        updated_at: str,
    ) -> bool:
        """Atomically update a user's identity without replacing enrollment data."""
        validate_user_id(old_user_id)
        validate_user_id(new_user_id)
        self._validate_text("name", name)
        self._validate_text("updated_at", updated_at)

        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    cursor = connection.execute(
                        """
                        UPDATE enrollments
                        SET user_id = ?, name = ?, updated_at = ?
                        WHERE user_id = ?
                        """,
                        (new_user_id, name.strip(), updated_at, old_user_id),
                    )
                except Exception:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
                    return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise StorageError(
                f"failed to update enrollment {old_user_id!r}: {exc}"
            ) from exc

    def get_auth_lockout(self, user_id: str) -> AuthenticationLockout | None:
        validate_user_id(user_id)
        now = self._current_time()
        try:
            with self._connection() as connection:
                with connection:
                    row = connection.execute(
                        """
                        SELECT user_id, failure_count, locked_until
                        FROM authentication_lockouts
                        WHERE user_id = ?
                        """,
                        (user_id,),
                    ).fetchone()
                    if row is None:
                        return None
                    lockout = self._lockout_from_row(row)
                    if lockout.locked_until is not None and lockout.locked_until <= now:
                        connection.execute(
                            "DELETE FROM authentication_lockouts WHERE user_id = ?",
                            (user_id,),
                        )
                        return None
                    return lockout
        except sqlite3.Error as exc:
            raise StorageError(
                f"failed to load authentication lockout {user_id!r}: {exc}"
            ) from exc

    def record_auth_failure(self, user_id: str) -> AuthenticationLockout:
        validate_user_id(user_id)
        now = self._current_time()
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        """
                        SELECT user_id, failure_count, locked_until
                        FROM authentication_lockouts
                        WHERE user_id = ?
                        """,
                        (user_id,),
                    ).fetchone()
                    current = None if row is None else self._lockout_from_row(row)
                    if (
                        current is not None
                        and current.locked_until is not None
                        and current.locked_until > now
                    ):
                        connection.commit()
                        return current

                    failure_count = 1 if current is None else current.failure_count + 1
                    if current is not None and current.locked_until is not None:
                        failure_count = 1
                    locked_until = None
                    if failure_count >= self.MAX_AUTH_FAILURES:
                        failure_count = self.MAX_AUTH_FAILURES
                        locked_until = now + self.AUTH_LOCKOUT_DURATION
                    connection.execute(
                        """
                        INSERT INTO authentication_lockouts (
                            user_id, failure_count, locked_until
                        ) VALUES (?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET
                            failure_count = excluded.failure_count,
                            locked_until = excluded.locked_until
                        """,
                        (
                            user_id,
                            failure_count,
                            self._serialize_time(locked_until),
                        ),
                    )
                except Exception:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
        except sqlite3.Error as exc:
            raise StorageError(
                f"failed to record authentication failure {user_id!r}: {exc}"
            ) from exc

        from ..enrollment_data import AuthenticationLockout

        return AuthenticationLockout(
            user_id=user_id,
            failure_count=failure_count,
            locked_until=locked_until,
        )

    def clear_auth_failures(self, user_id: str) -> bool:
        validate_user_id(user_id)
        try:
            with self._connection() as connection:
                with connection:
                    cursor = connection.execute(
                        "DELETE FROM authentication_lockouts WHERE user_id = ?",
                        (user_id,),
                    )
                    return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise StorageError(
                f"failed to clear authentication failures {user_id!r}: {exc}"
            ) from exc

    def _current_time(self) -> datetime:
        current = self._clock()
        if not isinstance(current, datetime) or current.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return current.astimezone(timezone.utc)

    @staticmethod
    def _serialize_time(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _lockout_from_row(row: sqlite3.Row) -> AuthenticationLockout:
        from ..enrollment_data import AuthenticationLockout

        locked_until_raw = row["locked_until"]
        try:
            locked_until = None
            if locked_until_raw is not None:
                parsed = datetime.fromisoformat(locked_until_raw)
                if parsed.tzinfo is None:
                    raise ValueError("lockout timestamp must include a timezone")
                locked_until = parsed.astimezone(timezone.utc)
        except (TypeError, ValueError) as exc:
            raise StorageError("stored authentication lockout timestamp is invalid") from exc
        return AuthenticationLockout(
            user_id=row["user_id"],
            failure_count=row["failure_count"],
            locked_until=locked_until,
        )

    def _validate_record(self, record: EnrollmentRecord) -> EnrollmentRecord:
        validate_user_id(record.user_id)
        self._validate_text("name", record.name)
        self._validate_text("model_version", record.model_version)
        self._validate_text("updated_at", record.updated_at)
        if len(record.embedding) != self._embedding_dimension:
            raise ValueError(
                "embedding dimension mismatch: "
                f"expected {self._embedding_dimension}, got {len(record.embedding)}"
            )

        embedding: list[float] = []
        for value in record.embedding:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("embedding values must be numeric") from exc
            if not math.isfinite(number):
                raise ValueError("embedding values must be finite")
            embedding.append(number)

        from ..enrollment_data import EnrollmentRecord

        return EnrollmentRecord(
            user_id=record.user_id,
            name=record.name,
            model_version=record.model_version,
            embedding=embedding,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _validate_text(field: str, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")

    def _encode_embedding(self, embedding: Sequence[float]) -> bytes:
        return struct.pack(f"<{self._embedding_dimension}f", *embedding)

    def _record_from_row(self, row: sqlite3.Row) -> EnrollmentRecord:
        try:
            validate_user_id(row["user_id"])
        except ValueError as exc:
            raise StorageError("stored enrollment contains an invalid user ID") from exc
        dimension = row["embedding_dimension"]
        if dimension != self._embedding_dimension:
            raise StorageError(
                "stored embedding dimension mismatch: "
                f"expected {self._embedding_dimension}, got {dimension}"
            )
        blob = bytes(row["embedding"])
        expected_size = self._embedding_dimension * 4
        if len(blob) != expected_size:
            raise StorageError(
                f"invalid embedding byte length: expected {expected_size}, got {len(blob)}"
            )
        embedding = list(struct.unpack(f"<{self._embedding_dimension}f", blob))
        if not all(math.isfinite(value) for value in embedding):
            raise StorageError("stored embedding contains a non-finite value")
        from ..enrollment_data import EnrollmentRecord

        return EnrollmentRecord(
            user_id=row["user_id"],
            name=row["name"],
            model_version=row["model_version"],
            embedding=embedding,
            updated_at=row["updated_at"],
        )

    def _secure_database_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            path = Path(f"{self._database_path}{suffix}")
            if path.exists():
                path.chmod(0o600)
