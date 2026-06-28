"""Domain-aware drift analytics agent."""

from __future__ import annotations

from typing import Any

from src import analytics
from src.database import DriftDatabase
from src.models import ExecutiveSummary, Metric, QueryPlan, QueryResponse
from src.prompting import generate_query_plan
from src.sql_safety import SQLSafetyError


def run_query_plan(db: DriftDatabase, plan: QueryPlan) -> QueryResponse:
    """Execute a query plan and return a structured response."""

    try:
        safe_query, rows, columns = db.execute_select(plan.sql)
    except SQLSafetyError as exc:
        return QueryResponse(
            question=plan.question,
            answer="I could not run that query safely.",
            sql=plan.sql,
            chart=plan.chart,
            error=str(exc),
        )

    return QueryResponse(
        question=plan.question,
        answer=summarize_result(plan, rows),
        sql=safe_query.sql,
        columns=columns,
        rows=rows,
        chart=plan.chart,
        warnings=safe_query.warnings,
    )


def answer_question(db: DriftDatabase, question: str) -> QueryResponse:
    """Generate and execute a drift analytics answer for natural language."""

    plan = generate_query_plan(question)
    plan.question = question
    return run_query_plan(db, plan)


def summarize_result(plan: QueryPlan, rows: list[dict[str, Any]]) -> str:
    """Create a concise business answer from returned rows."""

    if not rows:
        return "No matching open in-scope drift was found for that question."

    if plan.intent == "top_drifting_apps":
        oldest = rows[0]
        return (
            f"{len(rows)} open in-scope drifting applications are shown. "
            f"The oldest is {oldest['app_name']} at {oldest['days_open']} days open."
        )
    if plan.intent == "critical_apps_with_open_drift":
        return (
            f"{len(rows)} critical/high priority open drift items matched the RTO rule. "
            f"Highest risk: {rows[0]['app_name']} with RTO score {rows[0]['rto_score']}."
        )
    if plan.intent == "drift_by_product":
        leader = rows[0]
        return (
            f"{leader['product']} has the highest open drift concentration "
            f"with {leader['drift_count']} affected applications."
        )
    if plan.intent == "drift_by_data_center":
        leader = rows[0]
        return (
            f"{leader['data_center']} has the largest data-center concentration "
            f"with {leader['drift_count']} open drift items."
        )
    if plan.intent == "exemption_analysis":
        pending = sum(row["drift_count"] for row in rows if row["exemption_result"] == "Pending")
        return f"Exemption posture is split across {len(rows)} buckets; {pending} open drift items are pending review."
    if plan.intent == "aging_bucket_analysis":
        severe = sum(
            row["app_count"]
            for row in rows
            if str(row["aging_bucket"]).startswith("120+") or str(row["aging_bucket"]).startswith("90-")
        )
        return f"{severe} open drift items are at senior or executive escalation age."
    if plan.intent == "executive_escalation_candidates":
        return f"{len(rows)} applications are at or beyond the 120-day executive escalation threshold."
    if plan.intent == "rto_risk_distribution":
        critical = next((row["drift_count"] for row in rows if row["rto_tier"] == "Mission Critical"), 0)
        return f"{critical} open drift items are Mission Critical based on rto_score 1-2."
    return f"{len(rows)} rows returned."


def build_executive_summary(db: DriftDatabase) -> ExecutiveSummary:
    """Create a multi-chart executive summary from reusable analytics functions."""

    open_drift = db.scalar(
        f"SELECT COUNT(*) AS count FROM applications AS a WHERE {analytics.OPEN_SCOPE_DRIFT}"
    ) or 0
    mission_critical = db.scalar(
        f"""
SELECT COUNT(*) AS count
FROM applications AS a
WHERE {analytics.OPEN_SCOPE_DRIFT}
  AND a.rto_score BETWEEN 1 AND 2
"""
    ) or 0
    executive_candidates = db.scalar(
        f"""
SELECT COUNT(*) AS count
FROM applications AS a
WHERE {analytics.OPEN_SCOPE_DRIFT}
  AND {analytics.AGE_DAYS} >= 120
"""
    ) or 0
    pending_exemptions = db.scalar(
        f"""
SELECT COUNT(*) AS count
FROM applications AS a
WHERE {analytics.OPEN_SCOPE_DRIFT}
  AND a.exemption_requested = 'Y'
  AND a.exemption_result = 'Pending'
"""
    ) or 0

    chart_responses = [run_query_plan(db, plan) for plan in analytics.all_dashboard_plans()]
    product_rows = chart_responses[0].rows if chart_responses else []
    data_center_rows = chart_responses[2].rows if len(chart_responses) > 2 else []
    top_product = product_rows[0]["product"] if product_rows else "N/A"
    top_data_center = data_center_rows[0]["data_center"] if data_center_rows else "N/A"

    focus_areas = [
        f"Focus remediation on {top_product}, the product family with the highest open drift count.",
        f"Investigate {top_data_center}, the data center with the strongest drift concentration.",
        "Resolve pending exemptions before 90-day and 120-day aging thresholds become governance escalations.",
    ]

    narrative = (
        f"There are {open_drift} open in-scope drift findings. "
        f"{mission_critical} are Mission Critical, and {executive_candidates} have aged into executive escalation."
    )

    return ExecutiveSummary(
        title="Executive Drift Summary",
        narrative=narrative,
        metrics=[
            Metric(label="Open Drift", value=open_drift),
            Metric(label="Mission Critical", value=mission_critical, help_text="rto_score 1-2"),
            Metric(label="Executive Escalations", value=executive_candidates, help_text="120+ days open"),
            Metric(label="Pending Exemptions", value=pending_exemptions),
        ],
        focus_areas=focus_areas,
        charts=chart_responses,
    )
