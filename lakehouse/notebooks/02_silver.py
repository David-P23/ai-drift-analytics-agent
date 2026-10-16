# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Silver: conform three source systems onto shared definitions  ✍️ *(David writes this one)*
# MAGIC
# MAGIC This is the Winnebago problem in miniature: the same things (an application, a drift
# MAGIC finding, a data center, a status) arrive with different IDs, names, codes and formats
# MAGIC from three acquired companies. Silver maps all of them onto one definition.
# MAGIC
# MAGIC **You are done when `05_checks` passes.** It compares your silver tables with the
# MAGIC contract in `lakehouse/pipeline/contracts.py` and checks that gold reproduces the
# MAGIC bundled SQLite demo row for row.
# MAGIC
# MAGIC ### What silver must produce
# MAGIC | Table | Rows | Key | Notes |
# MAGIC |---|---|---|---|
# MAGIC | `silver.applications` | 1,000 | `app_id` | canonical ID from `ref.app_crosswalk` |
# MAGIC | `silver.drift_instances` | 475 | `drift_id` | canonical ID from `ref.drift_crosswalk`; latest extract wins |
# MAGIC | `silver.drift_notes` | 804 | `source_system`, `note_id` | exact duplicates removed |
# MAGIC | `silver.quarantine` | 4 records | | rows that failed a rule, kept as JSON with the reason (one row per record and reason) |
# MAGIC
# MAGIC Column names and types: `contracts.SILVER_*` (printed in the next cell).
# MAGIC
# MAGIC ### Rules
# MAGIC 1. **IDs:** every source ID goes through its crosswalk. A drift row whose application is
# MAGIC    unknown is quarantined (reason `unknown application`), never dropped silently.
# MAGIC 2. **Codes:** statuses, yes/no flags, data centers, note types, escalation flags and Summit's
# MAGIC    product categories go through `ref.code_map` (`source_system`, `domain`, `source_value`).
# MAGIC    Products go through `ref.product_alias`.
# MAGIC 3. **RTO:** Harbor sends hours, Summit sends minutes. Convert to hours and look up the score
# MAGIC    in `ref.rto_scale`. An application whose score is not 1-10 is quarantined.
# MAGIC 4. **Dates:** Harbor uses `MM/dd/yyyy`; Summit uses `yyyyMMdd` and UTC timestamps
# MAGIC    (`2025-08-12T14:03:11Z`, keep the UTC date). Output `DATE` / `TIMESTAMP` types.
# MAGIC 5. **Quality:** a drift row with an empty `detected_version` is quarantined.
# MAGIC 6. **Duplicates:** Harbor sent drift twice (March 6 and April 3). Keep the row with the
# MAGIC    latest `extracted_at`. Summit double-wrote 6 comments; keep one copy.
# MAGIC 7. **Idempotent:** running this notebook twice must not change the row counts.
# MAGIC    Use `MERGE INTO`, not append.
# MAGIC
# MAGIC ### Column mapping
# MAGIC | Contract column | northstar_core | harbor_trust | summit_capital |
# MAGIC |---|---|---|---|
# MAGIC | **applications** | | | |
# MAGIC | source_app_id | app_id | AppNumber | asset_key |
# MAGIC | app_name | app_name | ApplicationName | asset_name |
# MAGIC | app_owner | app_owner | BusinessOwner | owner |
# MAGIC | owner_email / owner_team | owner_email / owner_team | OwnerEmail / OwnerTeam | owner_mail / team |
# MAGIC | resiliency_lead | resiliency_lead | ResiliencyLead | resiliency_contact |
# MAGIC | lob | lob | LineOfBusiness | business_unit |
# MAGIC | app_status / deploy_status | app_status / deploy_status | LifecycleStatus / DeploymentStatus | lifecycle / deploy_state |
# MAGIC | in_scope | in_scope (`yes_no`) | InComplianceScope (`yes_no`) | compliance_scope (`yes_no`) |
# MAGIC | rto_score | rto_score | RecoveryTimeHours → `rto_scale` | rto_minutes / 60 → `rto_scale` |
# MAGIC | primary_dc / secondary_dc | primary_dc / secondary_dc (`data_center`) | PrimarySite / SecondarySite (`data_center`) | primary_location / secondary_location (`data_center`; `none` → NULL) |
# MAGIC | env / hosting / os_family | env / hosting / os_family | EnvironmentTier / HostingModel / OSFamily | environment / hosting / os |
# MAGIC | last_test_date / result | last_test_date / last_test_result | LastDRTestDate / LastDRTestOutcome | dr_test_date / dr_test_result |
# MAGIC | extracted_at | extract_ts | ExtractedAt | exported_at |
# MAGIC | **drift_instances** | | | |
# MAGIC | source_drift_id | drift_id | DriftRef | event_id |
# MAGIC | (app link) | app_id | AppNumber | asset_key |
# MAGIC | product_category | product_category | ProductCategory | product_category (`product_category`) |
# MAGIC | product | product (`product_alias`) | ProductName (`product_alias`) | product (`product_alias`) |
# MAGIC | instance_name | instance_name | HostName | host |
# MAGIC | approved / detected_version | approved_version / detected_version | ApprovedVersion / DetectedVersion | baseline_version / observed_version |
# MAGIC | status | status (`status`) | DriftState (`status`) | is_open (`status`) |
# MAGIC | drift_open / close_date | drift_open_date / drift_close_date | OpenedDate / ClosedDate | opened_at / closed_at |
# MAGIC | exemption_requested | exemption_requested (`yes_no`) | ExemptionRequested (`yes_no`) | exemption (`exemption_requested`) |
# MAGIC | exemption_result | exemption_result | ExemptionDecision | exemption (`exemption_result`) |
# MAGIC | root_cause / environment | root_cause / environment | RootCause / Environment | root_cause / env |
# MAGIC | **drift_notes** | | | |
# MAGIC | note_id | note_id | NoteRef | comment_id |
# MAGIC | (drift link) | drift_id | DriftRef | event_id |
# MAGIC | note_date | note_date | NoteDate | created_at |
# MAGIC | note_type | note_type (`note_type`) | Category (`note_type`) | kind (`note_type`) |
# MAGIC | author_role / note_text | author_role / note_text | AuthorRole / Body | author_role / comment |
# MAGIC | escalation_flag | escalation_flag (`escalation`) | Escalated (`escalation`) | escalated (`escalation`) |
# MAGIC
# MAGIC Names in backticks are `ref.code_map` domains (or reference tables). Empty strings in bronze mean NULL.

# COMMAND ----------

# MAGIC %run ./_bootstrap

# COMMAND ----------

import json

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

for table, columns in contracts.SILVER_TABLES.items():
    print(f"silver.{table}: " + ", ".join(f"{name} {dtype}" for name, dtype in columns))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Plumbing (provided)

# COMMAND ----------


def bronze(source: str, entity: str) -> DataFrame:
    """A bronze table with empty strings turned into NULLs."""

    frame = spark.table(f"{SCHEMAS.bronze}.{config.bronze_table(source, entity)}")
    return frame.select(
        *[F.when(F.col(c) == "", None).otherwise(F.col(c)).alias(c) if t == "string" else F.col(c) for c, t in frame.dtypes]
    )


app_crosswalk = spark.table(f"{SCHEMAS.ref}.app_crosswalk")
drift_crosswalk = spark.table(f"{SCHEMAS.ref}.drift_crosswalk")
product_alias = spark.table(f"{SCHEMAS.ref}.product_alias")
code_map = spark.table(f"{SCHEMAS.ref}.code_map")
rto_scale = spark.table(f"{SCHEMAS.ref}.rto_scale")


def codes(source: str, domain: str) -> DataFrame:
    """(source_value, canonical_value) pairs for one source system and one code_map domain."""

    return code_map.where((F.col("source_system") == source) & (F.col("domain") == domain)).select(
        "source_value", "canonical_value"
    )


def ensure_table(name: str) -> str:
    """Create silver.<name> with the contract schema if it does not exist yet; return its full name."""

    full_name = f"{SCHEMAS.silver}.{name}"
    columns = ", ".join(f"{column} {dtype}" for column, dtype in contracts.SILVER_TABLES[name])
    spark.sql(f"CREATE TABLE IF NOT EXISTS {full_name} ({columns})")
    return full_name


def send_to_quarantine(rows: DataFrame, source: str, entity: str, id_column: str, reason: str) -> int:
    """Append failed rows to silver.quarantine (once per source record and reason). Returns the count."""

    table = ensure_table("quarantine")
    payload_columns = [c for c in rows.columns]
    batch = rows.select(
        F.lit(source).alias("source_system"),
        F.lit(entity).alias("entity"),
        F.col(id_column).cast("string").alias("source_record_id"),
        F.lit(reason).alias("reason"),
        F.to_json(F.struct(*payload_columns)).alias("record"),
        F.current_timestamp().alias("quarantined_at"),
    )
    batch.createOrReplaceTempView("quarantine_batch")
    spark.sql(
        f"""
        MERGE INTO {table} AS t
        USING quarantine_batch AS s
        ON t.source_system = s.source_system AND t.entity = s.entity
           AND t.source_record_id = s.source_record_id AND t.reason = s.reason
        WHEN NOT MATCHED THEN INSERT *
        """
    )
    return batch.count()


# COMMAND ----------

# MAGIC %md
# MAGIC ## Worked example: northstar_core applications
# MAGIC The pattern for every source and entity: **rename → map codes → map IDs → apply rules →
# MAGIC cast to the contract**. Northstar is the easy one (its columns already look canonical),
# MAGIC but it still goes through the same reference tables, and it contains one bad record.

# COMMAND ----------


def northstar_applications() -> DataFrame:
    raw = bronze("northstar_core", "applications")
    yes_no = codes("northstar_core", "yes_no")
    dc = codes("northstar_core", "data_center")
    renamed = raw.select(
        F.col("app_id").alias("source_app_id"),
        "app_name",
        "app_owner",
        "owner_email",
        "owner_team",
        "resiliency_lead",
        "lob",
        "app_status",
        "deploy_status",
        F.col("in_scope").alias("in_scope_code"),
        F.col("rto_score").cast("int").alias("rto_score"),
        F.col("primary_dc").alias("primary_dc_code"),
        F.col("secondary_dc").alias("secondary_dc_code"),
        "env",
        "hosting",
        "os_family",
        F.to_date("last_test_date", "yyyy-MM-dd").alias("last_test_date"),
        "last_test_result",
        F.to_timestamp("extract_ts").alias("extracted_at"),
    )
    return (
        renamed.join(yes_no.withColumnsRenamed({"source_value": "in_scope_code", "canonical_value": "in_scope"}), "in_scope_code", "left")
        .join(dc.withColumnsRenamed({"source_value": "primary_dc_code", "canonical_value": "primary_dc"}), "primary_dc_code", "left")
        .join(dc.withColumnsRenamed({"source_value": "secondary_dc_code", "canonical_value": "secondary_dc"}), "secondary_dc_code", "left")
        .withColumn("source_system", F.lit("northstar_core"))
    )


display(northstar_applications().limit(5))  # noqa: F821

# COMMAND ----------

# MAGIC %md
# MAGIC ## TODO 1: harbor_trust and summit_capital applications
# MAGIC Same shape as `northstar_applications()`. For RTO, join `rto_scale` on `rto_hours`
# MAGIC (Harbor: `RecoveryTimeHours`; Summit: `rto_minutes / 60`).
# MAGIC Dates: `F.to_date(col, "MM/dd/yyyy")` and `F.to_date(col, "yyyyMMdd")`.

# COMMAND ----------


def harbor_applications() -> DataFrame:
    raise NotImplementedError("TODO 1")


def summit_applications() -> DataFrame:
    raise NotImplementedError("TODO 1")


# COMMAND ----------

# MAGIC %md
# MAGIC ## TODO 2: conform, check, and MERGE applications
# MAGIC 1. Union the three (`unionByName`).
# MAGIC 2. Join `ref.app_crosswalk` on (`source_system`, `source_app_id`) to get `app_id`.
# MAGIC 3. Quarantine rows with no `app_id` or with `rto_score` outside 1-10 (`send_to_quarantine`),
# MAGIC    and keep the rest.
# MAGIC 4. Fill `rto_hours` from `rto_scale` for Northstar (it sends the score, not the hours).
# MAGIC 5. Select the contract columns in order and `MERGE INTO silver.applications` on `app_id`.
# MAGIC
# MAGIC Write the MERGE as a reusable function; drift and notes need it too.

# COMMAND ----------


def merge_into(batch: DataFrame, table_name: str, keys: list[str]) -> None:
    """Upsert batch into silver.<table_name>: update when the incoming row is newer
    (by extracted_at), insert when the key is new.

    Hints:
    - Call ensure_table(table_name) first so the target exists with the contract schema.
    - MERGE fails with "multiple source rows matched" if the batch has two rows for one
      key. Make the batch unique on `keys` first (Window + row_number, newest
      extracted_at first), then MERGE.
    """

    raise NotImplementedError("TODO 2")


def build_applications() -> None:
    raise NotImplementedError("TODO 2")


build_applications()

# COMMAND ----------

# MAGIC %md
# MAGIC ## TODO 3: drift instances
# MAGIC Three readers like TODO 1 (Summit's `exemption` field feeds two domains:
# MAGIC `exemption_requested` and `exemption_result`; Summit timestamps → `F.to_date(F.to_timestamp(col))`).
# MAGIC Then: crosswalk the drift ID **and** the app ID, quarantine `unknown application` and
# MAGIC `missing detected_version`, and `merge_into(..., "drift_instances", ["drift_id"])`.
# MAGIC Harbor's March 6 rows must lose to its April 3 rows.

# COMMAND ----------


def build_drift_instances() -> None:
    raise NotImplementedError("TODO 3")


build_drift_instances()

# COMMAND ----------

# MAGIC %md
# MAGIC ## TODO 4: drift notes
# MAGIC Readers as before; `escalation_flag` is the `escalation` code cast to BOOLEAN.
# MAGIC Map the note's drift link through `ref.drift_crosswalk`, keep notes whose drift is in
# MAGIC `silver.drift_instances`, drop exact duplicates, and merge on (`source_system`, `note_id`).

# COMMAND ----------


def build_drift_notes() -> None:
    raise NotImplementedError("TODO 4")


build_drift_notes()

# COMMAND ----------

for table in ("applications", "drift_instances", "drift_notes", "quarantine"):
    print(f"silver.{table:<16} {spark.table(f'{SCHEMAS.silver}.{table}').count():>6}")
print("Expected:", contracts.EXPECTED_ROWS)
