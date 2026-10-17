"""Render the .sql files under lakehouse/sql and split them into statements.

The SQL files use ${bronze}, ${ref}, ${silver} and ${gold} placeholders for the
fully qualified schema names, so the same file runs in any catalog (and in the
local Spark tests, where the "catalog" is just a database prefix).
"""

from __future__ import annotations

from pathlib import Path
from string import Template

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"


def render(sql_text: str, variables: dict[str, str]) -> str:
    """Substitute ${name} placeholders; a missing variable raises KeyError."""

    return Template(sql_text).substitute(variables)


def split_statements(sql_text: str) -> list[str]:
    """Split on semicolons that end a line, ignoring blank and comment-only chunks.

    Kept deliberately simple: statements in lakehouse/sql end with ';' at the end
    of a line, and no string literal contains ';' followed by a newline.
    """

    statements: list[str] = []
    current: list[str] = []
    for line in sql_text.splitlines():
        current.append(line)
        if line.rstrip().endswith(";"):
            statement = "\n".join(current).strip().rstrip(";").strip()
            if _has_code(statement):
                statements.append(statement)
            current = []
    tail = "\n".join(current).strip()
    if _has_code(tail):
        statements.append(tail)
    return statements


def load(name: str, variables: dict[str, str]) -> list[str]:
    """Read lakehouse/sql/<name>.sql, render it, and return its statements."""

    return split_statements(render((SQL_DIR / f"{name}.sql").read_text(encoding="utf-8"), variables))


def _has_code(chunk: str) -> bool:
    return any(line.strip() and not line.strip().startswith("--") for line in chunk.splitlines())
