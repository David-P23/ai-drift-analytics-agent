"""Answers count findings and the applications they belong to.

One application can carry several drift findings, so "how many apps" and "how many
findings" have different answers: 125 open findings belong to 101 applications. Every list
answer states both, and each application count here is checked against an independent
query written directly against the demo database.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from src.agent import answer_question, build_executive_summary
from src.database import DriftDatabase

ROOT = Path(__file__).resolve().parents[1]
OPEN_IN_SCOPE = "detected_version <> approved_version AND status = 'Open' AND in_scope = 'Y'"
DAYS_OPEN = "CAST(julianday(date(last_scanned_at)) - julianday(drift_detected_on) AS INTEGER)"
COUNTS = re.compile(r"^(\d+) .*?findings across (\d+) applications? ")


@pytest.fixture
def demo_copy(tmp_path: Path) -> Path:
    copy = tmp_path / "northstar_demo.sqlite"
    shutil.copy(ROOT / "data" / "northstar_demo.sqlite", copy)
    return copy


def _independent_counts(path: Path, condition: str) -> tuple[int, int]:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(
            f"SELECT COUNT(*), COUNT(DISTINCT app_id) FROM applications WHERE {OPEN_IN_SCOPE} AND {condition}"
        ).fetchone()


@pytest.mark.parametrize(
    ("question", "condition"),
    [
        ("Which applications have the oldest open drift?", "1 = 1"),
        ("Show mission critical apps with open drift.", "rto_score BETWEEN 1 AND 2"),
        ("Show critical and high priority open drift.", "rto_score BETWEEN 1 AND 4"),
        ("Which apps need executive escalation?", f"{DAYS_OPEN} >= 120"),
    ],
)
def test_list_answers_state_findings_and_applications(demo_copy: Path, question: str, condition: str) -> None:
    answer = answer_question(DriftDatabase(demo_copy, max_rows=1000), question).answer
    match = COUNTS.match(answer)
    assert match, f"no 'N findings across M applications' in: {answer}"
    findings, apps = _independent_counts(demo_copy, condition)
    assert (int(match.group(1)), int(match.group(2))) == (findings, apps)
    assert apps < findings, "the demo data has applications with several findings, so the counts must differ"


def test_follow_up_keeps_both_counts(demo_copy: Path) -> None:
    db = DriftDatabase(demo_copy, max_rows=1000)
    first = answer_question(db, "Show mission critical apps with open drift.")
    narrowed = answer_question(db, "Let's narrow that analysis to the Minneapolis location.", first.conversation_context)
    findings, apps = _independent_counts(demo_copy, "rto_score BETWEEN 1 AND 2 AND LOWER(data_center) = 'minneapolis'")
    assert narrowed.answer.startswith(f"{findings} Mission Critical open drift findings across {apps} applications ")


def test_executive_summary_states_both_counts(demo_copy: Path) -> None:
    findings, apps = _independent_counts(demo_copy, "1 = 1")
    narrative = build_executive_summary(DriftDatabase(demo_copy, max_rows=1000)).narrative
    assert f"{findings} open in-scope drift findings across {apps} applications." in narrative
