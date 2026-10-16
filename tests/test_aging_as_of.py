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
    assert {row["aging_bucket"]: row["app_count"] for row in rows} == EXPECTED


def test_summary_states_the_as_of_date(tmp_path: Path) -> None:
    summary = build_executive_summary(_demo_db(tmp_path))
    assert summary.narrative.startswith("As of the 2026-04-03 scan, there are 125 open in-scope drift findings.")
    assert {m.label: m.value for m in summary.metrics}["Executive Escalations"] == EXPECTED["120+ Executive escalation"]
