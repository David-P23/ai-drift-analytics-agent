# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Gold: findings, the agent's view, and KPI tables
# MAGIC
# MAGIC Runs `lakehouse/sql/gold.sql` (the same file the local Spark tests run in CI).
# MAGIC Everything here is deterministic SQL, so the numbers the agent reports never come
# MAGIC from an LLM doing arithmetic.
# MAGIC
# MAGIC | Object | What it is |
# MAGIC |---|---|
# MAGIC | `gold.drift_findings` | One row per drift finding with its application and latest note (the old `drift_full`) |
# MAGIC | `gold.applications` | View with the 23 columns the agent was built against, so the agent code did not change |
# MAGIC | `gold.kpi_drift_by_lob` | Share of in-scope applications with open drift, by line of business |
# MAGIC | `gold.kpi_aging_buckets` | Open drift by 60 / 90 / 120-day threshold, as of the extract (scan) date |
# MAGIC | `gold.kpi_by_source_system` | The same KPIs per acquired company |

# COMMAND ----------

# MAGIC %run ./_bootstrap

# COMMAND ----------

for statement in sql_runner.load("gold", SCHEMAS.as_template_vars()):
    first_line = next(line for line in statement.splitlines() if line.strip() and not line.strip().startswith("--"))
    print(first_line.strip())
    spark.sql(statement)

# COMMAND ----------

display(spark.table(f"{SCHEMAS.gold}.kpi_by_source_system"))  # noqa: F821

# COMMAND ----------

display(spark.table(f"{SCHEMAS.gold}.kpi_drift_by_lob").orderBy("open_drift_pct", ascending=False))  # noqa: F821
