"""Text-to-SQL prompt construction and governed question routing."""

from __future__ import annotations

import re

from src import analytics
from src.domain import DOMAIN_RULES, format_schema_for_prompt
from src.glossary import term_by_key
from src.llm_planner import resolve_query_decision
from src.models import AnalyticsIntent, ConversationContext, PromptBundle, QueryFilters, QueryPlan


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
    "Phoenix",
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


FOLLOW_UP_MARKERS = ("that", "those", "them", "narrow", "only", "same", "drill down", "what about")


def generate_query_plan(question: str, *, context: ConversationContext | None = None) -> QueryPlan:
    """Map natural language to a safe analytics query plan.

    A configured LLM can select a typed intent and approved scope. The existing
    deterministic router remains the fallback and always constructs the SQL plan.
    """

    if _is_suggested_question(question):
        # The prompt buttons always mean the same thing, whatever was asked before them.
        intent, filters = _deterministic_route(question, None)
        plan = _build_analytics_plan(intent, filters)
        plan.filters = filters
        plan.resolved_question = _resolved_question(intent, filters)
        plan.planner = "deterministic"
        return plan

    fallback_intent, fallback_filters = _deterministic_route(question, context)
    decision = resolve_query_decision(
        question,
        context=context,
        data_centers=KNOWN_DATA_CENTERS,
        products=KNOWN_PRODUCTS,
    )
    question_type = "data"
    term = None
    if decision:
        question_type = decision.question_type
        term = decision.term if term_by_key(decision.term) else None
        if question_type == "definition" and term is None:
            question_type = "data"
        intent = decision.intent
        filters = QueryFilters(
            data_center=_canonical_known_value(decision.data_center, KNOWN_DATA_CENTERS)
            or fallback_filters.data_center,
            product=_canonical_known_value(decision.product, KNOWN_PRODUCTS) or fallback_filters.product,
            include_high=decision.include_high
            if decision.include_high is not None
            else fallback_filters.include_high,
        )
        planner = "llm"
    else:
        intent = fallback_intent
        filters = fallback_filters
        planner = "deterministic"
        if looks_out_of_scope(question, context):
            question_type = "out_of_scope"

    plan = _build_analytics_plan(intent, filters)
    plan.filters = filters
    plan.resolved_question = _resolved_question(intent, filters)
    plan.planner = planner
    plan.question_type = question_type
    plan.term = term
    return plan


_SCOPE_WORDS = {
    "app", "apps", "application", "applications", "finding", "findings", "drift", "drifting", "product",
    "products", "datacenter", "rto", "critical", "high", "priority", "exemption", "exemptions", "aging",
    "oldest", "longest", "open", "closed", "risk", "version", "versions", "patch", "portfolio", "owner",
    "owners", "tier", "tiers", "cluster", "clusters", "scan", "kpi", "kpis", "summary", "overview",
}
_SCOPE_PREFIXES = ("escalat", "remediat", "exempt", "complian", "governan", "resilien")


def looks_out_of_scope(question: str, context: ConversationContext | None = None) -> bool:
    """Keyword check used when no LLM planner is available: nothing in the question ties it to drift data."""

    normalized = " ".join(question.lower().replace("-", " ").split())
    words = set(re.findall(r"[a-z0-9]+", normalized))
    if words & _SCOPE_WORDS or any(word.startswith(_SCOPE_PREFIXES) for word in words):
        return False
    if "data center" in normalized or "in scope" in normalized:
        return False
    if _find_known_value(normalized, KNOWN_DATA_CENTERS) or _find_known_value(normalized, KNOWN_PRODUCTS):
        return False
    if context and any(marker in normalized for marker in FOLLOW_UP_MARKERS):
        return False
    return True


def _is_suggested_question(question: str) -> bool:
    return _plain(question) in {_plain(suggested) for suggested in SUGGESTED_QUESTIONS}


def _plain(text: str) -> str:
    return " ".join(text.casefold().strip().rstrip("?.!").split())


def _deterministic_route(
    question: str, context: ConversationContext | None
) -> tuple[AnalyticsIntent, QueryFilters]:
    """Return the existing keyword-and-context route as the safe planner fallback."""

    normalized = " ".join(question.lower().replace("-", " ").split())
    explicit_data_center = _find_known_value(normalized, KNOWN_DATA_CENTERS)
    explicit_product = _find_known_value(normalized, KNOWN_PRODUCTS)
    explicit_intent = _detect_intent(normalized)
    follow_up_marker = bool(context) and any(marker in normalized for marker in FOLLOW_UP_MARKERS)
    follow_up = bool(context) and (explicit_intent is None or follow_up_marker)

    filters = QueryFilters(
        data_center=explicit_data_center or (context.filters.data_center if follow_up and context else None),
        product=explicit_product or (context.filters.product if follow_up and context else None),
        include_high=_explicit_include_high(normalized, context if follow_up else None),
    )
    # "Datacenter" and "product" can describe the scope of a follow-up, not a new analysis type.
    # Preserve the previous analysis in phrases such as "do that for Minneapolis Datacenter."
    soft_intent = explicit_intent in {"drift_by_data_center", "drift_by_product"}
    if follow_up_marker and soft_intent and context:
        intent = context.intent
    else:
        intent = explicit_intent or (context.intent if follow_up and context else "top_drifting_apps")

    return intent, filters


def _build_analytics_plan(intent: AnalyticsIntent, filters: QueryFilters) -> QueryPlan:
    if intent == "executive_escalation_candidates":
        plan = analytics.executive_escalation_candidates(data_center=filters.data_center, product=filters.product)
    elif intent == "aging_bucket_analysis":
        plan = analytics.aging_bucket_analysis(data_center=filters.data_center, product=filters.product)
    elif intent == "exemption_analysis":
        plan = analytics.exemption_analysis(data_center=filters.data_center, product=filters.product)
    elif intent == "rto_risk_distribution":
        plan = analytics.rto_risk_distribution(data_center=filters.data_center, product=filters.product)
    elif intent == "critical_apps_with_open_drift":
        plan = analytics.critical_apps_with_open_drift(
            include_high=bool(filters.include_high),
            data_center=filters.data_center,
            product=filters.product,
        )
    elif intent == "drift_by_data_center":
        plan = analytics.drift_by_data_center(data_center=filters.data_center, product=filters.product)
    elif intent == "drift_by_product":
        plan = analytics.drift_by_product(data_center=filters.data_center, product=filters.product)
    else:
        plan = analytics.top_drifting_apps(data_center=filters.data_center, product=filters.product)

    return plan


def _detect_intent(normalized: str) -> AnalyticsIntent | None:
    if any(term in normalized for term in ["executive", "120"]):
        return "executive_escalation_candidates"
    if any(term in normalized for term in ["aging", "age bucket", "bucket", "90 day", "60 day"]):
        return "aging_bucket_analysis"
    if "exemption" in normalized:
        return "exemption_analysis"
    if "rto" in normalized or "risk distribution" in normalized or "tier" in normalized:
        return "rto_risk_distribution"
    if "critical" in normalized or "priority" in normalized or "high" in normalized:
        return "critical_apps_with_open_drift"
    if "data center" in normalized or "datacenter" in normalized or "hosting" in normalized:
        return "drift_by_data_center"
    if "product" in normalized or "platform" in normalized:
        return "drift_by_product"
    if any(term in normalized for term in ["oldest", "longest", "top drifting", "most days"]):
        return "top_drifting_apps"
    return None


def _explicit_include_high(normalized: str, context: ConversationContext | None) -> bool | None:
    if "high" in normalized or "priority" in normalized:
        return True
    if "mission critical" in normalized or "critical" in normalized:
        return False
    return context.filters.include_high if context else None


def _resolved_question(intent: str, filters: QueryFilters) -> str:
    labels = {
        "top_drifting_apps": "Show open applications with drift",
        "critical_apps_with_open_drift": "Show critical and high applications with open drift"
        if filters.include_high
        else "Show Mission Critical applications with open drift",
        "drift_by_product": "Show drift concentration by product",
        "drift_by_data_center": "Show drift concentration by data center",
        "exemption_analysis": "Analyze exemption status for open drift",
        "aging_bucket_analysis": "Show aging buckets for open drift",
        "executive_escalation_candidates": "Show applications needing executive escalation",
        "rto_risk_distribution": "Show RTO risk distribution for open drift",
    }
    scope = []
    if filters.data_center:
        scope.append(f"in {filters.data_center}")
    if filters.product:
        scope.append(f"for {filters.product}")
    return " ".join([labels.get(intent, "Analyze open drift"), *scope])


def _find_known_value(normalized_question: str, known_values: list[str]) -> str | None:
    """Return a canonical known value mentioned in the question."""

    for value in known_values:
        if value.lower() in normalized_question:
            return value
    return None


def _canonical_known_value(candidate: str | None, known_values: list[str]) -> str | None:
    if not candidate:
        return None
    normalized_candidate = " ".join(candidate.casefold().split())
    for value in known_values:
        if " ".join(value.casefold().split()) == normalized_candidate:
            return value
    return None
