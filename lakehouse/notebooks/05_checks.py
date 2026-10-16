# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Checks: is every layer right?
# MAGIC
# MAGIC Runs last in the job and fails the run if anything is off.
# MAGIC
# MAGIC 1. **Silver contract**: the tables exist with the exact columns and types in `contracts.py`.
# MAGIC 2. **Row accounting**: every bronze row lands in silver, in quarantine, or is a known
# MAGIC    superseded/duplicate copy. Nothing disappears silently.
# MAGIC 3. **Keys**: canonical IDs are unique and never NULL.
# MAGIC 4. **Truth test**: `gold.applications` matches the bundled SQLite demo
# MAGIC    (`data/northstar_demo_rows.json`) row for row. The source data was generated from the
# MAGIC    same portfolio, so any difference is a silver or gold bug.
# MAGIC 5. **Themes** (only once notebook 04 exists): every note has exactly one allowed theme.

# COMMAND ----------

# MAGIC %run ./_bootstrap

# COMMAND ----------

import json

import pandas as pd
from pyspark.sql import functions as F

results: list[dict] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    results.append({"check": name, "passed": bool(passed), "detail": detail})


def table_exists(name: str) -> bool:
    return spark.catalog.tableExists(name)


# COMMAND ----------

# 1. Silver contract
for table, columns in contracts.SILVER_TABLES.items():
    full_name = f"{SCHEMAS.silver}.{table}"
    if not table_exists(full_name):
        check(f"{full_name} exists", False, "missing: run 02_silver")
        continue
    actual = [(f.name, f.dataType.simpleString().upper()) for f in spark.table(full_name).schema]
    expected = [(name, dtype.upper()) for name, dtype in columns]
    check(f"{full_name} matches contract", actual == expected, "" if actual == expected else f"got {actual}")

# COMMAND ----------

# 2. Row accounting: bronze = silver + quarantined records + superseded copies
silver_ready = all(table_exists(f"{SCHEMAS.silver}.{t}") for t in contracts.SILVER_TABLES)
if silver_ready:
    quarantine = spark.table(f"{SCHEMAS.silver}.quarantine")
    for entity in config.ENTITIES:
        bronze_rows = sum(
            spark.table(f"{SCHEMAS.bronze}.{config.bronze_table(s, entity)}").count() for s in config.SOURCE_SYSTEMS
        )
        silver_rows = spark.table(f"{SCHEMAS.silver}.{entity}").count()
        quarantined = (
            quarantine.where(F.col("entity") == entity)
            .select("source_system", "source_record_id")
            .distinct()
            .count()
        )
        expected_silver = contracts.EXPECTED_ROWS[f"silver.{entity}"]
        expected_quarantine = contracts.EXPECTED_QUARANTINE_BY_ENTITY.get(entity, 0)
        superseded = bronze_rows - silver_rows - quarantined
        check(f"silver.{entity} row count", silver_rows == expected_silver, f"{silver_rows} (expected {expected_silver})")
        check(
            f"{entity} quarantined records",
            quarantined == expected_quarantine,
            f"{quarantined} (expected {expected_quarantine})",
        )
        check(
            f"{entity} bronze rows all accounted for",
            superseded == contracts.EXPECTED_SUPERSEDED[entity],
            f"bronze {bronze_rows} = silver {silver_rows} + quarantined {quarantined} + superseded {superseded}"
            f" (expected superseded {contracts.EXPECTED_SUPERSEDED[entity]})",
        )

# COMMAND ----------

# 3. Keys
if silver_ready:
    for entity, keys in (
        ("applications", ["app_id"]),
        ("drift_instances", ["drift_id"]),
        ("drift_notes", ["source_system", "note_id"]),
    ):
        frame = spark.table(f"{SCHEMAS.silver}.{entity}")
        duplicates = frame.groupBy(*keys).count().where("count > 1").count()
        nulls = frame.where(" OR ".join(f"{k} IS NULL" for k in keys)).count()
        check(f"silver.{entity} keys unique and not null", duplicates == 0 and nulls == 0, f"dupes {duplicates}, nulls {nulls}")
    orphans = (
        spark.table(f"{SCHEMAS.silver}.drift_instances")
        .join(spark.table(f"{SCHEMAS.silver}.applications"), "app_id", "left_anti")
        .count()
    )
    check("every drift instance has an application", orphans == 0, f"{orphans} orphans")

# COMMAND ----------

# 4. Truth test against the bundled SQLite demo
agent_view = f"{SCHEMAS.gold}.applications"
if table_exists(agent_view):
    gold = spark.table(agent_view)
    check("gold.applications has the agent's 23 columns", gold.columns == contracts.AGENT_COLUMNS, str(gold.columns))
    check(
        "gold.drift_findings row count",
        gold.count() == contracts.EXPECTED_ROWS["gold.drift_findings"],
        f"{gold.count()} (expected {contracts.EXPECTED_ROWS['gold.drift_findings']})",
    )

    with open(os.path.join(REPO_ROOT, "data", "northstar_demo_rows.json"), encoding="utf-8") as handle:
        demo = pd.DataFrame(json.load(handle))  # plain JSON load: no date guessing
    # last_scanned_at is compared as a calendar date: aging is measured to it, but the
    # timestamp's text format differs between SQLite and Spark.
    demo["last_scanned_at"] = demo["last_scanned_at"].str[:10]
    compare = contracts.AGENT_COLUMNS
    rows = [[None if pd.isna(v) else str(v) for v in row] for row in demo[compare].itertuples(index=False)]
    expected = spark.createDataFrame(rows, ", ".join(f"{c} STRING" for c in compare))
    actual = gold.select(
        *[
            F.to_date(c).cast("string").alias(c) if c == "last_scanned_at" else F.col(c).cast("string").alias(c)
            for c in compare
        ]
    )
    missing = expected.exceptAll(actual)
    unexpected = actual.exceptAll(expected)
    n_missing, n_unexpected = missing.count(), unexpected.count()
    check(
        "gold matches the SQLite demo row for row",
        n_missing == 0 and n_unexpected == 0,
        f"{n_missing} demo rows missing from gold, {n_unexpected} gold rows not in the demo",
    )
    if n_missing or n_unexpected:
        print("Rows in the demo but not in gold (first 10):")
        display(missing.limit(10))  # noqa: F821
        print("Rows in gold but not in the demo (first 10):")
        display(unexpected.limit(10))  # noqa: F821
    aging_table = f"{SCHEMAS.gold}.kpi_aging_buckets"
    aging = {r.aging_bucket: r.open_findings for r in spark.table(aging_table).collect()}
    as_of = {str(r.as_of_date) for r in spark.table(aging_table).select("as_of_date").distinct().collect()}
    check(
        f"aging buckets as of {contracts.AS_OF_DATE}",
        aging == contracts.EXPECTED_AGING and as_of == {contracts.AS_OF_DATE},
        f"as of {sorted(as_of)}: {aging} (expected {contracts.EXPECTED_AGING})",
    )
else:
    check(f"{agent_view} exists", False, "missing: run 03_gold")

# COMMAND ----------

# 5. Themes (optional until notebook 04 is written)
themes_table = f"{SCHEMAS.gold}.note_themes"
if table_exists(themes_table):
    themes = spark.table(themes_table)
    per_note = themes.groupBy("source_system", "note_id").count()
    check("every note tagged exactly once", per_note.where("count <> 1").count() == 0 and per_note.count() == 804)
else:
    print(f"{themes_table} not built yet; skipping theme checks.")

# COMMAND ----------

report = spark.createDataFrame(results)
display(report)  # noqa: F821
failed = [r for r in results if not r["passed"]]
if failed:
    raise AssertionError(f"{len(failed)} check(s) failed: " + "; ".join(f"{r['check']} ({r['detail']})" for r in failed))
print(f"All {len(results)} checks passed.")
