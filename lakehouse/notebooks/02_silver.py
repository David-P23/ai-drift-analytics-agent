# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "6"
# ///
# MAGIC %md
# MAGIC # 02 · Silver: conform three source systems onto shared definitions
# MAGIC
# MAGIC This is the post-acquisition integration problem in miniature: the same things (an
# MAGIC application, a drift finding, a data center, a status) arrive with different IDs, names,
# MAGIC codes and formats from three acquired companies. Silver maps all of them onto one definition.
# MAGIC
# MAGIC **Validation:** `05_checks` compares these tables with the contract in
# MAGIC `lakehouse/pipeline/contracts.py` and checks that gold reproduces the bundled SQLite demo
# MAGIC row for row.
# MAGIC
# MAGIC ### Output
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
# MAGIC 3. **RTO:** Harbor sends hours and Summit sends minutes; both are converted to hours and
# MAGIC    matched to a score in `ref.rto_scale`. An application whose score is not 1-10 is quarantined.
# MAGIC 4. **Dates:** Harbor uses `MM/dd/yyyy`; Summit uses `yyyyMMdd` and UTC timestamps
# MAGIC    (`2025-08-12T14:03:11Z`), which keep their UTC date. Output columns are `DATE` / `TIMESTAMP`.
# MAGIC 5. **Quality:** a drift row with an empty `detected_version` is quarantined.
# MAGIC 6. **Duplicates:** Harbor sent drift twice (March 6 and April 3); the row with the latest
# MAGIC    `extracted_at` wins. Summit double-wrote 6 comments; one copy is kept.
# MAGIC 7. **Idempotent:** a rerun changes no row counts, because every table is written with
# MAGIC    `MERGE INTO`, not appended.
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
# MAGIC ## Shared helpers

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
# MAGIC ## Applications
# MAGIC Every source and entity follows the same pattern: **rename → map codes → map IDs → apply
# MAGIC rules → cast to the contract**. Northstar's columns are already close to the shared names,
# MAGIC but it goes through the same reference tables, and it contains one invalid record.

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
# MAGIC ### Harbor and Summit readers
# MAGIC Harbor sends RTO in hours and Summit in minutes; both are converted to hours and matched
# MAGIC to a score in `ref.rto_scale`. Harbor dates are `MM/dd/yyyy`, Summit's are `yyyyMMdd`.

# COMMAND ----------


def harbor_applications() -> DataFrame:
    raw = bronze("harbor_trust", "applications")
    yes_no = codes("harbor_trust", "yes_no")
    dc = codes("harbor_trust", "data_center")
    renamed = raw.select(
        F.col("AppNumber").alias("source_app_id"),
        F.col("ApplicationName").alias("app_name"),
        F.col("BusinessOwner").alias("app_owner"),
        F.col("OwnerEmail").alias("owner_email"),
        F.col("OwnerTeam").alias("owner_team"),
        F.col("ResiliencyLead").alias("resiliency_lead"),
        F.col("LineOfBusiness").alias("lob"),
        F.col("LifecycleStatus").alias("app_status"),
        F.col("DeploymentStatus").alias("deploy_status"),
        F.col("InComplianceScope").alias("in_scope_code"),
        F.col("RecoveryTimeHours").cast("double").alias("rto_hours"),
        F.col("PrimarySite").alias("primary_dc_code"),
        F.col("SecondarySite").alias("secondary_dc_code"),
        F.col("EnvironmentTier").alias("env"),
        F.col("HostingModel").alias("hosting"),
        F.col("OSFamily").alias("os_family"),
        F.to_date("LastDRTestDate", "MM/dd/yyyy").alias("last_test_date"),
        F.col("LastDRTestOutcome").alias("last_test_result"),
        F.to_timestamp("ExtractedAt").alias("extracted_at"),
    )
    return (
        renamed
        .join(rto_scale.select("rto_hours", "rto_score"), "rto_hours", "left")
        .join(yes_no.withColumnsRenamed({"source_value": "in_scope_code", "canonical_value": "in_scope"}), "in_scope_code", "left")
        .join(dc.withColumnsRenamed({"source_value": "primary_dc_code", "canonical_value": "primary_dc"}), "primary_dc_code", "left")
        .join(dc.withColumnsRenamed({"source_value": "secondary_dc_code", "canonical_value": "secondary_dc"}), "secondary_dc_code", "left")
        .withColumn("source_system", F.lit("harbor_trust"))
    )

def summit_applications() -> DataFrame:
    raw = bronze("summit_capital", "applications")
    yes_no = codes("summit_capital", "yes_no")
    dc = codes("summit_capital", "data_center")
    renamed = raw.select(
        F.col("asset_key").alias("source_app_id"),
        F.col("asset_name").alias("app_name"),
        F.col("owner").alias("app_owner"),
        F.col("owner_mail").alias("owner_email"),
        F.col("team").alias("owner_team"),
        F.col("resiliency_contact").alias("resiliency_lead"),
        F.col("business_unit").alias("lob"),
        F.col("lifecycle").alias("app_status"),
        F.col("deploy_state").alias("deploy_status"),
        F.col("compliance_scope").alias("in_scope_code"),
        (F.col("rto_minutes").cast("double") / 60).alias("rto_hours"),
        F.col("primary_location").alias("primary_dc_code"),
        F.col("secondary_location").alias("secondary_dc_code"),
        F.col("environment").alias("env"),
        F.col("hosting").alias("hosting"),
        F.col("os").alias("os_family"),
        F.to_date("dr_test_date", "yyyyMMdd").alias("last_test_date"),
        F.col("dr_test_result").alias("last_test_result"),
        F.to_timestamp("exported_at").alias("extracted_at"),
    )
    return (
        renamed
        .join(rto_scale.select("rto_hours", "rto_score"), "rto_hours", "left")
        .join(yes_no.withColumnsRenamed({"source_value": "in_scope_code", "canonical_value": "in_scope"}), "in_scope_code", "left")
        .join(dc.withColumnsRenamed({"source_value": "primary_dc_code", "canonical_value": "primary_dc"}), "primary_dc_code", "left")
        .join(dc.withColumnsRenamed({"source_value": "secondary_dc_code", "canonical_value": "secondary_dc"}), "secondary_dc_code", "left")
        .withColumn("source_system", F.lit("summit_capital"))
    )


# COMMAND ----------

# MAGIC %md
# MAGIC ### Conform, quarantine and MERGE
# MAGIC Union the three readers, map each source ID to the shared `app_id` through
# MAGIC `ref.app_crosswalk`, quarantine rows with no shared ID or an RTO score outside 1-10, look up
# MAGIC `rto_hours` for Northstar (it sends the score, not the hours), and `MERGE` into
# MAGIC `silver.applications` on `app_id`. `merge_into` is shared by all three entities.

# COMMAND ----------

def merge_into(batch: DataFrame, table_name: str, keys: list[str]) -> None:
    """Upsert into silver.<table_name>: the newest extract per key wins, and a rerun changes nothing."""

    target = ensure_table(table_name)

    # One row per key: keep the newest extract
    newest_first = Window.partitionBy(*keys).orderBy(F.col("extracted_at").desc())
    latest = (
        batch.withColumn("_rn", F.row_number().over(newest_first))
        .where("_rn = 1")
        .drop("_rn")
    )
    latest.createOrReplaceTempView("merge_batch")

    on = " AND ".join(f"t.{k} = s.{k}" for k in keys)
    spark.sql(f"""
        MERGE INTO {target} AS t
        USING merge_batch AS s
        ON {on}
        WHEN MATCHED AND s.extracted_at > t.extracted_at THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
    """)


def build_applications() -> None:
    # Northstar sends the score, not the hours: look the hours up
    northstar = northstar_applications().join(rto_scale.select("rto_score", "rto_hours"), "rto_score", "left")
    combined = (
        northstar
        .unionByName(harbor_applications(), allowMissingColumns=True)
        .unionByName(summit_applications(), allowMissingColumns=True)
    )

    # Every source ID -> one shared app_id
    with_ids = combined.join(
        app_crosswalk.withColumnRenamed("canonical_app_id", "app_id"),
        ["source_system", "source_app_id"],
        "left",
    )

    # Rules: set failures aside with a reason, never drop them silently
    unknown_app = F.col("app_id").isNull()
    bad_rto = F.col("rto_score").isNull() | ~F.col("rto_score").between(1, 10)
    for source in config.SOURCE_SYSTEMS:
        from_source = with_ids.where(F.col("source_system") == source)
        send_to_quarantine(from_source.where(unknown_app), source, "applications", "source_app_id", "unknown application")
        send_to_quarantine(from_source.where(bad_rto), source, "applications", "source_app_id", "rto_score not 1-10")

    # Keep the rest, in contract order and types, and upsert
    good = with_ids.where(~unknown_app & ~bad_rto)
    contract = [F.col(name).cast(dtype).alias(name) for name, dtype in contracts.SILVER_APPLICATIONS]
    merge_into(good.select(*contract), "applications", ["app_id"])


build_applications()

# COMMAND ----------

display(spark.sql(f"SELECT COUNT(*) AS rows, COUNT(DISTINCT app_id) AS app_ids FROM {SCHEMAS.silver}.applications"))
display(spark.table(f"{SCHEMAS.silver}.quarantine").select("source_system", "source_record_id", "reason"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Drift instances
# MAGIC Three readers in the same pattern. Summit's single `exemption` field feeds two shared
# MAGIC columns (`exemption_requested` and `exemption_result`), and its UTC timestamps keep their
# MAGIC UTC date. The drift ID and the app ID both go through their crosswalks; drift on an
# MAGIC application that is not in silver, or with no `detected_version`, is quarantined.
# MAGIC `merge_into` keeps Harbor's April 3 rows over its March 6 rows.

# COMMAND ----------

def map_code(frame: DataFrame, column: str, source: str, domain: str, output: str) -> DataFrame:
    """Add `output`: the shared value of `column` from ref.code_map."""
    lookup = codes(source, domain).withColumnsRenamed({"source_value": column, "canonical_value": output})
    return frame.join(lookup, column, "left")


def map_product(frame: DataFrame, source: str) -> DataFrame:
    """Add `product`: the shared product name for `product_code` from ref.product_alias."""
    lookup = product_alias.where(F.col("source_system") == source).select(
        F.col("source_product").alias("product_code"),
        F.col("canonical_product").alias("product"),
    )
    return frame.join(lookup, "product_code", "left")


def northstar_drift() -> DataFrame:
    raw = bronze("northstar_core", "drift_instances")
    renamed = raw.select(
        F.col("drift_id").alias("source_drift_id"),
        F.col("app_id").alias("source_app_id"),
        F.col("product_category").alias("product_category"),
        F.col("product").alias("product_code"),
        F.col("instance_name").alias("instance_name"),
        F.col("approved_version").alias("approved_version"),
        F.col("detected_version").alias("detected_version"),
        F.col("status").alias("status_code"),
        F.to_date("drift_open_date", "yyyy-MM-dd").alias("drift_open_date"),
        F.to_date("drift_close_date", "yyyy-MM-dd").alias("drift_close_date"),
        F.col("exemption_requested").alias("exemption_requested_code"),
        F.col("exemption_result").alias("exemption_result"),
        F.col("root_cause").alias("root_cause"),
        F.col("environment").alias("environment"),
        F.to_timestamp("extract_ts").alias("extracted_at"),
    )
    out = map_code(renamed, "status_code", "northstar_core", "status", "status")
    out = map_code(out, "exemption_requested_code", "northstar_core", "yes_no", "exemption_requested")
    return map_product(out, "northstar_core").withColumn("source_system", F.lit("northstar_core"))


def harbor_drift() -> DataFrame:
    raw = bronze("harbor_trust", "drift_instances")
    renamed = raw.select(
        F.col("DriftRef").alias("source_drift_id"),
        F.col("AppNumber").alias("source_app_id"),
        F.col("ProductCategory").alias("product_category"),
        F.col("ProductName").alias("product_code"),
        F.col("HostName").alias("instance_name"),
        F.col("ApprovedVersion").alias("approved_version"),
        F.col("DetectedVersion").alias("detected_version"),
        F.col("DriftState").alias("status_code"),
        F.to_date("OpenedDate", "MM/dd/yyyy").alias("drift_open_date"),
        F.to_date("ClosedDate", "MM/dd/yyyy").alias("drift_close_date"),
        F.col("ExemptionRequested").alias("exemption_requested_code"),
        F.col("ExemptionDecision").alias("exemption_result"),
        F.col("RootCause").alias("root_cause"),
        F.col("Environment").alias("environment"),
        F.to_timestamp("ExtractedAt").alias("extracted_at"),
    )
    out = map_code(renamed, "status_code", "harbor_trust", "status", "status")
    out = map_code(out, "exemption_requested_code", "harbor_trust", "yes_no", "exemption_requested")
    return map_product(out, "harbor_trust").withColumn("source_system", F.lit("harbor_trust"))


def summit_drift() -> DataFrame:
    raw = bronze("summit_capital", "drift_instances")
    renamed = raw.select(
        F.col("event_id").alias("source_drift_id"),
        F.col("asset_key").alias("source_app_id"),
        F.col("product_category").alias("category_code"),
        F.col("product").alias("product_code"),
        F.col("host").alias("instance_name"),
        F.col("baseline_version").alias("approved_version"),
        F.col("observed_version").alias("detected_version"),
        F.col("is_open").alias("status_code"),
        F.to_date(F.to_timestamp("opened_at")).alias("drift_open_date"),
        F.to_date(F.to_timestamp("closed_at")).alias("drift_close_date"),
        F.col("exemption").alias("exemption_code"),
        F.col("root_cause").alias("root_cause"),
        F.col("env").alias("environment"),
        F.to_timestamp("exported_at").alias("extracted_at"),
    )
    out = map_code(renamed, "status_code", "summit_capital", "status", "status")
    out = map_code(out, "category_code", "summit_capital", "product_category", "product_category")
    # One Summit field feeds two shared columns: "PENDING" -> requested Y, result Pending
    out = map_code(out, "exemption_code", "summit_capital", "exemption_requested", "exemption_requested")
    out = map_code(out, "exemption_code", "summit_capital", "exemption_result", "exemption_result")
    return map_product(out, "summit_capital").withColumn("source_system", F.lit("summit_capital"))


def build_drift_instances() -> None:
    combined = (
        northstar_drift()
        .unionByName(harbor_drift(), allowMissingColumns=True)
        .unionByName(summit_drift(), allowMissingColumns=True)
    )

    # Two crosswalks: the drift's own ID and the application it belongs to
    with_ids = (
        combined
        .join(app_crosswalk.withColumnRenamed("canonical_app_id", "app_id"), ["source_system", "source_app_id"], "left")
        .join(drift_crosswalk.withColumnRenamed("canonical_drift_id", "drift_id"), ["source_system", "source_drift_id"], "left")
    )

    # A drift row needs an application that made it into silver, not just one in the crosswalk
    apps_in_silver = spark.table(f"{SCHEMAS.silver}.applications").select("app_id", F.lit(True).alias("app_in_silver"))
    checked = with_ids.join(apps_in_silver, "app_id", "left")

    unknown_app = F.col("app_in_silver").isNull()
    no_version = F.col("detected_version").isNull()
    for source in config.SOURCE_SYSTEMS:
        from_source = checked.where(F.col("source_system") == source)
        send_to_quarantine(from_source.where(unknown_app), source, "drift_instances", "source_drift_id", "unknown application")
        send_to_quarantine(from_source.where(no_version), source, "drift_instances", "source_drift_id", "missing detected_version")

    good = checked.where(~unknown_app & ~no_version)
    contract = [F.col(name).cast(dtype).alias(name) for name, dtype in contracts.SILVER_DRIFT_INSTANCES]
    # merge_into keeps the newest extract per drift_id, so Harbor's April rows beat its March rows
    merge_into(good.select(*contract), "drift_instances", ["drift_id"])


build_drift_instances()

# COMMAND ----------

display(spark.sql(f"SELECT COUNT(*) AS rows, COUNT(DISTINCT drift_id) AS drift_ids FROM {SCHEMAS.silver}.drift_instances"))
display(spark.table(f"{SCHEMAS.silver}.quarantine").select("source_system", "entity", "source_record_id", "reason"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Drift notes
# MAGIC Escalation codes become a BOOLEAN, Summit's duplicate comments are dropped, and each note
# MAGIC links to its drift through `ref.drift_crosswalk`. A note on a drift that is not in silver
# MAGIC is quarantined. Notes merge on (`source_system`, `note_id`), since note IDs are only
# MAGIC unique within one source.

# COMMAND ----------

def northstar_notes() -> DataFrame:
    raw = bronze("northstar_core", "drift_notes")
    renamed = raw.select(
        F.col("note_id").alias("note_id"),
        F.col("drift_id").alias("source_drift_id"),
        F.to_date("note_date", "yyyy-MM-dd").alias("note_date"),
        F.col("note_type").alias("note_type_code"),
        F.col("author_role").alias("author_role"),
        F.col("note_text").alias("note_text"),
        F.col("escalation_flag").alias("escalation_code"),
        F.to_timestamp("extract_ts").alias("extracted_at"),
    )
    out = map_code(renamed, "note_type_code", "northstar_core", "note_type", "note_type")
    out = map_code(out, "escalation_code", "northstar_core", "escalation", "escalation")
    return out.withColumn("source_system", F.lit("northstar_core"))


def harbor_notes() -> DataFrame:
    raw = bronze("harbor_trust", "drift_notes")
    renamed = raw.select(
        F.col("NoteRef").alias("note_id"),
        F.col("DriftRef").alias("source_drift_id"),
        F.to_date("NoteDate", "MM/dd/yyyy").alias("note_date"),
        F.col("Category").alias("note_type_code"),
        F.col("AuthorRole").alias("author_role"),
        F.col("Body").alias("note_text"),
        F.col("Escalated").alias("escalation_code"),
        F.to_timestamp("ExtractedAt").alias("extracted_at"),
    )
    out = map_code(renamed, "note_type_code", "harbor_trust", "note_type", "note_type")
    out = map_code(out, "escalation_code", "harbor_trust", "escalation", "escalation")
    return out.withColumn("source_system", F.lit("harbor_trust"))


def summit_notes() -> DataFrame:
    raw = bronze("summit_capital", "drift_notes")
    renamed = raw.select(
        F.col("comment_id").alias("note_id"),
        F.col("event_id").alias("source_drift_id"),
        F.to_date(F.to_timestamp("created_at")).alias("note_date"),
        F.col("kind").alias("note_type_code"),
        F.col("author_role").alias("author_role"),
        F.col("comment").alias("note_text"),
        F.col("escalated").alias("escalation_code"),
        F.to_timestamp("exported_at").alias("extracted_at"),
    )
    out = map_code(renamed, "note_type_code", "summit_capital", "note_type", "note_type")
    out = map_code(out, "escalation_code", "summit_capital", "escalation", "escalation")
    return out.withColumn("source_system", F.lit("summit_capital"))


def build_drift_notes() -> None:
    combined = (
        northstar_notes()
        .unionByName(harbor_notes(), allowMissingColumns=True)
        .unionByName(summit_notes(), allowMissingColumns=True)
        .dropDuplicates()  # Summit wrote 6 comments twice: identical copies, keep one
        .withColumn("escalation_flag", F.col("escalation").cast("boolean"))
    )

    # The note's drift link goes through the same drift crosswalk as drift instances
    with_ids = combined.join(
        drift_crosswalk.withColumnRenamed("canonical_drift_id", "drift_id"),
        ["source_system", "source_drift_id"],
        "left",
    )

    # A note must belong to a drift that made it into silver
    drift_in_silver = spark.table(f"{SCHEMAS.silver}.drift_instances").select("drift_id", F.lit(True).alias("drift_in_silver"))
    checked = with_ids.join(drift_in_silver, "drift_id", "left")

    unknown_drift = F.col("drift_in_silver").isNull()
    for source in config.SOURCE_SYSTEMS:
        from_source = checked.where(F.col("source_system") == source)
        send_to_quarantine(from_source.where(unknown_drift), source, "drift_notes", "note_id", "unknown drift")

    good = checked.where(~unknown_drift)
    contract = [F.col(name).cast(dtype).alias(name) for name, dtype in contracts.SILVER_DRIFT_NOTES]
    merge_into(good.select(*contract), "drift_notes", ["source_system", "note_id"])


build_drift_notes()

# COMMAND ----------

for table in ("applications", "drift_instances", "drift_notes", "quarantine"):
    print(f"silver.{table:<16} {spark.table(f'{SCHEMAS.silver}.{table}').count():>6}")
print("Expected:", contracts.EXPECTED_ROWS)