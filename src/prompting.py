"""Text-to-SQL prompt construction and deterministic intent routing."""

from __future__ import annotations

from src import analytics
from src.domain import DOMAIN_RULES, format_schema_for_prompt
from src.models import PromptBundle, QueryPlan


SUGGESTED_QUESTIONS = [
    "Which applications have the oldest open drift?",
    "Show mission critical apps with open drift.",
    "Show critical and high priority open drift.",
    "Where is drift concentrated by product?",
    "Where is drift concentrated by data center?",
    "Analyze exemption status for open drift.",
    "Show aging buckets for open drift.",
    "Which apps need executive escalation?",
    "Show RTO risk distribution.",
]

KNOWN_DATA_CENTERS = [
    "Ashburn",
    "Chandler",
    "Charlotte",
    "Columbus",
    "Dallas",
    "Des Moines",
    "Eagan",
    "Minneapolis",
    "St. Louis",
]

KNOWN_PRODUCTS = [
    "Apache HTTP Server",
    "Apigee Gateway",
    "AppDynamics Agent",
    "CyberArk",
    "Dynatrace OneAgent",
    "IBM MQ",
    "IIS",
    "JBoss EAP",
    "Kafka",
    "MongoDB Enterprise",
    "MuleSoft Runtime",
    "NGINX",
    "Okta Agent",
    "Oracle Database",
    "PingFederate",
    "PostgreSQL",
    "RHEL",
    "SailPoint IQService",
    "Splunk UF",
    "SQL Server",
    "SUSE Linux",
    "Tanium Client",
    "TIBCO EMS",
    "Tomcat",
    "Ubuntu Server",
    "WebLogic",
    "WebSphere",
    "Windows Server",
]


def build_text_to_sql_prompt(question: str) -> PromptBundle:
    """Build a constrained text-to-SQL prompt that matches the safety layer."""

    system = f"""
{DOMAIN_RULES}

SQL generation constraints:
- Use only the tables and columns listed in the schema.
- Generate exactly one SQLite SELECT statement.
- Do not generate INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, PRAGMA, or ATTACH.
- Include a LIMIT unless the user explicitly asks for an aggregate with a small grouped result.
- Use a table alias such as applications AS a and prefix selected columns with a. when practical.
- For drift, compare a.detected_version <> a.approved_version.
- For open findings, use a.status = 'Open'.
- For scoped analytics, use a.in_scope = 'Y'.
- Return JSON with keys: sql, rationale, chart_title, chart_x, chart_y.

Schema:
{format_schema_for_prompt()}
""".strip()

    user = f"User question: {question.strip()}"
    return PromptBundle(system=system, user=user)


def generate_query_plan(question: str) -> QueryPlan:
    """Map natural language to a safe analytics query plan."""

    normalized = " ".join(question.lower().split())
    data_center = _find_known_value(normalized, KNOWN_DATA_CENTERS)
    product = _find_known_value(normalized, KNOWN_PRODUCTS)

    if any(term in normalized for term in ["executive", "120"]):
        return analytics.executive_escalation_candidates()
    if any(term in normalized for term in ["aging", "age bucket", "bucket", "90 day", "60 day"]):
        return analytics.aging_bucket_analysis()
    if "exemption" in normalized:
        return analytics.exemption_analysis()
    if "rto" in normalized or "risk distribution" in normalized or "tier" in normalized:
        return analytics.rto_risk_distribution(data_center=data_center, product=product)
    if "critical" in normalized or "priority" in normalized or "high" in normalized:
        include_high = "high" in normalized or "priority" in normalized
        return analytics.critical_apps_with_open_drift(
            include_high=include_high,
            data_center=data_center,
            product=product,
        )
    if "data center" in normalized or "datacenter" in normalized or "hosting" in normalized:
        return analytics.drift_by_data_center()
    if "product" in normalized or "platform" in normalized:
        return analytics.drift_by_product()
    return analytics.top_drifting_apps()


def _find_known_value(normalized_question: str, known_values: list[str]) -> str | None:
    """Return a canonical known value mentioned in the question."""

    for value in known_values:
        if value.lower() in normalized_question:
            return value
    return None
