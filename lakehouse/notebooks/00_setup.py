# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Setup: catalog, schemas, landing volume, master data, grants
# MAGIC
# MAGIC Safe to rerun. Creates (if missing):
# MAGIC
# MAGIC | Object | Purpose |
# MAGIC |---|---|
# MAGIC | `<catalog>` | One Unity Catalog catalog for the whole project (default `drift_demo`) |
# MAGIC | `<catalog>.bronze` | Raw rows exactly as each source delivered them |
# MAGIC | `<catalog>.bronze.landing` | Volume where the three source systems "drop" their CSV exports |
# MAGIC | `<catalog>.ref` | Master data: ID crosswalks, code maps, product aliases, RTO scale |
# MAGIC | `<catalog>.silver` | Cleaned, conformed rows on shared IDs |
# MAGIC | `<catalog>.gold` | Analytics tables, KPI tables, and the agent-facing view |
# MAGIC
# MAGIC **Parameters**
# MAGIC - `catalog`: if `CREATE CATALOG` is not allowed in your workspace, set this to `workspace`
# MAGIC   (the catalog every Free Edition workspace already has) and use the same value everywhere.
# MAGIC - `agent_principal`: optional user, group, or service principal the agent connects as.
# MAGIC   It gets read-only access to `gold` and nothing else.
# MAGIC - `reset`: `true` drops all four schemas first (full rebuild from the CSVs).

# COMMAND ----------

# MAGIC %run ./_bootstrap

# COMMAND ----------

dbutils.widgets.text("agent_principal", "", "Agent principal (optional)")
dbutils.widgets.dropdown("reset", "false", ["false", "true"], "Drop and rebuild")
AGENT_PRINCIPAL = dbutils.widgets.get("agent_principal").strip()
RESET = dbutils.widgets.get("reset") == "true"

# COMMAND ----------

try:
    spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG} COMMENT 'Application drift lakehouse (portfolio project)'")
except Exception as exc:  # Free Edition or restricted metastores may not allow this
    raise RuntimeError(
        f"Could not create catalog '{CATALOG}': {exc}\n"
        "Set the 'catalog' parameter to 'workspace' (it already exists) and rerun."
    ) from exc

if RESET:
    for schema in ("gold", "silver", "ref", "bronze"):
        spark.sql(f"DROP SCHEMA IF EXISTS {CATALOG}.{schema} CASCADE")
    print("Dropped bronze, ref, silver and gold.")

schema_comments = {
    "bronze": "Raw source-system rows, all STRING, plus load metadata",
    "ref": "Master data shared by every source: crosswalks, code maps, product aliases, RTO scale",
    "silver": "Cleaned, conformed, deduplicated rows on shared IDs, plus a quarantine table",
    "gold": "Analytics tables, KPIs, and the agent-facing view",
}
for schema, comment in schema_comments.items():
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{schema} COMMENT '{comment}'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.bronze.{config.LANDING_VOLUME} COMMENT 'Landing zone for source exports'")
print(f"Schemas ready in {CATALOG}; landing volume at {SCHEMAS.landing_root}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Master data
# MAGIC The crosswalks are how three companies' IDs become one shared ID, and the code map is
# MAGIC how `OPEN`, `true` and `Open` all become `Open`. In a real company data stewards own
# MAGIC these tables; here they ship with the repo as CSVs.

# COMMAND ----------

import pandas as pd
from pyspark.sql import functions as F


def parse_schema(schema: str) -> list[tuple[str, str]]:
    return [tuple(part.strip().split(" ", 1)) for part in schema.split(",")]


for table, schema in config.REFERENCE_TABLES.items():
    path = os.path.join(SOURCE_DATA_DIR, "reference", f"{table}.csv")
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    columns = parse_schema(schema)
    rows = [[value if value != "" else None for value in row] for row in frame[[c for c, _ in columns]].itertuples(index=False)]
    staged = spark.createDataFrame(rows, ", ".join(f"{name} STRING" for name, _ in columns))
    typed = staged.select(*[F.col(name).cast(dtype).alias(name) for name, dtype in columns])
    typed.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{SCHEMAS.ref}.{table}")
    print(f"{SCHEMAS.ref}.{table:<16} {typed.count():>5} rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Read-only access for the agent
# MAGIC Replaces the SQLite `PRAGMA query_only` safeguard with platform permissions. The agent's
# MAGIC principal also needs **Can use** on the SQL warehouse (SQL Warehouses → your warehouse →
# MAGIC Permissions), which is granted in the UI, not here.

# COMMAND ----------

if AGENT_PRINCIPAL:
    for statement in (
        f"GRANT USE CATALOG ON CATALOG {CATALOG} TO `{AGENT_PRINCIPAL}`",
        f"GRANT USE SCHEMA ON SCHEMA {SCHEMAS.gold} TO `{AGENT_PRINCIPAL}`",
        f"GRANT SELECT ON SCHEMA {SCHEMAS.gold} TO `{AGENT_PRINCIPAL}`",
    ):
        spark.sql(statement)
        print(statement)
else:
    print("No agent_principal given; skipping grants. The agent will connect as you.")
