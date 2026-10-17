# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Bronze: land the source exports and load them as-is
# MAGIC
# MAGIC Three "acquired companies" each send their own exports with their own column names,
# MAGIC ID formats, codes and date formats:
# MAGIC
# MAGIC | Source | Owns | Looks like |
# MAGIC |---|---|---|
# MAGIC | `northstar_core` | Customer Platforms, Payments | The original snake_case schema, ISO dates, `Y`/`N` |
# MAGIC | `harbor_trust` | Risk & Compliance, Treasury & Finance | PascalCase, `HT-#####` IDs, `MM/DD/YYYY`, site codes like `DAL`, RTO in hours, vendor product names; sends drift twice (March 6 and April 3 exports) |
# MAGIC | `summit_capital` | Technology Resiliency, Internal Services | Ticketing export: numeric asset keys, UTC timestamps, `true`/`false`, RTO in minutes, product codes like `rhel`, one combined exemption field |
# MAGIC
# MAGIC Bronze keeps every column as `STRING`, exactly as delivered, and adds three columns:
# MAGIC `_source_system`, `_source_file`, `_ingested_at`. No cleaning happens here, so silver can
# MAGIC always be rebuilt from bronze.
# MAGIC
# MAGIC `COPY INTO` is idempotent: rerunning skips files it has already loaded, so a scheduled
# MAGIC job only picks up new drops.

# COMMAND ----------

# MAGIC %run ./_bootstrap

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Land the files
# MAGIC Simulates each source system dropping its export into the landing volume.
# MAGIC Files already in the volume are left alone.

# COMMAND ----------

import glob
import shutil

landed = 0
for source in config.SOURCE_SYSTEMS:
    for entity in config.ENTITIES:
        target_dir = f"{SCHEMAS.landing_root}/{source}/{entity}"
        os.makedirs(target_dir, exist_ok=True)
        for path in sorted(glob.glob(os.path.join(SOURCE_DATA_DIR, source, entity, "*.csv"))):
            target = os.path.join(target_dir, os.path.basename(path))
            if not os.path.exists(target):
                shutil.copyfile(path, target)
                landed += 1
print(f"Landed {landed} new file(s) under {SCHEMAS.landing_root}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Load each source and entity into its own bronze table

# COMMAND ----------

results = []
for source in config.SOURCE_SYSTEMS:
    for entity in config.ENTITIES:
        table = f"{SCHEMAS.bronze}.{config.bronze_table(source, entity)}"
        folder = f"{SCHEMAS.landing_root}/{source}/{entity}/"
        # Empty placeholder table; COPY INTO infers the columns on first load (mergeSchema).
        spark.sql(f"CREATE TABLE IF NOT EXISTS {table}")
        outcome = spark.sql(
            f"""
            COPY INTO {table}
            FROM (
                SELECT
                    *,
                    '{source}' AS _source_system,
                    _metadata.file_name AS _source_file,
                    current_timestamp() AS _ingested_at
                FROM '{folder}'
            )
            FILEFORMAT = CSV
            FORMAT_OPTIONS ('header' = 'true', 'inferSchema' = 'false', 'mergeSchema' = 'true')
            COPY_OPTIONS ('mergeSchema' = 'true')
            """
        ).collect()[0].asDict()
        results.append({"table": table, **outcome})

display(spark.createDataFrame(results))  # noqa: F821 - display exists in notebooks

# COMMAND ----------

summary = []
for source in config.SOURCE_SYSTEMS:
    for entity in config.ENTITIES:
        table = f"{SCHEMAS.bronze}.{config.bronze_table(source, entity)}"
        summary.append(
            spark.sql(
                f"SELECT '{table}' AS table_name, COUNT(*) AS rows, COUNT(DISTINCT _source_file) AS files FROM {table}"
            )
        )
from functools import reduce  # noqa: E402

display(reduce(lambda a, b: a.unionByName(b), summary))  # noqa: F821
