"""Compare query results from two backends (SQLite demo vs Databricks gold)."""

from __future__ import annotations

from typing import Any

# ROUND(AVG(...), 1) can land one step apart on an exact .x5 tie: SQLite rounds the
# binary double (232.85 -> 232.8), Databricks rounds the decimal half-up (232.9).
ROUNDING_TOLERANCE = 0.1 + 1e-9


def rows_match(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    """True when both results have the same rows, columns and values, in the same order."""

    return first_difference(left, right) is None


def first_difference(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> str | None:
    """Describe the first mismatch, or return None when the results match."""

    if len(left) != len(right):
        return f"{len(left)} rows vs {len(right)} rows"
    for index, (a, b) in enumerate(zip(left, right)):
        if list(a.keys()) != list(b.keys()):
            return f"row {index}: columns {list(a.keys())} vs {list(b.keys())}"
        for key in a:
            x, y = a[key], b[key]
            if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool):
                if isinstance(x, float) or isinstance(y, float):
                    if abs(float(x) - float(y)) > ROUNDING_TOLERANCE:
                        return f"row {index}, {key}: {x!r} vs {y!r}"
                    continue
            if x != y:
                return f"row {index}, {key}: {x!r} vs {y!r}"
    return None
