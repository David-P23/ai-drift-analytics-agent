"""Domain-aware drift analytics agent."""

from __future__ import annotations

from typing import Any

from src import analytics
from src.database import DriftDatabase
from src.models import ConversationContext, ExecutiveSummary, Metric, QueryPlan, QueryResponse
from src.policy_rag import compose_policy_guidance, is_policy_only_question, retrieve_policy_context, should_retrieve_policy
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
        intent=plan.intent,
        resolved_question=plan.resolved_question,
        planner=plan.planner,
    )


def answer_question(
    db: DriftDatabase,
    question: str,
    context: ConversationContext | None = None,
) -> QueryResponse:
    """Generate and execute a drift analytics answer for natural language."""

    policy_retrieval = retrieve_policy_context(question) if should_retrieve_policy(question) else None
    if policy_retrieval and is_policy_only_question(question):
        return QueryResponse(
            question=question,
            answer=compose_policy_guidance(question, policy_retrieval),
            policy_sources=list(policy_retrieval.sources),
            retrieval_mode=policy_retrieval.mode,
        )

    plan = generate_query_plan(question, context=context)
    plan.question = question
    response = run_query_plan(db, plan)
    if not response.error:
        response.conversation_context = ConversationContext(
            intent=plan.intent,
            resolved_question=plan.resolved_question or question,
            filters=plan.filters,
        )
        if policy_retrieval:
            response.answer = compose_policy_guidance(
                question,
                policy_retrieval,
                data_answer=response.answer,
            )
            response.policy_sources = list(policy_retrieval.sources)
            response.retrieval_mode = policy_retrieval.mode
    return response


def summarize_result(plan: QueryPlan, rows: list[dict[str, Any]]) -> str:
    """Create a concise business answer from returned rows."""

    if not rows:
        return "No matching open in-scope drift was found for that question."

    if plan.intent == "top_drifting_apps":
        oldest = rows[0]
        return (
            f"{len(rows)} open in-scope drifting applications match the request. "
            f"The oldest is {oldest['app_name']} at {oldest['days_open']} days open."
        )
    if plan.intent == "critical_apps_with_open_drift":
        mission_critical = sum(1 for row in rows if row.get("rto_score", 99) <= 2)
        high = sum(1 for row in rows if 3 <= row.get("rto_score", 99) <= 4)
        oldest = max(rows, key=lambda row: row.get("days_open", 0))
        if plan.filters.include_high:
            return (
                f"{len(rows)} critical/high open drift findings match the request: "
                f"{mission_critical} Mission Critical and {high} High. "
                f"The oldest is {oldest['app_name']} at {oldest['days_open']} days open."
            )
        return (
            f"{mission_critical} Mission Critical applications with open drift match the request. "
            f"The oldest is {oldest['app_name']} at {oldest['days_open']} days open."
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
        high = next((row["drift_count"] for row in rows if row["rto_tier"] == "High"), 0)
        total = sum(row["drift_count"] for row in rows)
        oldest = max((row["oldest_days_open"] for row in rows), default=0)
        return (
            f"{total} open drift findings are in this RTO distribution. "
            f"{_count_phrase(critical, 'is', 'are')} Mission Critical and "
            f"{_count_phrase(high, 'is', 'are')} High, with the oldest item at {oldest} days open."
        )
    return f"{len(rows)} rows returned."


def _count_phrase(count: int, singular_verb: str, plural_verb: str) -> str:
    verb = singular_verb if count == 1 else plural_verb
    return f"{count} {verb}"


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
