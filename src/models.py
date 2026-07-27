"""Structured response objects for the drift analytics agent."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ChartKind = Literal["bar", "table_only"]
PlannerKind = Literal["deterministic", "llm"]
AnalyticsIntent = Literal[
    "top_drifting_apps",
    "critical_apps_with_open_drift",
    "drift_by_product",
    "drift_by_data_center",
    "exemption_analysis",
    "aging_bucket_analysis",
    "executive_escalation_candidates",
    "rto_risk_distribution",
    "ai_risk_intelligence",
    "drift_cluster_source",
]


class ChartSpec(BaseModel):
    """Portable chart metadata consumed by the Streamlit UI."""

    kind: ChartKind = "table_only"
    title: str
    x: str | None = None
    y: str | None = None
    color: str | None = None


class QueryFilters(BaseModel):
    """Canonical filters carried between conversational turns."""

    data_center: str | None = None
    product: str | None = None
    include_high: bool | None = None


class LLMPlannerDecision(BaseModel):
    """Strict, non-executable routing decision returned by the LLM planner."""

    model_config = ConfigDict(extra="forbid")

    intent: AnalyticsIntent
    data_center: str | None = None
    product: str | None = None
    include_high: bool | None = None


class ConversationContext(BaseModel):
    """The last resolved analytics request, kept per Streamlit session."""

    intent: str
    resolved_question: str
    filters: QueryFilters = Field(default_factory=QueryFilters)


class PolicySource(BaseModel):
    """A policy-document chunk used as evidence in a RAG answer."""

    document: str
    section: str
    excerpt: str


class PolicyGuidance(BaseModel):
    """Brief, retrieval-grounded policy guidance for the executive surface."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    recommended_actions: list[str] = Field(default_factory=list, max_length=3)


class QueryPlan(BaseModel):
    """Generated SQL and intent metadata before execution."""

    question: str
    sql: str
    intent: AnalyticsIntent
    rationale: str
    chart: ChartSpec | None = None
    filters: QueryFilters = Field(default_factory=QueryFilters)
    resolved_question: str | None = None
    planner: PlannerKind = "deterministic"


class SafeQuery(BaseModel):
    """Validated SQL ready for read-only execution."""

    original_sql: str
    sql: str
    warnings: list[str] = Field(default_factory=list)


class QueryResponse(BaseModel):
    """The primary response object returned by the agent."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    question: str
    answer: str
    sql: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    chart: ChartSpec | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    intent: str | None = None
    resolved_question: str | None = None
    planner: PlannerKind | None = None
    policy_sources: list[PolicySource] = Field(default_factory=list)
    retrieval_mode: Literal["semantic", "lexical"] | None = None
    conversation_context: ConversationContext | None = None


class Metric(BaseModel):
    label: str
    value: int | float | str
    help_text: str | None = None


class ExecutiveSummary(BaseModel):
    """Structured executive summary for the portfolio demo mode."""

    title: str
    narrative: str
    metrics: list[Metric]
    focus_areas: list[str]
    charts: list[QueryResponse]


class PromptBundle(BaseModel):
    """Prompt text used for text-to-SQL generation tests and LLM handoff."""

    system: str
    user: str


class SpreadsheetImportIssue(BaseModel):
    """Validation issue found while importing a workbook sheet."""

    sheet: str
    row_number: int | None = None
    message: str


class SpreadsheetImportResult(BaseModel):
    """Normalized workbook import result ready for database refresh."""

    filename: str | None = None
    sheets: list[str]
    row_count: int
    rows: list[dict[str, Any]]
    warnings: list[SpreadsheetImportIssue] = Field(default_factory=list)
