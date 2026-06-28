"""Safety checks for generated SQL before it reaches SQLite."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.models import SafeQuery


FORBIDDEN_KEYWORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "PRAGMA",
    "ATTACH",
    "DETACH",
    "VACUUM",
    "REPLACE",
    "TRUNCATE",
    "MERGE",
}

SQL_KEYWORDS = {
    "SELECT",
    "FROM",
    "WHERE",
    "AND",
    "OR",
    "NOT",
    "NULL",
    "IS",
    "IN",
    "AS",
    "ON",
    "JOIN",
    "LEFT",
    "RIGHT",
    "INNER",
    "OUTER",
    "FULL",
    "CROSS",
    "GROUP",
    "BY",
    "ORDER",
    "HAVING",
    "LIMIT",
    "OFFSET",
    "CASE",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "ASC",
    "DESC",
    "DISTINCT",
    "BETWEEN",
    "LIKE",
    "CAST",
    "INTEGER",
    "REAL",
    "TEXT",
    "COLLATE",
}

SQL_FUNCTIONS = {
    "COUNT",
    "SUM",
    "AVG",
    "MIN",
    "MAX",
    "ROUND",
    "CAST",
    "COALESCE",
    "NULLIF",
    "JULIANDAY",
    "DATE",
    "DATETIME",
    "LOWER",
    "UPPER",
    "ABS",
}


class SQLSafetyError(ValueError):
    """Raised when a SQL statement violates read-only safety rules."""


@dataclass(frozen=True)
class _TopLevelLimit:
    keyword_start: int
    number_start: int | None = None
    number_end: int | None = None
    value: int | None = None


class SQLSafetyValidator:
    """Validate SQLite SELECT statements against a known schema."""

    def __init__(self, schema: dict[str, set[str]], *, max_rows: int = 100) -> None:
        self.schema = schema
        self.max_rows = max_rows
        self.allowed_tables = set(schema)
        self.allowed_columns = set().union(*schema.values()) if schema else set()

    def validate(self, sql: str) -> SafeQuery:
        original_sql = sql
        normalized = self._normalize_statement(self._strip_comments(sql))
        warnings: list[str] = []

        self._reject_empty(normalized)
        self._reject_multiple_statements(normalized)
        self._reject_non_select(normalized)
        self._reject_forbidden_keywords(normalized)
        self._validate_tables_and_columns(normalized)

        limited_sql, limit_warning = self._enforce_limit(normalized)
        if limit_warning:
            warnings.append(limit_warning)

        return SafeQuery(original_sql=original_sql, sql=limited_sql, warnings=warnings)

    @staticmethod
    def _strip_comments(sql: str) -> str:
        sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
        sql = re.sub(r"--[^\n\r]*", " ", sql)
        return sql

    @staticmethod
    def _strip_string_literals(sql: str) -> str:
        return re.sub(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"", " ", sql)

    @staticmethod
    def _normalize_statement(sql: str) -> str:
        return re.sub(r"\s+", " ", sql.strip())

    @staticmethod
    def _reject_empty(sql: str) -> None:
        if not sql:
            raise SQLSafetyError("Empty SQL is not allowed. Ask a drift analytics question or provide a SELECT query.")

    @staticmethod
    def _reject_multiple_statements(sql: str) -> None:
        statements = [part.strip() for part in sql.split(";") if part.strip()]
        if len(statements) > 1:
            raise SQLSafetyError("Only one SELECT statement may be executed at a time.")

    @staticmethod
    def _reject_non_select(sql: str) -> None:
        first_word = re.match(r"^\s*([A-Za-z_][\w]*)", sql)
        if not first_word or first_word.group(1).upper() != "SELECT":
            raise SQLSafetyError("Only read-only SELECT statements are allowed.")

    def _reject_forbidden_keywords(self, sql: str) -> None:
        scrubbed = self._strip_string_literals(sql)
        for keyword in sorted(FORBIDDEN_KEYWORDS):
            if re.search(rf"\b{keyword}\b", scrubbed, flags=re.IGNORECASE):
                raise SQLSafetyError(f"{keyword} statements are blocked. Use a read-only SELECT query.")

    def _validate_tables_and_columns(self, sql: str) -> None:
        scrubbed = self._strip_string_literals(sql)
        table_aliases = self._extract_table_aliases(scrubbed)
        self._validate_qualified_columns(scrubbed, table_aliases)
        self._validate_unqualified_identifiers(scrubbed, table_aliases)

    def _extract_table_aliases(self, sql: str) -> dict[str, str]:
        aliases: dict[str, str] = {}
        table_pattern = re.compile(
            r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)\s*(?:AS\s+)?([A-Za-z_][\w]*)?",
            flags=re.IGNORECASE,
        )
        for match in table_pattern.finditer(sql):
            table = match.group(1)
            alias = match.group(2)
            if table not in self.allowed_tables:
                raise SQLSafetyError(
                    f"Unknown table '{table}'. Allowed tables: {', '.join(sorted(self.allowed_tables))}."
                )
            aliases[table] = table
            if alias and alias.upper() not in SQL_KEYWORDS:
                aliases[alias] = table
        return aliases

    def _validate_qualified_columns(self, sql: str, aliases: dict[str, str]) -> None:
        for alias, column in re.findall(r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b", sql):
            if alias not in aliases:
                raise SQLSafetyError(f"Unknown table alias '{alias}' in column reference '{alias}.{column}'.")
            table = aliases[alias]
            if column not in self.schema[table]:
                raise SQLSafetyError(f"Unknown column '{column}' on table '{table}'.")

    def _validate_unqualified_identifiers(self, sql: str, aliases: dict[str, str]) -> None:
        allowed_alias_names = set(re.findall(r"\bAS\s+([A-Za-z_][\w]*)\b", sql, flags=re.IGNORECASE))
        function_names = set(
            match.group(1).upper()
            for match in re.finditer(r"\b([A-Za-z_][\w]*)\s*\(", sql)
        )
        qualified_parts = set()
        for alias, column in re.findall(r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b", sql):
            qualified_parts.add(alias)
            qualified_parts.add(column)

        for token in re.findall(r"\b[A-Za-z_][\w]*\b", sql):
            upper = token.upper()
            if token in qualified_parts:
                continue
            if upper in SQL_KEYWORDS or upper in SQL_FUNCTIONS or upper in function_names:
                continue
            if token in self.allowed_tables or token in aliases or token in allowed_alias_names:
                continue
            if token in self.allowed_columns:
                continue
            raise SQLSafetyError(
                f"Unknown identifier '{token}'. Use columns from the approved drift analytics schema."
            )

    def _enforce_limit(self, sql: str) -> tuple[str, str | None]:
        stripped = sql.rstrip(";").strip()
        limit = self._find_top_level_limit(stripped)
        if limit is None:
            return f"{stripped} LIMIT {self.max_rows}", f"Added LIMIT {self.max_rows} to protect the demo database."
        if limit.value is not None and limit.value > self.max_rows and limit.number_start is not None:
            rewritten = f"{stripped[:limit.number_start]}{self.max_rows}{stripped[limit.number_end:]}"
            return rewritten, f"Reduced LIMIT from {limit.value} to {self.max_rows}."
        return stripped, None

    @staticmethod
    def _find_top_level_limit(sql: str) -> _TopLevelLimit | None:
        depth = 0
        in_single = False
        in_double = False
        i = 0
        while i < len(sql):
            char = sql[i]
            if char == "'" and not in_double:
                in_single = not in_single
                i += 1
                continue
            if char == '"' and not in_single:
                in_double = not in_double
                i += 1
                continue
            if in_single or in_double:
                i += 1
                continue
            if char == "(":
                depth += 1
                i += 1
                continue
            if char == ")":
                depth = max(0, depth - 1)
                i += 1
                continue
            if depth == 0 and sql[i : i + 5].upper() == "LIMIT":
                before = sql[i - 1] if i > 0 else " "
                after = sql[i + 5] if i + 5 < len(sql) else " "
                if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                    number_match = re.match(r"\s+(\d+)", sql[i + 5 :])
                    if number_match:
                        number_start = i + 5 + number_match.start(1)
                        number_end = i + 5 + number_match.end(1)
                        return _TopLevelLimit(
                            keyword_start=i,
                            number_start=number_start,
                            number_end=number_end,
                            value=int(number_match.group(1)),
                        )
                    return _TopLevelLimit(keyword_start=i)
            i += 1
        return None
