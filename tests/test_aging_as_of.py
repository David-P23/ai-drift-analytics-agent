"""Aging KPIs are point-in-time: measured to each finding's scan date, not today."""

from __future__ import annotations

import shutil
from pathlib import Path

from src import analytics
from src.agent import build_executive_summary
from src.database import DriftDatabase

ROOT = Path(__file__).resolve().parents[1]

# Open in-scope findings per bucket in the bundled demo, scanned 2026-04-03.
EXPECTED = {"<60 Current": 17, "60-89 Warning": 10, "90-119 Senior escalation": 12, "120+ Executive escalation": 86}


def _demo_db(tmp_path: Path) -> DriftDatabase:
    copy = tmp_path / "northstar_demo.sqlite"
    shutil.copy(ROOT / "data" / "northstar_demo.sqlite", copy)
    return DriftDatabase(copy, max_rows=1000)


def test_demo_aging_buckets_do_not_depend_on_todays_date(tmp_path: Path) -> None:
    _, rows, _ = _demo_db(tmp_path).execute_select(analytics.aging_bucket_analysis().sql)
    assert {row["aging_bucket"]: row["finding_count"] for row in rows} == EXPECTED


def test_summary_states_the_as_of_date(tmp_path: Path) -> None:
    summary = build_executive_summary(_demo_db(tmp_path))
    assert summary.narrative.startswith("As of the 2026-04-03 scan, there are 125 open in-scope drift findings.")
    assert {m.label: m.value for m in summary.metrics}["Executive Escalations"] == EXPECTED["120+ Executive escalation"]


def test_list_answers_report_true_totals_not_the_list_limit(tmp_path: Path) -> None:
    """The answer text must agree with the KPI cards even when the table is capped by LIMIT."""

    from src.agent import answer_question

    db = _demo_db(tmp_path)
    escalation = answer_question(db, "Which apps need executive escalation?")
    assert escalation.answer.startswith("86 open drift findings are at or beyond the 120-day")
    assert len(escalation.rows) == 25 and "Showing the 25 most critical." in escalation.answer

    critical_high = answer_question(db, "Show critical and high priority open drift.")
    assert critical_high.answer.startswith(
        "46 critical/high open drift findings match the request: 20 Mission Critical and 26 High."
    )
