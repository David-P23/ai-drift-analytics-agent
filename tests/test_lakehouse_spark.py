"""Local Spark checks for the Databricks path (skipped when pyspark is not installed).

1. Gold: build silver from the committed source files, run lakehouse/sql/gold.sql,
   and require the agent-facing view to match the bundled SQLite demo row for row.
2. Dialect: run every agent query template on SQLite and, translated, on Spark SQL
   over the same rows, and require identical results.

Spark here is open-source Spark 4 with ANSI mode on, the same SQL engine family
as a Databricks SQL warehouse. Delta-specific statements are swapped for plain
tables (see _localize), since Delta jars are not available offline.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

pyspark = pytest.importorskip("pyspark")

import pandas as pd  # noqa: E402
from pyspark.sql import SparkSession  # noqa: E402
from pyspark.sql import functions as F  # noqa: E402

from lakehouse.pipeline import contracts, sql_runner  # noqa: E402
from src import analytics  # noqa: E402
from src.database import DriftDatabase  # noqa: E402
from src.sql_dialect import to_databricks  # noqa: E402
from tests.lakehouse_reference import reconstruct  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEMO_ROWS = ROOT / "data" / "northstar_demo_rows.json"
LOCAL_SCHEMAS = {"catalog": "local", "bronze": "bronze", "ref": "ref", "silver": "silver", "gold": "gold"}


@pytest.fixture(scope="module")
def spark(tmp_path_factory) -> SparkSession:
    warehouse = tmp_path_factory.mktemp("spark-warehouse")
    # Spark workers must use this interpreter, not whatever python3 is on PATH.
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    session = (
        SparkSession.builder.master("local[2]")
        .appName("lakehouse-tests")
        .config("spark.sql.warehouse.dir", str(warehouse))
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.sql.ansi.enabled", "true")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


def _load_silver(spark: SparkSession) -> None:
    rebuilt = reconstruct()
    spark.sql("CREATE DATABASE IF NOT EXISTS silver")
    spark.sql("CREATE DATABASE IF NOT EXISTS gold")
    for table in ("applications", "drift_instances", "drift_notes"):
        frame: pd.DataFrame = rebuilt[table].copy()
        contract = contracts.SILVER_TABLES[table]
        if "extracted_at" not in frame:
            frame["extracted_at"] = None
        staged = spark.createDataFrame(
            _as_strings(frame[[name for name, _ in contract]]),
            schema=", ".join(f"{name} STRING" for name, _ in contract),
        )
        typed = staged.select(*[F.col(name).cast(dtype).alias(name) for name, dtype in contract])
        typed.write.mode("overwrite").saveAsTable(f"silver.{table}")


def _as_strings(frame: pd.DataFrame) -> list[list[str | None]]:
    """Stage everything as STRING and let Spark cast, the way bronze -> silver does."""

    return [[None if pd.isna(v) else str(v) for v in row] for row in frame.itertuples(index=False)]


def _localize(statement: str) -> list[str]:
    """CREATE OR REPLACE TABLE needs Delta; locally use drop + create a plain table."""

    statement = "\n".join(line for line in statement.splitlines() if not line.strip().startswith("--"))
    match = re.match(r"\s*CREATE OR REPLACE TABLE\s+(\S+)", statement, flags=re.IGNORECASE)
    if not match:
        return [statement]
    table = match.group(1)
    body = statement[match.end():]
    return [f"DROP TABLE IF EXISTS {table}", f"CREATE TABLE {table} USING parquet{body}"]


def _demo_frame() -> pd.DataFrame:
    return pd.DataFrame(json.loads(DEMO_ROWS.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def gold(spark: SparkSession) -> SparkSession:
    _load_silver(spark)
    for statement in sql_runner.load("gold", LOCAL_SCHEMAS):
        for local_statement in _localize(statement):
            spark.sql(local_statement)
    return spark


def test_gold_view_matches_the_bundled_sqlite_demo(gold: SparkSession) -> None:
    spark = gold

    gold = spark.table("gold.applications").toPandas()
    assert list(gold.columns) == contracts.AGENT_COLUMNS
    assert len(gold) == contracts.EXPECTED_ROWS["gold.drift_findings"]

    compare = [c for c in contracts.AGENT_COLUMNS if c != "last_scanned_at"]
    got = gold[compare].copy()
    for column in ("drift_detected_on", "latest_note_date"):
        got[column] = got[column].map(lambda v: v.isoformat() if v is not None and not pd.isna(v) else None)
    want = _demo_frame()[compare]

    def normalized(frame: pd.DataFrame) -> pd.DataFrame:
        frame = frame.astype(object)
        frame = frame.where(frame.notna(), None)
        return frame.sort_values("finding_id").reset_index(drop=True)

    pd.testing.assert_frame_equal(normalized(got), normalized(want), check_dtype=False)


def test_gold_kpis_are_consistent(gold: SparkSession) -> None:
    spark = gold
    lob = spark.table("gold.kpi_drift_by_lob").toPandas()
    assert lob.in_scope_apps.sum() == 750
    by_source = spark.table("gold.kpi_by_source_system").toPandas()
    assert by_source.findings.sum() == 475
    aging = spark.table("gold.kpi_aging_buckets").toPandas()
    open_in_scope = spark.sql(
        "SELECT COUNT(*) FROM gold.drift_findings WHERE status = 'Open' AND in_scope = 'Y'"
        " AND detected_version <> approved_version"
    ).collect()[0][0]
    assert aging.open_findings.sum() == open_in_scope


@pytest.fixture(scope="module")
def parity_table(spark: SparkSession, tmp_path_factory) -> Path:
    """The same 475 demo rows in SQLite and in a Spark table typed like gold."""

    db_path = tmp_path_factory.mktemp("sqlite") / "northstar_demo.sqlite"
    from src.database import replace_applications_rows

    rows = json.loads(DEMO_ROWS.read_text(encoding="utf-8"))
    replace_applications_rows(db_path, rows)

    staged = spark.createDataFrame(
        _as_strings(_demo_frame()[contracts.AGENT_COLUMNS]), schema=", ".join(f"{c} STRING" for c in contracts.AGENT_COLUMNS)
    )
    typed = staged.select(
        *[
            F.col(c).cast("INT").alias(c)
            if c in {"rto_score", "note_count"}
            else F.to_date(c).alias(c)
            if c in {"drift_detected_on", "latest_note_date"}
            else F.to_timestamp(c).alias(c)
            if c == "last_scanned_at"
            else F.col(c)
            for c in contracts.AGENT_COLUMNS
        ]
    )
    spark.sql("CREATE DATABASE IF NOT EXISTS parity")
    typed.write.mode("overwrite").saveAsTable("parity.applications")
    return db_path


def _plans() -> list:
    plans = list(analytics.all_dashboard_plans())
    plans += [
        analytics.top_drifting_apps(),
        analytics.top_drifting_apps(data_center="Ashburn"),
        analytics.critical_apps_with_open_drift(),
        analytics.critical_apps_with_open_drift(include_high=True, product="Kafka"),
        analytics.exemption_analysis(),
        analytics.executive_escalation_candidates(),
        analytics.drift_cluster_source(),
    ]
    return plans


def test_every_agent_query_returns_the_same_rows_on_spark(spark: SparkSession, parity_table: Path) -> None:
    sqlite_db = DriftDatabase(parity_table, max_rows=1000)
    spark.sql("USE parity")
    for plan in _plans():
        safe, sqlite_rows, columns = sqlite_db.execute_select(plan.sql)
        spark_rows = [row.asDict() for row in spark.sql(to_databricks(safe.sql)).collect()]

        def portable(rows: list[dict]) -> list[dict]:
            out = []
            for row in rows:
                out.append(
                    {
                        k: (v.isoformat() if hasattr(v, "isoformat") else float(v) if isinstance(v, float) else v)
                        for k, v in row.items()
                    }
                )
            return out

        assert list(spark_rows[0].keys()) == columns, plan.intent
        assert portable(spark_rows) == portable(sqlite_rows), plan.intent


class _SparkCursor:
    """Just enough of the DB-API cursor the Databricks connector exposes, backed by local Spark."""

    def __init__(self, spark: SparkSession) -> None:
        self.spark = spark
        self.description = None
        self._rows: list[tuple] = []

    def execute(self, sql: str) -> None:
        frame = self.spark.sql(sql)
        self.description = [(field.name, str(field.dataType), None, None, None, None, None) for field in frame.schema]
        self._rows = [tuple(row) for row in frame.collect()]

    def fetchall(self) -> list[tuple]:
        return self._rows

    def close(self) -> None:
        pass


class _SparkConnection:
    def __init__(self, spark: SparkSession) -> None:
        self.spark = spark

    def cursor(self) -> _SparkCursor:
        return _SparkCursor(self.spark)

    def close(self) -> None:
        pass


def test_agent_answers_are_identical_on_both_backends(spark: SparkSession, parity_table: Path) -> None:
    from src.agent import answer_question, build_executive_summary
    from src.prompting import SUGGESTED_QUESTIONS
    from src.warehouse import DatabricksDriftDatabase, DatabricksSettings

    spark.sql("USE parity")
    sqlite_db = DriftDatabase(parity_table, max_rows=1000)
    warehouse_db = DatabricksDriftDatabase(
        DatabricksSettings("local", "/local", "unused"),
        max_rows=1000,
        connect=lambda _settings: _SparkConnection(spark),
    )

    local_summary = build_executive_summary(sqlite_db)
    warehouse_summary = build_executive_summary(warehouse_db)
    assert warehouse_summary.narrative == local_summary.narrative
    assert [m.value for m in warehouse_summary.metrics] == [m.value for m in local_summary.metrics]

    for question in SUGGESTED_QUESTIONS:
        local = answer_question(sqlite_db, question)
        remote = answer_question(warehouse_db, question)
        assert remote.error is None, question
        assert remote.answer == local.answer, question
        assert remote.rows == local.rows, question
