"""Databricks SQL warehouse backend for the drift analytics agent.

``DatabricksDriftDatabase`` has the same interface as ``src.database.DriftDatabase``
(``validate_sql``, ``execute_select``, ``scalar``), so the agent, the dashboard and
the cluster detector run unchanged on either backend. Select it with
``DRIFT_BACKEND=databricks``.

Every query takes the same path it takes on SQLite, plus one step:

1. ``SQLSafetyValidator`` checks the SQLite-dialect template (single read-only
   SELECT, known table and columns, LIMIT enforced).
2. ``src.sql_dialect.to_databricks`` translates it to Databricks SQL.
3. The query runs on a SQL warehouse against ``<catalog>.gold.applications``.

Read-only is enforced twice: by the validator in the app, and by Unity Catalog
grants on the warehouse side (the agent's principal should hold only USE
CATALOG, USE SCHEMA and SELECT on the gold schema).
"""

from __future__ import annotations

import datetime as dt
import decimal
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from src.domain import schema_map
from src.models import SafeQuery
from src.sql_dialect import to_databricks
from src.sql_safety import SQLSafetyError, SQLSafetyValidator

REQUIRED_ENV = ("DATABRICKS_SERVER_HOSTNAME", "DATABRICKS_HTTP_PATH")
OAUTH = "databricks-oauth"


@dataclass(frozen=True)
class DatabricksSettings:
    server_hostname: str
    http_path: str
    access_token: str | None
    catalog: str = "drift_demo"
    schema: str = "gold"
    auth_type: str = "pat"  # "pat" (personal access token) or "databricks-oauth" (browser sign-in)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DatabricksSettings:
        env = os.environ if env is None else env
        auth_type = env.get("DATABRICKS_AUTH_TYPE", "pat").strip().lower() or "pat"
        if auth_type not in {"pat", OAUTH}:
            raise ValueError(f"DATABRICKS_AUTH_TYPE must be 'pat' or '{OAUTH}', not '{auth_type}'.")
        required = REQUIRED_ENV + (("DATABRICKS_TOKEN",) if auth_type == "pat" else ())
        missing = [name for name in required if not env.get(name, "").strip()]
        if missing:
            raise ValueError(f"DRIFT_BACKEND=databricks needs these environment variables: {', '.join(missing)}.")
        hostname = env["DATABRICKS_SERVER_HOSTNAME"].strip()
        hostname = hostname.removeprefix("https://").removeprefix("http://").rstrip("/")
        return cls(
            server_hostname=hostname,
            http_path=env["DATABRICKS_HTTP_PATH"].strip(),
            access_token=env.get("DATABRICKS_TOKEN", "").strip() or None,
            catalog=env.get("DATABRICKS_CATALOG", "drift_demo").strip() or "drift_demo",
            schema=env.get("DATABRICKS_SCHEMA", "gold").strip() or "gold",
            auth_type=auth_type,
        )

    @property
    def location(self) -> str:
        return f"{self.catalog}.{self.schema}"


Connector = Callable[[DatabricksSettings], Any]


def connect_with_databricks_sql(settings: DatabricksSettings) -> Any:
    """Open a connection with the official databricks-sql-connector."""

    try:
        from databricks import sql as databricks_sql
    except ImportError as exc:  # pragma: no cover - exercised only without the optional dependency
        raise RuntimeError(
            "DRIFT_BACKEND=databricks requires the connector: pip install -r requirements-databricks.txt"
        ) from exc

    credentials: dict[str, Any] = (
        {"auth_type": OAUTH} if settings.auth_type == OAUTH else {"access_token": settings.access_token}
    )
    return databricks_sql.connect(
        server_hostname=settings.server_hostname,
        http_path=settings.http_path,
        catalog=settings.catalog,
        schema=settings.schema,
        user_agent_entry="ai-drift-analytics-agent",
        **credentials,
    )


class DatabricksDriftDatabase:
    """Read-only query access to the gold layer through a Databricks SQL warehouse."""

    backend_name = "databricks"

    def __init__(
        self,
        settings: DatabricksSettings,
        *,
        max_rows: int = 100,
        connect: Connector = connect_with_databricks_sql,
    ) -> None:
        self.settings = settings
        self.max_rows = max_rows
        self.validator = SQLSafetyValidator(schema_map(), max_rows=max_rows)
        self._connect = connect
        self._connection: Any = None
        self._lock = threading.Lock()

    def validate_sql(self, sql: str) -> SafeQuery:
        return self.validator.validate(sql)

    def execute_select(self, sql: str) -> tuple[SafeQuery, list[dict[str, Any]], list[str]]:
        """Validate, translate, and run a SELECT on the warehouse."""

        validated = self.validate_sql(sql)
        warehouse_sql = to_databricks(validated.sql)
        try:
            columns, raw_rows = self._run(warehouse_sql)
        except Exception as exc:
            raise SQLSafetyError(f"Databricks rejected the query: {exc}") from exc

        rows = [{column: _to_portable(value) for column, value in zip(columns, raw)} for raw in raw_rows]
        # Show the SQL that actually ran as the answer's evidence.
        executed = SafeQuery(original_sql=validated.original_sql, sql=warehouse_sql, warnings=validated.warnings)
        return executed, rows, columns

    def scalar(self, sql: str) -> Any:
        _, rows, columns = self.execute_select(sql)
        if not rows or not columns:
            return None
        return rows[0][columns[0]]

    def close(self) -> None:
        with self._lock:
            self._reset_connection()

    def _run(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        with self._lock:
            if self._connection is None:
                self._connection = self._connect(self.settings)
            try:
                cursor = self._connection.cursor()
                try:
                    cursor.execute(sql)
                    raw_rows = [tuple(row) for row in cursor.fetchall()]
                    columns = [description[0] for description in cursor.description or []]
                finally:
                    cursor.close()
            except Exception:
                # Drop a possibly broken session so the next question reconnects.
                self._reset_connection()
                raise
        return columns, raw_rows

    def _reset_connection(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:
                pass
        self._connection = None


def _to_portable(value: Any) -> Any:
    """Return values in the same Python types the SQLite backend produces."""

    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() and value.as_tuple().exponent >= 0 else float(value)
    return value
