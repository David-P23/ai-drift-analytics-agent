"""Every query the app can run must read well as SQL evidence, on both backends.

This sweeps the query templates, the totals queries, the executive summary, the
glossary figures, and every suggested question. A new feature that produces a
one-line or very wide query fails here, in CI, before it can be merged.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from src import analytics
from src.agent import answer_question, build_executive_summary
from src.database import DriftDatabase
from src.glossary import TERMS
from src.prompting import SUGGESTED_QUESTIONS
from src.sql_dialect import to_databricks

ROOT = Path(__file__).resolve().parents[1]
MAX_LINE = 140  # characters; wider lines scroll sideways in the SQL Evidence panel


class RecordingDatabase(DriftDatabase):
    """The demo database, remembering every statement it ran."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.ran: list[str] = []

    def execute_select(self, sql: str):
        safe, rows, columns = super().execute_select(sql)
        self.ran.append(safe.sql)
        return safe, rows, columns


@pytest.fixture()
def db(tmp_path: Path, monkeypatch) -> RecordingDatabase:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    copy = tmp_path / "northstar_demo.sqlite"
    shutil.copy(ROOT / "data" / "northstar_demo.sqlite", copy)
    return RecordingDatabase(copy, max_rows=1000)


def _every_query(db: RecordingDatabase) -> list[str]:
    build_executive_summary(db)
    for question in SUGGESTED_QUESTIONS:
        answer_question(db, question)
    for term in TERMS:
        answer_question(db, f"What does {term.aliases[0]} mean?")
    for plan in [
        *analytics.all_dashboard_plans(),
        analytics.drift_cluster_source(),
        analytics.critical_apps_with_open_drift(include_high=True, data_center="Phoenix", product="Kafka"),
        analytics.top_drifting_apps(data_center="St. Louis"),
    ]:
        db.execute_select(plan.sql)
        if plan.count_sql:
            db.execute_select(plan.count_sql)
    return db.ran


def _problems(sql: str) -> list[str]:
    lines = sql.splitlines()
    problems = []
    if len(lines) < 2:
        problems.append("written on one line")
    wide = [line for line in lines if len(line) > MAX_LINE]
    if wide:
        problems.append(f"{len(wide)} line(s) wider than {MAX_LINE} characters, e.g. {wide[0][:80]}...")
    if any(not line.strip() for line in lines):
        problems.append("contains blank lines")
    return problems


def test_every_query_reads_well_on_sqlite(db: RecordingDatabase) -> None:
    ran = _every_query(db)
    assert len(ran) > 40
    failures = {sql.splitlines()[0][:70]: _problems(sql) for sql in ran if _problems(sql)}
    assert not failures, failures


def test_every_query_reads_well_on_databricks(db: RecordingDatabase) -> None:
    failures = {}
    for sql in _every_query(db):
        translated = to_databricks(sql)
        if _problems(translated):
            failures[translated.splitlines()[0][:70]] = _problems(translated)
    assert not failures, failures


def test_one_line_sql_is_laid_out_and_still_runs(db: RecordingDatabase) -> None:
    safe, rows, _ = db.execute_select(
        "SELECT COUNT(*) AS figure FROM applications AS a WHERE a.status = 'Open' AND a.in_scope = 'Y'"
    )
    assert safe.sql.splitlines() == [
        "SELECT COUNT(*) AS figure",
        "FROM applications AS a",
        "WHERE a.status = 'Open'",
        "    AND a.in_scope = 'Y'",
        "LIMIT 1000",
    ]
    assert rows == [{"figure": 125}]
