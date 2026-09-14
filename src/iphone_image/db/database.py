"""SQLite connection handling and forward-only migrations.

The schema version lives in `PRAGMA user_version`. Migrations are numbered SQL
files applied in order, each inside its own transaction. There is no downgrade
path: a database newer than the build refuses to open rather than being altered
by code that does not understand it.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")


class DatabaseError(Exception):
    """Raised when the database cannot be opened or migrated."""


def utcnow() -> str:
    """The single timestamp format used everywhere in this database."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, str, Path]]:
    """Return (version, name, path) for every migration, in order.

    Gaps are fatal. A missing 0002 means someone's checkout is incomplete, and
    applying 0003 on top of 0001 would produce a schema nobody has tested.
    """
    found: list[tuple[int, str, Path]] = []
    for path in sorted(directory.glob("*.sql")):
        match = _MIGRATION_RE.match(path.name)
        if not match:
            raise DatabaseError(
                f"migration filename not understood: {path.name}. "
                f"Expected NNNN_lower_snake_case.sql"
            )
        found.append((int(match.group(1)), match.group(2), path))

    for index, (version, _, path) in enumerate(found, start=1):
        if version != index:
            raise DatabaseError(
                f"migration numbering has a gap: expected {index:04d}, found {path.name}"
            )
    return found


class Database:
    """A connection to the ledger."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        self._conn: sqlite3.Connection | None = None

    # -- lifecycle ---------------------------------------------------------

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise DatabaseError("database is not open, call connect() first")
        return self._conn

    def connect(self, *, create: bool = True) -> Self:
        if self._conn is not None:
            return self
        if not self.path.exists():
            if not create:
                raise DatabaseError(f"no database at {self.path}")
            self.path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.path, isolation_level=None, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA synchronous = FULL")  # this database authorises deletions
        self._conn = conn
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Self:
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- migrations --------------------------------------------------------

    @property
    def schema_version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self, *, directory: Path = MIGRATIONS_DIR) -> list[str]:
        """Apply pending migrations. Returns the names applied, possibly empty."""
        migrations = discover_migrations(directory)
        if not migrations:
            raise DatabaseError(f"no migrations found in {directory}")

        latest = migrations[-1][0]
        current = self.schema_version

        if current > latest:
            raise DatabaseError(
                f"database at {self.path} is schema version {current}, but this build "
                f"only knows up to {latest}. Upgrade the tool rather than downgrading "
                f"the database."
            )

        applied: list[str] = []
        for version, name, path in migrations:
            if version <= current:
                continue
            # executescript() issues an implicit COMMIT before it runs, so the
            # transaction has to live inside the script text rather than around it.
            # Without this a half-applied migration would be left committed.
            script = f"BEGIN;\n{path.read_text()}\nPRAGMA user_version = {version};\nCOMMIT;"
            try:
                self.conn.executescript(script)
            except sqlite3.Error as exc:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise DatabaseError(f"migration {path.name} failed: {exc}") from exc
            applied.append(f"{version:04d}_{name}")

        return applied

    # -- helpers -----------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """A real transaction. Rolls back on any exception."""
        self.conn.execute("BEGIN")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str | None) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated_at = excluded.updated_at",
            (key, value, utcnow()),
        )

    def counts(self) -> dict[str, int]:
        """Row counts per table, for status output and for proving a gate ran."""
        tables = [
            r["name"]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        return {
            t: self.conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in tables
        }

    def integrity_check(self) -> list[str]:
        """Return problems found by SQLite itself. Empty means clean."""
        problems = [r[0] for r in self.conn.execute("PRAGMA integrity_check") if r[0] != "ok"]
        problems += [
            f"foreign key violation in {r['table']} row {r['rowid']}"
            for r in self.conn.execute("PRAGMA foreign_key_check")
        ]
        return problems


def open_database(path: Path, *, migrate: bool = True) -> Database:
    db = Database(path).connect()
    if migrate:
        db.migrate()
    return db
