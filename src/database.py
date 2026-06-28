"""SQLite repository and demo database initialization."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.domain import APPLICATIONS_TABLE, schema_map
from src.models import SafeQuery
from src.sample_data import build_demo_rows
from src.sql_safety import SQLSafetyError, SQLSafetyValidator


CREATE_APPLICATIONS_SQL = """
CREATE TABLE IF NOT EXISTS applications (
    finding_id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL,
    drift_id TEXT,
    app_name TEXT NOT NULL,
    business_owner TEXT NOT NULL,
    technology_owner TEXT NOT NULL,
    product TEXT NOT NULL,
    data_center TEXT NOT NULL,
    approved_version TEXT NOT NULL,
    detected_version TEXT NOT NULL,
    rto_score INTEGER NOT NULL CHECK (rto_score BETWEEN 1 AND 10),
    status TEXT NOT NULL CHECK (status IN ('Open', 'Closed')),
    exemption_requested TEXT NOT NULL CHECK (exemption_requested IN ('Y', 'N')),
    exemption_result TEXT CHECK (
        exemption_result IN ('Approved', 'Pending', 'Denied') OR exemption_result IS NULL
    ),
    in_scope TEXT NOT NULL CHECK (in_scope IN ('Y', 'N')),
    drift_detected_on TEXT NOT NULL,
    last_scanned_at TEXT NOT NULL,
    note_count INTEGER NOT NULL DEFAULT 0,
    latest_note_date TEXT,
    latest_note_type TEXT,
    latest_note_author_role TEXT,
    latest_note_text TEXT,
    escalation_flag TEXT CHECK (escalation_flag IN ('Y', 'N') OR escalation_flag IS NULL)
);
""".strip()


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with row dictionaries enabled."""

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def initialize_demo_database(db_path: str | Path, *, reset: bool = False) -> Path:
    """Create and seed the demo database when needed."""

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with connect(path) as conn:
        if reset:
            conn.execute("DROP TABLE IF EXISTS applications")
        _ensure_applications_schema(conn)
        count = conn.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        if count == 0:
            rows = build_demo_rows()
            columns = [column.name for column in APPLICATIONS_TABLE.columns]
            placeholders = ", ".join("?" for _ in columns)
            conn.executemany(
                f"INSERT INTO applications ({', '.join(columns)}) VALUES ({placeholders})",
                [[row[column] for column in columns] for row in rows],
            )
            conn.commit()

    return path


def initialize_empty_database(db_path: str | Path, *, reset: bool = False) -> Path:
    """Create the application table without loading demo rows."""

    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with connect(path) as conn:
        if reset:
            conn.execute("DROP TABLE IF EXISTS applications")
        _ensure_applications_schema(conn)
        conn.commit()

    return path


def _ensure_applications_schema(conn: sqlite3.Connection) -> None:
    """Create or refresh the local table when the lightweight portfolio schema changes."""

    conn.execute(CREATE_APPLICATIONS_SQL)
    existing_columns = [row["name"] for row in conn.execute("PRAGMA table_info(applications)").fetchall()]
    expected_columns = [column.name for column in APPLICATIONS_TABLE.columns]
    if existing_columns != expected_columns:
        conn.execute("DROP TABLE IF EXISTS applications")
        conn.execute(CREATE_APPLICATIONS_SQL)


def clear_applications_rows(db_path: str | Path) -> int:
    """Remove all application rows while preserving the schema."""

    path = initialize_empty_database(db_path)
    with connect(path) as conn:
        cursor = conn.execute("DELETE FROM applications")
        conn.commit()
        return cursor.rowcount


def replace_applications_rows(db_path: str | Path, rows: list[dict[str, Any]]) -> int:
    """Replace the applications table with validated uploaded workbook rows."""

    path = initialize_empty_database(db_path)
    columns = [column.name for column in APPLICATIONS_TABLE.columns]
    placeholders = ", ".join("?" for _ in columns)

    with connect(path) as conn:
        conn.execute("DELETE FROM applications")
        conn.executemany(
            f"INSERT INTO applications ({', '.join(columns)}) VALUES ({placeholders})",
            [[row.get(column) for column in columns] for row in rows],
        )
        conn.commit()

    return len(rows)


class DriftDatabase:
    """Read-only query access for drift analytics."""

    def __init__(self, db_path: str | Path, *, max_rows: int = 100) -> None:
        self.db_path = Path(db_path)
        self.max_rows = max_rows
        self.validator = SQLSafetyValidator(schema_map(), max_rows=max_rows)

    def validate_sql(self, sql: str) -> SafeQuery:
        return self.validator.validate(sql)

    def execute_select(self, sql: str) -> tuple[SafeQuery, list[dict[str, Any]], list[str]]:
        """Validate and execute a SELECT statement in SQLite query-only mode."""

        safe_query = self.validate_sql(sql)
        with connect(self.db_path) as conn:
            conn.execute("PRAGMA query_only = ON")
            self._assert_sql_compiles(conn, safe_query.sql)
            cursor = conn.execute(safe_query.sql)
            rows = [dict(row) for row in cursor.fetchall()]
            columns = [description[0] for description in cursor.description or []]
        return safe_query, rows, columns

    def scalar(self, sql: str) -> Any:
        """Execute a safe query expected to return one scalar value."""

        _, rows, columns = self.execute_select(sql)
        if not rows or not columns:
            return None
        return rows[0][columns[0]]

    @staticmethod
    def _assert_sql_compiles(conn: sqlite3.Connection, sql: str) -> None:
        try:
            conn.execute(f"EXPLAIN QUERY PLAN {sql}")
        except sqlite3.Error as exc:
            raise SQLSafetyError(f"SQLite rejected the query: {exc}") from exc


def rows_to_markdown(rows: Iterable[dict[str, Any]], limit: int = 5) -> str:
    """Small markdown helper for answer text."""

    preview = list(rows)[:limit]
    if not preview:
        return "No rows returned."
    lines: list[str] = []
    for row in preview:
        fragments = [f"{key}={value}" for key, value in row.items()]
        lines.append("- " + ", ".join(fragments))
    return "\n".join(lines)
