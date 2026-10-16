"""Translate the agent's SQLite query templates to Databricks SQL.

The query templates in src/analytics.py are written for SQLite and validated by
src/sql_safety.py before anything runs. Only one construct differs between the
two engines in those templates: day arithmetic. SQLite has no date type, so the
templates compute "days open" with julianday(); Databricks SQL has DATEDIFF.

Translation runs after validation, and it refuses any SQLite-only function it
does not know how to translate rather than sending something that might behave
differently on the warehouse.

Known difference, left as is: ROUND on an exact .x5 tie. SQLite rounds the binary
double (232.85 is stored as 232.8499... and becomes 232.8); Databricks rounds the
decimal value half-up (232.9). Only one-decimal display averages are affected.
"""

from __future__ import annotations

import re

from src.sql_safety import SQLSafetyError

_COLUMN = r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?"

# CAST(julianday(date(a.last_scanned_at)) - julianday(a.drift_detected_on) AS INTEGER)
# CAST(julianday('now') - julianday(a.drift_detected_on) AS INTEGER)
_DAYS_BETWEEN = re.compile(
    rf"CAST\(\s*julianday\(\s*(?:(?P<now>'now')|date\(\s*(?P<end>{_COLUMN})\s*\))\s*\)"
    rf"\s*-\s*julianday\(\s*(?P<start>{_COLUMN})\s*\)\s*AS\s+INTEGER\s*\)",
    flags=re.IGNORECASE,
)

_SQLITE_ONLY = re.compile(r"\b(julianday|datetime|strftime|date)\s*\(|\bPRAGMA\b", flags=re.IGNORECASE)


def _days_between(match: re.Match[str]) -> str:
    # CAST(timestamp AS DATE) uses the session time zone; scan timestamps are
    # recorded at midday UTC so the calendar date is the same in any US zone.
    end = "CURRENT_DATE()" if match.group("now") else f"CAST({match.group('end')} AS DATE)"
    return f"DATEDIFF({end}, {match.group('start')})"


def to_databricks(sql: str) -> str:
    """Return the Databricks SQL equivalent of a validated SQLite SELECT."""

    translated = _DAYS_BETWEEN.sub(_days_between, sql)
    leftover = _SQLITE_ONLY.search(_strip_string_literals(translated))
    if leftover:
        raise SQLSafetyError(
            f"'{leftover.group(0).strip()}' is SQLite-specific and has no Databricks translation yet."
        )
    return translated


def _strip_string_literals(sql: str) -> str:
    return re.sub(r"'(?:''|[^'])*'", "''", sql)
