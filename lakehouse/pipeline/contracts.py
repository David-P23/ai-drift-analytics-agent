"""Table contracts between the layers.

Silver is built in notebook 02. Gold (notebook 03) is built against the
column lists below, and the checks notebook (05) fails if silver drifts from
them. Change a contract here first, then the code.
"""

from __future__ import annotations

# (column, Spark SQL type). Order matters only for readability.
SILVER_APPLICATIONS: list[tuple[str, str]] = [
    ("app_id", "STRING"),  # canonical ID from ref.app_crosswalk
    ("source_system", "STRING"),
    ("source_app_id", "STRING"),
    ("app_name", "STRING"),
    ("app_owner", "STRING"),
    ("owner_email", "STRING"),
    ("owner_team", "STRING"),
    ("resiliency_lead", "STRING"),
    ("lob", "STRING"),
    ("app_status", "STRING"),
    ("deploy_status", "STRING"),
    ("in_scope", "STRING"),  # 'Y' / 'N'
    ("rto_score", "INT"),  # 1-10, via ref.rto_scale for sources that send hours or minutes
    ("rto_hours", "DOUBLE"),
    ("primary_dc", "STRING"),  # canonical city name via ref.code_map (domain data_center)
    ("secondary_dc", "STRING"),
    ("env", "STRING"),
    ("hosting", "STRING"),
    ("os_family", "STRING"),
    ("last_test_date", "DATE"),
    ("last_test_result", "STRING"),
    ("extracted_at", "TIMESTAMP"),  # when the source system produced the record
]

SILVER_DRIFT_INSTANCES: list[tuple[str, str]] = [
    ("drift_id", "STRING"),  # canonical ID from ref.drift_crosswalk
    ("app_id", "STRING"),  # canonical ID from ref.app_crosswalk
    ("source_system", "STRING"),
    ("source_drift_id", "STRING"),
    ("product_category", "STRING"),
    ("product", "STRING"),  # canonical product via ref.product_alias
    ("instance_name", "STRING"),
    ("approved_version", "STRING"),
    ("detected_version", "STRING"),
    ("status", "STRING"),  # 'Open' / 'Closed'
    ("drift_open_date", "DATE"),
    ("drift_close_date", "DATE"),
    ("exemption_requested", "STRING"),  # 'Y' / 'N'
    ("exemption_result", "STRING"),  # 'Approved' / 'Pending' / 'Denied' / NULL
    ("root_cause", "STRING"),
    ("environment", "STRING"),
    ("extracted_at", "TIMESTAMP"),
]

SILVER_DRIFT_NOTES: list[tuple[str, str]] = [
    ("note_id", "STRING"),  # source-native ID; sorts in creation order within a drift
    ("drift_id", "STRING"),  # canonical
    ("source_system", "STRING"),
    ("note_date", "DATE"),
    ("note_type", "STRING"),
    ("author_role", "STRING"),
    ("note_text", "STRING"),
    ("escalation_flag", "BOOLEAN"),
    ("extracted_at", "TIMESTAMP"),
]

SILVER_QUARANTINE: list[tuple[str, str]] = [
    ("source_system", "STRING"),
    ("entity", "STRING"),  # applications / drift_instances / drift_notes
    ("source_record_id", "STRING"),
    ("reason", "STRING"),
    ("record", "STRING"),  # the bronze row as JSON, so nothing is lost
    ("quarantined_at", "TIMESTAMP"),
]

SILVER_TABLES: dict[str, list[tuple[str, str]]] = {
    "applications": SILVER_APPLICATIONS,
    "drift_instances": SILVER_DRIFT_INSTANCES,
    "drift_notes": SILVER_DRIFT_NOTES,
    "quarantine": SILVER_QUARANTINE,
}

# The 23 columns the analytics agent queries (src/domain.py APPLICATIONS_TABLE).
AGENT_COLUMNS: list[str] = [
    "finding_id",
    "app_id",
    "drift_id",
    "app_name",
    "business_owner",
    "technology_owner",
    "product",
    "data_center",
    "approved_version",
    "detected_version",
    "rto_score",
    "status",
    "exemption_requested",
    "exemption_result",
    "in_scope",
    "drift_detected_on",
    "last_scanned_at",
    "note_count",
    "latest_note_date",
    "latest_note_type",
    "latest_note_author_role",
    "latest_note_text",
    "escalation_flag",
]

# What a correct silver layer produces from the committed source files.
EXPECTED_ROWS = {
    "silver.applications": 1000,
    "silver.drift_instances": 475,
    "silver.drift_notes": 804,
    "silver.quarantine": 4,  # distinct records: 1 application + 3 drift instances (see generate_sources.py)
    "gold.drift_findings": 475,
}

# Distinct quarantined source records per entity. A record that breaks two rules may
# appear twice in silver.quarantine (once per reason); it still counts once here.
EXPECTED_QUARANTINE_BY_ENTITY = {"applications": 1, "drift_instances": 3}

# Bronze rows that correctly do not reach silver because a newer or identical copy
# exists: Harbor's March 6 drift snapshot (124 rows, all re-sent on April 3) and
# Summit's 6 duplicate comments.
EXPECTED_SUPERSEDED = {"applications": 0, "drift_instances": 124, "drift_notes": 6}

# Gold measures aging as of the extract (scan) date, not the day the pipeline runs.
AS_OF_DATE = "2026-04-03"
# Open in-scope findings per aging bucket as of AS_OF_DATE.
EXPECTED_AGING = {"<60 Current": 17, "60-89 Warning": 10, "90-119 Senior escalation": 12, "120+ Executive escalation": 86}
