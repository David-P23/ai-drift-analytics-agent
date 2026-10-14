"""Translate the agent's SQLite query templates to Databricks SQL.

The query templates in src/analytics.py are written for SQLite and validated by
src/sql_safety.py before anything runs. Only one construct differs between the
two engines in those templates: day arithmetic. SQLite has no date type, so the
templates compute "days open" with julianday(); Databricks SQL has DATEDIFF.

Translation runs after validation, and it refuses any SQLite-only function it
does not know how to translate rather than sending something that might behave
differently on the warehouse.
"""

from __future__ import annotations

import re

from src.sql_safety import SQLSafetyError

# CAST(julianday('now') - julianday(a.drift_detected_on) AS INTEGER)
_DAYS_SINCE = re.compile(
    r"CAST\(\s*julianday\(\s*'now'\s*\)\s*-\s*julianday\(\s*([A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?)\s*\)\s*AS\s+INTEGER\s*\)",
    flags=re.IGNORECASE,
)

_SQLITE_ONLY = re.compile(r"\b(julianday|datetime|strftime|date)\s*\(|\bPRAGMA\b", flags=re.IGNORECASE)


def to_databricks(sql: str) -> str:
    """Return the Databricks SQL equivalent of a validated SQLite SELECT."""

    translated = _DAYS_SINCE.sub(r"DATEDIFF(CURRENT_DATE(), \1)", sql)
    leftover = _SQLITE_ONLY.search(_strip_string_literals(translated))
    if leftover:
        raise SQLSafetyError(
            f"'{leftover.group(0).strip()}' is SQLite-specific and has no Databricks translation yet."
        )
    return translated


def _strip_string_literals(sql: str) -> str:
    return re.sub(r"'(?:''|[^'])*'", "''", sql)
