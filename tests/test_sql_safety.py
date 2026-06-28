from __future__ import annotations

import pytest

from src.domain import schema_map
from src.sql_safety import SQLSafetyError, SQLSafetyValidator


@pytest.fixture()
def validator() -> SQLSafetyValidator:
    return SQLSafetyValidator(schema_map(), max_rows=100)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO applications (app_id) VALUES ('x')",
        "UPDATE applications SET status = 'Closed'",
        "DELETE FROM applications",
        "DROP TABLE applications",
        "ALTER TABLE applications ADD COLUMN bad TEXT",
        "CREATE TABLE bad (id TEXT)",
        "PRAGMA table_info(applications)",
        "ATTACH DATABASE 'x.db' AS x",
    ],
)
def test_blocks_write_and_ddl_keywords(validator: SQLSafetyValidator, sql: str) -> None:
    with pytest.raises(SQLSafetyError):
        validator.validate(sql)


def test_only_select_statements_are_allowed(validator: SQLSafetyValidator) -> None:
    with pytest.raises(SQLSafetyError, match="Only read-only SELECT"):
        validator.validate("WITH drift AS (SELECT * FROM applications) SELECT * FROM drift")


def test_adds_limit_when_missing(validator: SQLSafetyValidator) -> None:
    safe = validator.validate("SELECT app_id, app_name FROM applications")
    assert safe.sql.endswith("LIMIT 100")
    assert "Added LIMIT 100" in safe.warnings[0]


def test_reduces_large_limit(validator: SQLSafetyValidator) -> None:
    safe = validator.validate("SELECT app_id FROM applications LIMIT 999")
    assert safe.sql.endswith("LIMIT 100")
    assert "Reduced LIMIT from 999 to 100." in safe.warnings


def test_validates_unknown_table(validator: SQLSafetyValidator) -> None:
    with pytest.raises(SQLSafetyError, match="Unknown table"):
        validator.validate("SELECT app_id FROM inventory")


def test_validates_unknown_column(validator: SQLSafetyValidator) -> None:
    with pytest.raises(SQLSafetyError, match="Unknown identifier"):
        validator.validate("SELECT imaginary_column FROM applications")


def test_allows_qualified_known_columns(validator: SQLSafetyValidator) -> None:
    safe = validator.validate(
        """
        SELECT a.app_id AS app_id, a.app_name AS app_name
        FROM applications AS a
        WHERE a.status = 'Open'
        ORDER BY a.app_name ASC
        """
    )
    assert "applications AS a" in safe.sql
    assert safe.sql.endswith("LIMIT 100")
