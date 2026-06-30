"""Reusable drift analytics query plans."""

from __future__ import annotations

from src.models import ChartSpec, QueryPlan


AGE_DAYS = "CAST(julianday('now') - julianday(a.drift_detected_on) AS INTEGER)"
DRIFT_CONDITION = "a.detected_version <> a.approved_version"
OPEN_SCOPE_DRIFT = f"{DRIFT_CONDITION} AND a.status = 'Open' AND a.in_scope = 'Y'"

RTO_TIER_CASE = """
CASE
    WHEN a.rto_score BETWEEN 1 AND 2 THEN 'Mission Critical'
    WHEN a.rto_score BETWEEN 3 AND 4 THEN 'High'
    WHEN a.rto_score BETWEEN 5 AND 6 THEN 'Medium'
    ELSE 'Low'
END
""".strip()

AGING_BUCKET_CASE = f"""
CASE
    WHEN {AGE_DAYS} >= 120 THEN '120+ Executive escalation'
    WHEN {AGE_DAYS} >= 90 THEN '90-119 Senior escalation'
    WHEN {AGE_DAYS} >= 60 THEN '60-89 Warning'
    ELSE '<60 Current'
END
""".strip()

AI_PRIORITY_SCORE = f"""
(
    CASE
        WHEN a.rto_score BETWEEN 1 AND 2 THEN 40
        WHEN a.rto_score BETWEEN 3 AND 4 THEN 30
        WHEN a.rto_score BETWEEN 5 AND 6 THEN 18
        ELSE 8
    END
    + CASE
        WHEN {AGE_DAYS} >= 120 THEN 35
        WHEN {AGE_DAYS} >= 90 THEN 25
        WHEN {AGE_DAYS} >= 60 THEN 15
        ELSE 5
    END
    + CASE
        WHEN a.exemption_requested = 'Y' AND a.exemption_result = 'Pending' THEN 15
        WHEN a.exemption_requested = 'Y' AND a.exemption_result = 'Denied' THEN 18
        WHEN a.exemption_requested = 'Y' AND a.exemption_result = 'Approved' THEN -8
        ELSE 0
    END
)
""".strip()

AI_ACTION_CASE = f"""
CASE
    WHEN {AI_PRIORITY_SCORE} >= 85 THEN 'Executive intervention'
    WHEN {AI_PRIORITY_SCORE} >= 70 THEN 'Remediation command'
    WHEN {AI_PRIORITY_SCORE} >= 50 THEN 'Governance watch'
    ELSE 'Managed backlog'
END
""".strip()


def top_drifting_apps(limit: int = 10) -> QueryPlan:
    sql = f"""
SELECT
    a.finding_id AS finding_id,
    a.app_id AS app_id,
    a.drift_id AS drift_id,
    a.app_name AS app_name,
    a.product AS product,
    a.data_center AS data_center,
    a.approved_version AS approved_version,
    a.detected_version AS detected_version,
    a.rto_score AS rto_score,
    {AGE_DAYS} AS days_open,
    a.exemption_requested AS exemption_requested,
    a.exemption_result AS exemption_result
FROM applications AS a
WHERE {OPEN_SCOPE_DRIFT}
ORDER BY days_open DESC, a.rto_score ASC, a.app_name ASC
LIMIT {limit}
""".strip()
    return QueryPlan(
        question="Top drifting applications",
        sql=sql,
        intent="top_drifting_apps",
        rationale="Ranks open in-scope drift by age and then by RTO risk.",
        chart=ChartSpec(kind="bar", title="Oldest Open Drift", x="app_name", y="days_open"),
    )


def _scope_filter_clause(*, data_center: str | None = None, product: str | None = None) -> str:
    clauses: list[str] = []
    if data_center:
        safe_data_center = data_center.replace("'", "''")
        clauses.append(f"LOWER(a.data_center) = LOWER('{safe_data_center}')")
    if product:
        safe_product = product.replace("'", "''")
        clauses.append(f"LOWER(a.product) = LOWER('{safe_product}')")
    return "\n  AND " + "\n  AND ".join(clauses) if clauses else ""


def _scope_label(*, data_center: str | None = None, product: str | None = None) -> str:
    scope_parts = []
    if data_center:
        scope_parts.append(f"in {data_center}")
    if product:
        scope_parts.append(f"for {product}")
    return " ".join(scope_parts)


def critical_apps_with_open_drift(
    *,
    include_high: bool = False,
    limit: int = 25,
    data_center: str | None = None,
    product: str | None = None,
) -> QueryPlan:
    rto_filter = "a.rto_score BETWEEN 1 AND 4" if include_high else "a.rto_score BETWEEN 1 AND 2"
    label = "critical and high" if include_high else "mission critical"
    scope_clause = _scope_filter_clause(data_center=data_center, product=product)
    scoped_label = _scope_label(data_center=data_center, product=product)
    sql = f"""
SELECT
    a.finding_id AS finding_id,
    a.app_id AS app_id,
    a.drift_id AS drift_id,
    a.app_name AS app_name,
    a.product AS product,
    a.data_center AS data_center,
    a.rto_score AS rto_score,
    {RTO_TIER_CASE} AS rto_tier,
    a.approved_version AS approved_version,
    a.detected_version AS detected_version,
    {AGE_DAYS} AS days_open,
    a.business_owner AS business_owner,
    a.technology_owner AS technology_owner
FROM applications AS a
WHERE {OPEN_SCOPE_DRIFT}
  AND {rto_filter}
  {scope_clause}
ORDER BY a.rto_score ASC, days_open DESC, a.app_name ASC
LIMIT {limit}
""".strip()
    return QueryPlan(
        question=f"Open drift for {label} applications {scoped_label}".strip(),
        sql=sql,
        intent="critical_apps_with_open_drift",
        rationale="Uses explicit rto_score logic for critical/high priority drift.",
        chart=ChartSpec(kind="bar", title="Critical Open Drift by Age", x="app_name", y="days_open"),
    )


def drift_by_product(limit: int = 20) -> QueryPlan:
    sql = f"""
SELECT
    a.product AS product,
    COUNT(*) AS drift_count,
    SUM(CASE WHEN a.rto_score BETWEEN 1 AND 2 THEN 1 ELSE 0 END) AS mission_critical_count,
    ROUND(AVG({AGE_DAYS}), 1) AS average_days_open
FROM applications AS a
WHERE {OPEN_SCOPE_DRIFT}
GROUP BY a.product
ORDER BY drift_count DESC, mission_critical_count DESC, a.product ASC
LIMIT {limit}
""".strip()
    return QueryPlan(
        question="Drift distribution by product",
        sql=sql,
        intent="drift_by_product",
        rationale="Aggregates open in-scope drift by product family.",
        chart=ChartSpec(kind="bar", title="Product Drift Distribution", x="product", y="drift_count"),
    )


def drift_by_data_center(limit: int = 20) -> QueryPlan:
    sql = f"""
SELECT
    a.data_center AS data_center,
    COUNT(*) AS drift_count,
    SUM(CASE WHEN a.rto_score BETWEEN 1 AND 4 THEN 1 ELSE 0 END) AS critical_high_count,
    ROUND(AVG({AGE_DAYS}), 1) AS average_days_open
FROM applications AS a
WHERE {OPEN_SCOPE_DRIFT}
GROUP BY a.data_center
ORDER BY drift_count DESC, critical_high_count DESC, a.data_center ASC
LIMIT {limit}
""".strip()
    return QueryPlan(
        question="Drift concentration by data center",
        sql=sql,
        intent="drift_by_data_center",
        rationale="Finds hosting concentration for open in-scope drift.",
        chart=ChartSpec(kind="bar", title="Data Center Concentration", x="data_center", y="drift_count"),
    )


def exemption_analysis(limit: int = 20) -> QueryPlan:
    sql = f"""
SELECT
    a.exemption_requested AS exemption_requested,
    COALESCE(a.exemption_result, 'None') AS exemption_result,
    COUNT(*) AS drift_count,
    SUM(CASE WHEN a.rto_score BETWEEN 1 AND 4 THEN 1 ELSE 0 END) AS critical_high_count,
    MAX({AGE_DAYS}) AS oldest_days_open
FROM applications AS a
WHERE {OPEN_SCOPE_DRIFT}
GROUP BY a.exemption_requested, COALESCE(a.exemption_result, 'None')
ORDER BY drift_count DESC, critical_high_count DESC, oldest_days_open DESC
LIMIT {limit}
""".strip()
    return QueryPlan(
        question="Exemption analysis",
        sql=sql,
        intent="exemption_analysis",
        rationale="Compares requested exemptions, decisions, risk, and age.",
        chart=ChartSpec(kind="bar", title="Exemption Drift Analysis", x="exemption_result", y="drift_count"),
    )


def aging_bucket_analysis(limit: int = 10) -> QueryPlan:
    sql = f"""
SELECT
    {AGING_BUCKET_CASE} AS aging_bucket,
    COUNT(*) AS app_count,
    SUM(CASE WHEN a.rto_score BETWEEN 1 AND 4 THEN 1 ELSE 0 END) AS critical_high_count,
    MAX({AGE_DAYS}) AS oldest_days_open
FROM applications AS a
WHERE {OPEN_SCOPE_DRIFT}
GROUP BY aging_bucket
ORDER BY oldest_days_open DESC
LIMIT {limit}
""".strip()
    return QueryPlan(
        question="Aging bucket analysis",
        sql=sql,
        intent="aging_bucket_analysis",
        rationale="Buckets open drift into warning, senior escalation, and executive escalation thresholds.",
        chart=ChartSpec(kind="bar", title="Aging Bucket Chart", x="aging_bucket", y="app_count"),
    )


def executive_escalation_candidates(limit: int = 25) -> QueryPlan:
    sql = f"""
SELECT
    a.finding_id AS finding_id,
    a.app_id AS app_id,
    a.drift_id AS drift_id,
    a.app_name AS app_name,
    a.product AS product,
    a.data_center AS data_center,
    a.rto_score AS rto_score,
    {RTO_TIER_CASE} AS rto_tier,
    {AGE_DAYS} AS days_open,
    a.exemption_requested AS exemption_requested,
    a.exemption_result AS exemption_result,
    a.business_owner AS business_owner,
    a.technology_owner AS technology_owner
FROM applications AS a
WHERE {OPEN_SCOPE_DRIFT}
  AND {AGE_DAYS} >= 120
ORDER BY a.rto_score ASC, days_open DESC, a.app_name ASC
LIMIT {limit}
""".strip()
    return QueryPlan(
        question="Executive escalation candidates",
        sql=sql,
        intent="executive_escalation_candidates",
        rationale="Finds open in-scope drift at or beyond the 120-day executive escalation threshold.",
        chart=ChartSpec(kind="bar", title="Executive Escalation Candidates", x="app_name", y="days_open"),
    )


def rto_risk_distribution(
    limit: int = 10,
    *,
    data_center: str | None = None,
    product: str | None = None,
) -> QueryPlan:
    scope_clause = _scope_filter_clause(data_center=data_center, product=product)
    scoped_label = _scope_label(data_center=data_center, product=product)
    sql = f"""
SELECT
    {RTO_TIER_CASE} AS rto_tier,
    COUNT(*) AS drift_count,
    MAX({AGE_DAYS}) AS oldest_days_open
FROM applications AS a
WHERE {OPEN_SCOPE_DRIFT}
  {scope_clause}
GROUP BY rto_tier
ORDER BY
    CASE rto_tier
        WHEN 'Mission Critical' THEN 1
        WHEN 'High' THEN 2
        WHEN 'Medium' THEN 3
        ELSE 4
    END
LIMIT {limit}
""".strip()
    return QueryPlan(
        question=f"RTO risk distribution {scoped_label}".strip(),
        sql=sql,
        intent="rto_risk_distribution",
        rationale="Maps rto_score into Mission Critical, High, Medium, and Low buckets.",
        chart=ChartSpec(kind="bar", title="RTO Risk Distribution", x="rto_tier", y="drift_count"),
    )


def ai_risk_intelligence(limit: int = 50) -> QueryPlan:
    sql = f"""
SELECT
    a.finding_id AS finding_id,
    a.app_id AS app_id,
    a.drift_id AS drift_id,
    a.app_name AS app_name,
    a.product AS product,
    a.data_center AS data_center,
    a.rto_score AS rto_score,
    {RTO_TIER_CASE} AS rto_tier,
    {AGE_DAYS} AS days_open,
    {AGING_BUCKET_CASE} AS aging_bucket,
    a.exemption_requested AS exemption_requested,
    COALESCE(a.exemption_result, 'None') AS exemption_result,
    {AI_PRIORITY_SCORE} AS ai_priority_score,
    {AI_ACTION_CASE} AS ai_recommended_action,
    a.business_owner AS business_owner,
    a.technology_owner AS technology_owner
FROM applications AS a
WHERE {OPEN_SCOPE_DRIFT}
ORDER BY ai_priority_score DESC, a.rto_score ASC, days_open DESC, a.app_name ASC
LIMIT {limit}
""".strip()
    return QueryPlan(
        question="AI risk intelligence",
        sql=sql,
        intent="ai_risk_intelligence",
        rationale="Scores open drift by RTO criticality, aging threshold, and exemption decision state.",
        chart=ChartSpec(kind="bar", title="AI Priority Score", x="app_name", y="ai_priority_score"),
    )


def drift_cluster_source(limit: int = 1000) -> QueryPlan:
    sql = f"""
SELECT
    a.finding_id AS finding_id,
    a.app_id AS app_id,
    a.drift_id AS drift_id,
    a.app_name AS app_name,
    a.product AS product,
    a.data_center AS data_center,
    a.approved_version AS approved_version,
    a.detected_version AS detected_version,
    a.rto_score AS rto_score,
    a.status AS status,
    a.exemption_requested AS exemption_requested,
    a.exemption_result AS exemption_result,
    a.drift_detected_on AS drift_detected_on,
    a.business_owner AS business_owner,
    a.technology_owner AS technology_owner
FROM applications AS a
WHERE {DRIFT_CONDITION}
  AND a.in_scope = 'Y'
ORDER BY a.product ASC, a.approved_version ASC, a.drift_detected_on ASC
LIMIT {limit}
""".strip()
    return QueryPlan(
        question="Drift cluster source rows",
        sql=sql,
        intent="drift_cluster_source",
        rationale="Returns in-scope drift findings for rolling-window cluster detection.",
        chart=ChartSpec(kind="table_only", title="Drift Cluster Source"),
    )


def all_dashboard_plans() -> list[QueryPlan]:
    """Charts expected on the Streamlit dashboard."""

    return [
        drift_by_product(),
        rto_risk_distribution(),
        drift_by_data_center(),
        aging_bucket_analysis(),
        ai_risk_intelligence(),
    ]
