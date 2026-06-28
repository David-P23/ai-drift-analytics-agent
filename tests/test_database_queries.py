from __future__ import annotations

from pathlib import Path

from src import analytics
from src.agent import build_executive_summary, run_query_plan
from src.database import DriftDatabase


def test_reusable_analytics_queries_execute(demo_db_path: Path) -> None:
    db = DriftDatabase(demo_db_path, max_rows=100)
    plans = [
        analytics.top_drifting_apps(),
        analytics.critical_apps_with_open_drift(),
        analytics.drift_by_product(),
        analytics.drift_by_data_center(),
        analytics.exemption_analysis(),
        analytics.aging_bucket_analysis(),
        analytics.executive_escalation_candidates(),
        analytics.rto_risk_distribution(),
        analytics.ai_risk_intelligence(),
        analytics.drift_cluster_source(),
    ]

    for plan in plans:
        response = run_query_plan(db, plan)
        assert response.error is None, response.error
        assert response.sql is not None
        assert response.rows


def test_top_drifting_apps_returns_oldest_first(demo_db_path: Path) -> None:
    db = DriftDatabase(demo_db_path, max_rows=100)
    response = run_query_plan(db, analytics.top_drifting_apps())
    days_open = [row["days_open"] for row in response.rows]
    assert days_open == sorted(days_open, reverse=True)


def test_executive_summary_is_structured(demo_db_path: Path) -> None:
    db = DriftDatabase(demo_db_path, max_rows=100)
    summary = build_executive_summary(db)
    assert summary.title == "Executive Drift Summary"
    assert len(summary.metrics) == 4
    assert summary.charts
    assert "open in-scope drift" in summary.narrative


def test_ai_risk_intelligence_returns_scored_actions(demo_db_path: Path) -> None:
    db = DriftDatabase(demo_db_path, max_rows=100)
    response = run_query_plan(db, analytics.ai_risk_intelligence())

    assert response.error is None
    assert response.rows
    scores = [row["ai_priority_score"] for row in response.rows]
    assert scores == sorted(scores, reverse=True)
    assert {"ai_priority_score", "ai_recommended_action", "aging_bucket"}.issubset(response.columns)
    assert response.rows[0]["ai_recommended_action"] in {
        "Executive intervention",
        "Remediation command",
        "Governance watch",
        "Managed backlog",
    }
