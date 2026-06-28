"""Domain rules and schema metadata for enterprise drift analytics."""

from __future__ import annotations

from dataclasses import dataclass


DOMAIN_RULES = """
You are an enterprise application drift analytics agent.

Business rules:
- Drift means detected_version differs from approved_version.
- rto_score 1-2 means Mission Critical.
- rto_score 3-4 means High.
- rto_score 5-6 means Medium.
- rto_score 7-10 means Low.
- Critical/high priority queries must use explicit rto_score logic.
- Aging threshold 60 days means warning.
- Aging threshold 90 days means senior escalation.
- Aging threshold 120 days means executive escalation.
- status values are Open and Closed.
- exemption_requested values are Y and N.
- exemption_result values are Approved, Pending, Denied, and NULL.
- in_scope values are Y and N.
- Prefix columns with aliases when joining.
- Return SQLite-compatible, read-only SELECT statements only.
""".strip()


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    description: str


@dataclass(frozen=True)
class TableInfo:
    name: str
    description: str
    columns: tuple[ColumnInfo, ...]


APPLICATIONS_TABLE = TableInfo(
    name="applications",
    description="One row per merged enterprise application/version drift finding.",
    columns=(
        ColumnInfo("finding_id", "Unique merged finding identifier, usually app_id plus drift_id."),
        ColumnInfo("app_id", "Stable application identifier from the workbook."),
        ColumnInfo("drift_id", "Source drift instance identifier from the workbook when supplied."),
        ColumnInfo("app_name", "Human-readable application name."),
        ColumnInfo("business_owner", "Business accountable owner."),
        ColumnInfo("technology_owner", "Technology accountable owner."),
        ColumnInfo("product", "Enterprise product or platform family."),
        ColumnInfo("data_center", "Primary hosting data center."),
        ColumnInfo("approved_version", "Approved software or platform version."),
        ColumnInfo("detected_version", "Version detected by inventory scan."),
        ColumnInfo("rto_score", "Recovery-time objective risk score from 1 to 10."),
        ColumnInfo("status", "Finding status: Open or Closed."),
        ColumnInfo("exemption_requested", "Whether an exemption was requested: Y or N."),
        ColumnInfo("exemption_result", "Approved, Pending, Denied, or NULL."),
        ColumnInfo("in_scope", "Whether the application is in compliance scope: Y or N."),
        ColumnInfo("drift_detected_on", "ISO date when drift was first detected."),
        ColumnInfo("last_scanned_at", "ISO timestamp of the latest inventory scan."),
        ColumnInfo("note_count", "Number of drift note records merged into the finding."),
        ColumnInfo("latest_note_date", "Most recent merged note date when supplied."),
        ColumnInfo("latest_note_type", "Most recent merged note type when supplied."),
        ColumnInfo("latest_note_author_role", "Author role for the most recent merged note."),
        ColumnInfo("latest_note_text", "Most recent merged note text."),
        ColumnInfo("escalation_flag", "Y when any merged note flagged escalation; otherwise N or NULL."),
    ),
)


SCHEMA = (APPLICATIONS_TABLE,)


def schema_map() -> dict[str, set[str]]:
    """Return allowed table and column names for validation."""

    return {table.name: {column.name for column in table.columns} for table in SCHEMA}


def format_schema_for_prompt() -> str:
    """Render schema metadata for a text-to-SQL prompt."""

    rendered: list[str] = []
    for table in SCHEMA:
        rendered.append(f"Table: {table.name} - {table.description}")
        for column in table.columns:
            rendered.append(f"- {table.name}.{column.name}: {column.description}")
    return "\n".join(rendered)
