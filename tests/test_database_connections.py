"""Every SQLite connection the app opens is closed again.

`with sqlite3.connect(...) as conn:` only ends the transaction; it does not close
the connection, so the database file stays open until garbage collection. Linux
does not mind, but Windows refuses to delete an open file. That is how the leak
showed up: on Windows, scripts/check_databricks.py could not clean up its
temporary copy of the demo database after all checks had passed.
"""

from __future__ import annotations

import sqlite3

import pytest

from src import database


@pytest.fixture
def opened_connections(monkeypatch: pytest.MonkeyPatch) -> list[sqlite3.Connection]:
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(database.sqlite3, "connect", tracking_connect)
    return opened


def _assert_all_closed(connections: list[sqlite3.Connection]) -> None:
    assert connections, "expected the database module to open at least one connection"
    for conn in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")


def test_queries_close_their_connection(tmp_path, opened_connections):
    path = database.initialize_demo_database(tmp_path / "demo.sqlite")
    db = database.DriftDatabase(path, max_rows=10)
    db.execute_select("SELECT COUNT(*) AS findings FROM applications AS a")
    db.scalar("SELECT COUNT(*) AS findings FROM applications AS a")
    _assert_all_closed(opened_connections)


def test_writes_close_their_connection_and_commit(tmp_path, opened_connections):
    path = database.initialize_demo_database(tmp_path / "demo.sqlite")
    assert database.clear_applications_rows(path) > 0
    database.replace_applications_rows(path, [])
    _assert_all_closed(opened_connections)

    count = database.DriftDatabase(path).scalar("SELECT COUNT(*) AS findings FROM applications AS a")
    assert count == 0


def test_database_file_can_be_removed_after_queries(tmp_path):
    """The Windows symptom, checked directly: nothing holds the file once a query returns."""

    path = database.initialize_demo_database(tmp_path / "demo.sqlite")
    database.DriftDatabase(path).scalar("SELECT COUNT(*) AS findings FROM applications AS a")
    path.unlink()
    assert not path.exists()
