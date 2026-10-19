"""Domain-aware drift analytics agent."""

from __future__ import annotations

from typing import Any

from src import analytics
from src.database import DriftDatabase
from src.glossary import Term, match_definition, term_by_key
from src.models import ConversationContext, ExecutiveSummary, Metric, QueryPlan, QueryResponse
from src.policy_rag import compose_policy_guidance, is_policy_only_question, retrieve_policy_context, should_retrieve_policy
from src.prompting import generate_query_plan
from src.sql_safety import SQLSafetyError


def run_query_plan(db: DriftDatabase, plan: QueryPlan) -> QueryResponse:
    """Execute a query plan and return a structured response."""

    try:
        safe_query, rows, columns = db.execute_select(plan.sql)
        totals = None
        if plan.count_sql:
            _, total_rows, _ = db.execute_select(plan.count_sql)
            totals = total_rows[0] if total_rows else None
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
        answer=summarize_result(plan, rows, totals),
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

    definition = match_definition(question)
    if definition:
        return define_term(db, question, definition, planner="deterministic")

    plan = generate_query_plan(question, context=context)
    plan.question = question
    if plan.question_type == "definition" and term_by_key(plan.term):
        return define_term(db, question, term_by_key(plan.term), planner=plan.planner)
    if plan.question_type == "out_of_scope":
        return out_of_scope(question, planner=plan.planner)
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


OUT_OF_SCOPE_ANSWER = (
    "That question is outside what this agent covers. It answers questions about application drift in the "
    "NorthStar portfolio: which findings are open and how old they are, where drift is concentrated, "
    "exemptions, and escalations. It can also explain terms such as Mission Critical, RTO, drift and exemption."
)


def define_term(db: DriftDatabase, question: str, term: Term, *, planner: str) -> QueryResponse:
    """Answer "what does X mean" from the glossary, plus the term's current figure when it has one."""

    answer = term.definition
    sql = None
    rows: list[dict[str, Any]] = []
    if term.figure_sql:
        try:
            safe_query, figure_rows, figure_columns = db.execute_select(term.figure_sql)
        except SQLSafetyError:
            figure_rows = []
        if figure_rows and figure_rows[0][figure_columns[0]] is not None:
            figure = figure_rows[0][figure_columns[0]]
            if term.key == "scan_date":
                figure = str(figure)[:10]
            answer = f"{answer} {term.figure_text.format(figure=figure)}"
            sql = safe_query.sql
            rows = [{"term": term.name, "current_value": figure}]
    return QueryResponse(
        question=question,
        answer=answer,
        sql=sql,
        columns=["term", "current_value"] if rows else [],
        rows=rows,
        intent="definition",
        resolved_question=f"Define {term.name}",
        planner=planner,
        suggestions=[term.try_next] if term.try_next else [],
    )


def out_of_scope(question: str, *, planner: str) -> QueryResponse:
    from src.prompting import SUGGESTED_QUESTIONS

    return QueryResponse(
        question=question,
        answer=OUT_OF_SCOPE_ANSWER,
        intent="out_of_scope",
        planner=planner,
        suggestions=list(SUGGESTED_QUESTIONS[:3]),
    )


def summarize_result(
    plan: QueryPlan, rows: list[dict[str, Any]], totals: dict[str, Any] | None = None
) -> str:
    """Create a concise business answer from returned rows.

    List questions return at most LIMIT rows, so counts come from ``totals`` (the same
    filter without the LIMIT) when the plan provides it, never from the length of the list.
    """

    if not rows:
        return "No matching open in-scope drift was found for that question."

    total = int(totals["total_count"]) if totals and totals.get("total_count") is not None else len(rows)
    shown = f" Showing the top {len(rows)}." if total > len(rows) else ""
    # Findings are not applications: one application can carry several findings.
    across = _across_applications(totals)

    if plan.intent == "top_drifting_apps":
        oldest = rows[0]
        return (
            f"{total} open in-scope drift findings{across} match the request. "
            f"The oldest is {oldest['app_name']} at {oldest['days_open']} days open.{shown}"
        )
    if plan.intent == "critical_apps_with_open_drift":
        if totals:
            mission_critical = int(totals.get("mission_critical_count") or 0)
            high = int(totals.get("high_count") or 0)
            oldest_days = int(totals.get("oldest_days_open") or 0)
        else:
            mission_critical = sum(1 for row in rows if row.get("rto_score", 99) <= 2)
            high = sum(1 for row in rows if 3 <= row.get("rto_score", 99) <= 4)
            oldest_days = max(row.get("days_open", 0) for row in rows)
        oldest_row = next((row for row in rows if row.get("days_open") == oldest_days), None)
        oldest = (
            f"The oldest is {oldest_row['app_name']} at {oldest_days} days open."
            if oldest_row
            else f"The oldest has been open {oldest_days} days."
        )
        if plan.filters.include_high:
            return (
                f"{total} critical/high open drift findings{across} match the request: "
                f"{mission_critical} Mission Critical and {high} High. {oldest}{shown}"
            )
        return f"{mission_critical} Mission Critical open drift findings{across} match the request. {oldest}{shown}"
    if plan.intent == "drift_by_product":
        leader = rows[0]
        return (
            f"{leader['product']} has the highest open drift concentration "
            f"with {leader['drift_count']} open drift findings."
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
            row["finding_count"]
            for row in rows
            if str(row["aging_bucket"]).startswith("120+") or str(row["aging_bucket"]).startswith("90-")
        )
        return f"{severe} open drift items are at senior or executive escalation age."
    if plan.intent == "executive_escalation_candidates":
        return (
            f"{total} open drift findings{across} are at or beyond the 120-day executive escalation threshold."
            f"{' Showing the ' + str(len(rows)) + ' most critical.' if total > len(rows) else ''}"
        )
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


def _across_applications(totals: dict[str, Any] | None) -> str:
    """' across 4 applications' from the totals row, or '' when the plan has no totals query."""

    if not totals or totals.get("app_count") is None:
        return ""
    apps = int(totals["app_count"])
    return f" across {apps} application{'' if apps == 1 else 's'}"


def _count_phrase(count: int, singular_verb: str, plural_verb: str) -> str:
    verb = singular_verb if count == 1 else plural_verb
    return f"{count} {verb}"


def build_executive_summary(db: DriftDatabase) -> ExecutiveSummary:
    """Create a multi-chart executive summary from reusable analytics functions."""

    open_drift = db.scalar(
        f"SELECT COUNT(*) AS count FROM applications AS a WHERE {analytics.OPEN_SCOPE_DRIFT}"
    ) or 0
    open_apps = db.scalar(
        f"""
SELECT COUNT(DISTINCT a.app_id) AS count
FROM applications AS a
WHERE {analytics.OPEN_SCOPE_DRIFT}
"""
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

    latest_scan = db.scalar("SELECT MAX(a.last_scanned_at) AS latest_scan FROM applications AS a")
    as_of = f"As of the {str(latest_scan)[:10]} scan, there" if latest_scan else "There"
    narrative = (
        f"{as_of} are {open_drift} open in-scope drift findings across {open_apps} applications. "
        f"{mission_critical} are Mission Critical, and {executive_candidates} have aged into executive escalation."
    )

    return ExecutiveSummary(
        title="Executive Drift Summary",
        narrative=narrative,
        metrics=[
            Metric(label="Open Drift", value=open_drift),
            Metric(label="Mission Critical", value=mission_critical, help_text="rto_score 1-2"),
            Metric(label="Executive Escalations", value=executive_candidates, help_text="120+ days open at last scan"),
            Metric(label="Pending Exemptions", value=pending_exemptions),
        ],
        focus_areas=focus_areas,
        charts=chart_responses,
    )
