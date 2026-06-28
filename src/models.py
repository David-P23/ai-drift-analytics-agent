"""Structured response objects for the drift analytics agent."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ChartKind = Literal["bar", "table_only"]


class ChartSpec(BaseModel):
    """Portable chart metadata consumed by the Streamlit UI."""

    kind: ChartKind = "table_only"
    title: str
    x: str | None = None
    y: str | None = None
    color: str | None = None


class QueryPlan(BaseModel):
    """Generated SQL and intent metadata before execution."""

    question: str
    sql: str
    intent: str
    rationale: str
    chart: ChartSpec | None = None


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
