"""Databricks backend: dialect translation and the warehouse adapter (no network)."""

from __future__ import annotations

import datetime as dt
import decimal

import pytest

from src import analytics
from src.agent import answer_question, build_executive_summary
from src.sql_dialect import to_databricks
from src.sql_safety import SQLSafetyError
from src.warehouse import DatabricksDriftDatabase, DatabricksSettings


def test_days_open_translates_to_datediff() -> None:
    sql = analytics.top_drifting_apps().sql
    translated = to_databricks(sql)
    assert "julianday" not in translated.lower()
    assert "DATEDIFF(CURRENT_DATE(), a.drift_detected_on) AS days_open" in translated


def test_every_template_translates_cleanly() -> None:
    for plan in analytics.all_dashboard_plans() + [analytics.drift_cluster_source()]:
        assert "julianday" not in to_databricks(plan.sql).lower(), plan.intent


def test_untranslatable_sqlite_function_is_refused() -> None:
    with pytest.raises(SQLSafetyError, match="SQLite-specific"):
        to_databricks("SELECT strftime('%Y', a.drift_detected_on) FROM applications AS a")


def test_function_names_inside_string_literals_are_ignored() -> None:
    sql = "SELECT a.app_name FROM applications AS a WHERE a.latest_note_text = 'date(soon)'"
    assert to_databricks(sql) == sql


def test_settings_from_env_requires_connection_details() -> None:
    with pytest.raises(ValueError, match="DATABRICKS_TOKEN"):
        DatabricksSettings.from_env({"DATABRICKS_SERVER_HOSTNAME": "x", "DATABRICKS_HTTP_PATH": "y"})
    settings = DatabricksSettings.from_env(
        {
            "DATABRICKS_SERVER_HOSTNAME": "https://dbc-123.cloud.databricks.com/",
            "DATABRICKS_HTTP_PATH": "/sql/1.0/warehouses/abc",
            "DATABRICKS_TOKEN": "token",
        }
    )
    assert settings.server_hostname == "dbc-123.cloud.databricks.com"
    assert settings.location == "drift_demo.gold"


class FakeCursor:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn
        self.description = None

    def execute(self, sql: str) -> None:
        self.conn.executed.append(sql)
        if self.conn.fail_next:
            self.conn.fail_next = False
            raise RuntimeError("warehouse is stopped")
        columns, rows = self.conn.result
        self.description = [(c, "string", None, None, None, None, None) for c in columns]
        self._rows = rows

    def fetchall(self) -> list[tuple]:
        return self._rows

    def close(self) -> None:
        pass


class FakeConnection:
    def __init__(self, result=(["count"], [(3,)])) -> None:
        self.result = result
        self.executed: list[str] = []
        self.fail_next = False
        self.closed = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True


def _db(conn: FakeConnection, connects: list | None = None) -> DatabricksDriftDatabase:
    settings = DatabricksSettings("host", "/sql/1.0/warehouses/abc", "token")

    def connect(_settings: DatabricksSettings) -> FakeConnection:
        if connects is not None:
            connects.append(_settings)
        return conn

    return DatabricksDriftDatabase(settings, max_rows=50, connect=connect)


def test_unsafe_sql_never_reaches_the_warehouse() -> None:
    conn = FakeConnection()
    db = _db(conn)
    for sql in ("DELETE FROM applications", "SELECT * FROM secrets", "SELECT 1; DROP TABLE applications"):
        with pytest.raises(SQLSafetyError):
            db.execute_select(sql)
    assert conn.executed == []


def test_query_is_validated_limited_translated_and_reported() -> None:
    conn = FakeConnection(
        (
            ["app_name", "days_open", "drift_detected_on", "last_scanned_at", "avg"],
            [("Granite Gateway", 130, dt.date(2025, 6, 1), dt.datetime(2026, 6, 27, 0, 50, 27), decimal.Decimal("12.5"))],
        )
    )
    db = _db(conn)
    safe, rows, columns = db.execute_select(
        "SELECT a.app_name, CAST(julianday('now') - julianday(a.drift_detected_on) AS INTEGER) AS days_open "
        "FROM applications AS a"
    )
    sent = conn.executed[0]
    assert "DATEDIFF(CURRENT_DATE(), a.drift_detected_on)" in sent
    assert sent.endswith("LIMIT 50")
    assert safe.sql == sent  # the evidence shown in the app is what actually ran
    assert columns == ["app_name", "days_open", "drift_detected_on", "last_scanned_at", "avg"]
    assert rows == [
        {
            "app_name": "Granite Gateway",
            "days_open": 130,
            "drift_detected_on": "2025-06-01",
            "last_scanned_at": "2026-06-27T00:50:27+00:00",
            "avg": 12.5,
        }
    ]


def test_connection_is_reused_and_reopened_after_a_failure() -> None:
    conn = FakeConnection()
    connects: list = []
    db = _db(conn, connects)
    assert db.scalar("SELECT COUNT(*) AS count FROM applications AS a") == 3
    assert db.scalar("SELECT COUNT(*) AS count FROM applications AS a") == 3
    assert len(connects) == 1

    conn.fail_next = True
    with pytest.raises(SQLSafetyError, match="Databricks rejected the query"):
        db.scalar("SELECT COUNT(*) AS count FROM applications AS a")
    assert conn.closed
    assert db.scalar("SELECT COUNT(*) AS count FROM applications AS a") == 3
    assert len(connects) == 2


class RecordingConnection(FakeConnection):
    """Answers every query with an empty result so the agent code paths run end to end."""

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(self)
        original = cursor.execute

        def execute(sql: str) -> None:
            self.result = (["count"], [(0,)]) if "COUNT(*) AS count" in sql else (["x"], [])
            original(sql)

        cursor.execute = execute  # type: ignore[method-assign]
        return cursor


def test_agent_and_dashboard_run_unchanged_on_the_databricks_backend() -> None:
    conn = RecordingConnection()
    db = _db(conn)
    summary = build_executive_summary(db)
    response = answer_question(db, "Which mission critical apps have open drift?")
    assert summary.metrics[0].value == 0
    assert response.error is None
    assert all("julianday" not in sql.lower() for sql in conn.executed)
