"""Split the NorthStar v1 dataset into three simulated "acquired company" source systems.

The v1 dataset (``lakehouse/seed/northstar_v1_normalized.sqlite``) is one clean,
normalized portfolio: 1,000 applications, 475 drift instances and 804 drift notes.
This script re-exports it the way three separately acquired companies would hand
it over: each system owns part of the portfolio and uses its own column names,
ID formats, codes and date formats. It also writes the master-data reference
tables (crosswalks and code maps) that map every source back to one shared
definition, and it injects a small, documented set of dirty records so the
silver layer has real data-quality work to do.

Every transformation is reversible, so the gold layer built in Databricks must
reproduce the bundled SQLite demo (``data/northstar_demo_rows.json``) exactly.

Usage (from the repo root):

    python lakehouse/generate_sources.py

Output: ``lakehouse/source_data/`` (committed, so nobody has to rerun this).
"""

from __future__ import annotations

import argparse
import csv
import random
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DEFAULT_SEED_DB = ROOT / "seed" / "northstar_v1_normalized.sqlite"
DEFAULT_OUTPUT = ROOT / "source_data"

# Each acquired company owns two lines of business.
SOURCE_LOBS: dict[str, tuple[str, ...]] = {
    "northstar_core": ("Customer Platforms", "Payments"),
    "harbor_trust": ("Risk & Compliance", "Treasury & Finance"),
    "summit_capital": ("Technology Resiliency", "Internal Services"),
}

# The v1 portfolio was built as of April 3, 2026 (its open-drift durations are
# measured to that date), so every source's current extract is stamped then. Gold
# and the agent measure aging as of the scan date, which keeps the 60/90/120-day
# KPIs meaningful no matter when the demo is opened. Midday UTC keeps the calendar
# date the same in any US time zone.
CURRENT_EXTRACT_DATE = "2026-04-03"
CURRENT_EXTRACT_TS = f"{CURRENT_EXTRACT_DATE}T12:00:00Z"
# Harbor Trust also sent the export from four weeks earlier.
HARBOR_PRIOR_EXTRACT_DATE = "2026-03-06"
HARBOR_PRIOR_EXTRACT_TS = f"{HARBOR_PRIOR_EXTRACT_DATE}T12:00:00Z"

# Shared RTO scale (score -> maximum recovery hours), from DATASET.md.
RTO_SCALE: dict[int, tuple[float, str]] = {
    1: (0.5, "Mission Critical"),
    2: (1.0, "Mission Critical"),
    3: (2.0, "High"),
    4: (4.0, "High"),
    5: (8.0, "Medium"),
    6: (12.0, "Medium"),
    7: (24.0, "Low"),
    8: (48.0, "Low"),
    9: (72.0, "Very Low"),
    10: (120.0, "Very Low"),
}

HARBOR_SITE_CODES = {
    "Ashburn": "IAD",
    "Chandler": "CHD",
    "Charlotte": "CLT",
    "Columbus": "CMH",
    "Dallas": "DAL",
    "Des Moines": "DSM",
    "Eagan": "EGN",
    "Minneapolis": "MSP",
    "Phoenix": "PHX",
    "St. Louis": "STL",
}

SUMMIT_SITE_CODES = {
    "Ashburn": "ashburn-va",
    "Chandler": "chandler-az",
    "Charlotte": "charlotte-nc",
    "Columbus": "columbus-oh",
    "Dallas": "dallas-tx",
    "Des Moines": "des-moines-ia",
    "Eagan": "eagan-mn",
    "Minneapolis": "minneapolis-mn",
    "Phoenix": "phoenix-az",
    "St. Louis": "st-louis-mo",
}

# Canonical product -> (Harbor Trust name, Summit Capital code)
PRODUCT_ALIASES: dict[str, tuple[str, str]] = {
    "RHEL": ("Red Hat Enterprise Linux", "rhel"),
    "Windows Server": ("Microsoft Windows Server", "windows_server"),
    "Ubuntu Server": ("Canonical Ubuntu Server", "ubuntu_server"),
    "SUSE Linux": ("SUSE Linux Enterprise Server", "sles"),
    "Oracle Database": ("Oracle Database", "oracle_db"),
    "SQL Server": ("Microsoft SQL Server", "mssql"),
    "PostgreSQL": ("PostgreSQL", "postgresql"),
    "MongoDB Enterprise": ("MongoDB Enterprise Advanced", "mongodb_ent"),
    "WebLogic": ("Oracle WebLogic Server", "weblogic"),
    "WebSphere": ("IBM WebSphere Application Server", "websphere"),
    "Tomcat": ("Apache Tomcat", "tomcat"),
    "JBoss EAP": ("Red Hat JBoss EAP", "jboss_eap"),
    "Apache HTTP Server": ("Apache HTTP Server", "apache_httpd"),
    "NGINX": ("NGINX Plus", "nginx"),
    "Apigee Gateway": ("Google Apigee", "apigee"),
    "IIS": ("Microsoft IIS", "iis"),
    "Okta Agent": ("Okta AD Agent", "okta_agent"),
    "CyberArk": ("CyberArk PAM", "cyberark"),
    "PingFederate": ("Ping Identity PingFederate", "pingfederate"),
    "SailPoint IQService": ("SailPoint IdentityIQ IQService", "sailpoint_iqservice"),
    "IBM MQ": ("IBM MQ", "ibm_mq"),
    "Kafka": ("Confluent Kafka", "kafka"),
    "MuleSoft Runtime": ("MuleSoft Mule Runtime", "mulesoft"),
    "TIBCO EMS": ("TIBCO Enterprise Message Service", "tibco_ems"),
    "Splunk UF": ("Splunk Universal Forwarder", "splunk_uf"),
    "Dynatrace OneAgent": ("Dynatrace OneAgent", "dynatrace"),
    "AppDynamics Agent": ("AppDynamics Java Agent", "appdynamics"),
    "Tanium Client": ("Tanium Client", "tanium"),
}

SUMMIT_CATEGORY_CODES = {
    "Operating System": "os",
    "Database": "database",
    "Middleware": "middleware",
    "Web Server / API": "web_api",
    "Security / Identity": "security_identity",
    "Messaging / Integration": "messaging",
    "Monitoring / Agent": "monitoring",
}

SUMMIT_NOTE_KINDS = {
    "Closure Note": "closure_note",
    "Escalation": "escalation",
    "Exemption Request": "exemption_request",
    "Exemption Review": "exemption_review",
    "Owner Update": "owner_update",
    "Remediation Update": "remediation_update",
    "Validation Result": "validation_result",
}

# Expected outcome of the injected dirty records; the checks notebook and the
# tests read these numbers so the story stays in one place.
EXPECTED_QUARANTINE = {
    "applications": 1,  # northstar_core test app with rto_score 11
    "drift_instances": 3,  # 2 Harbor orphans (unknown app) + 1 Summit row missing detected_version
    "drift_notes": 0,
}
SUMMIT_DUPLICATE_COMMENTS = 6  # exact duplicate rows in the Summit comment export


@dataclass
class Canonical:
    applications: list[dict[str, Any]]
    drift_instances: list[dict[str, Any]]
    drift_notes: list[dict[str, Any]]


def load_canonical(seed_db: Path) -> Canonical:
    conn = sqlite3.connect(str(seed_db))
    conn.row_factory = sqlite3.Row
    try:
        apps = [dict(r) for r in conn.execute("SELECT * FROM applications ORDER BY app_id")]
        drift = [dict(r) for r in conn.execute("SELECT * FROM drift_instances ORDER BY drift_id")]
        notes = [dict(r) for r in conn.execute("SELECT * FROM drift_notes ORDER BY note_id")]
    finally:
        conn.close()
    return Canonical(apps, drift, notes)


def _us_date(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{d.month:02d}/{d.day:02d}/{d.year}"


def _compact_date(iso: str | None) -> str:
    return iso.replace("-", "") if iso else ""


def _utc_timestamp(iso: str | None, rng: random.Random) -> str:
    """Date -> ISO-8601 UTC timestamp during business hours (date part unchanged in UTC)."""

    if not iso:
        return ""
    return f"{iso}T{rng.randint(13, 21):02d}:{rng.randint(0, 59):02d}:{rng.randint(0, 59):02d}Z"


def _hours(value: float) -> str:
    return f"{value:g}"


def _write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: ("" if row.get(column) is None else row.get(column)) for column in columns})


def build(seed_db: Path = DEFAULT_SEED_DB, output: Path = DEFAULT_OUTPUT) -> dict[str, int]:
    canon = load_canonical(seed_db)
    rng = random.Random(20261014)

    app_source = {}
    for app in canon.applications:
        source = next(name for name, lobs in SOURCE_LOBS.items() if app["lob"] in lobs)
        app_source[app["app_id"]] = source
    drift_by_id = {d["drift_id"]: d for d in canon.drift_instances}

    # ---------------------------------------------------------------- crosswalks
    app_xwalk: list[dict[str, Any]] = []
    drift_xwalk: list[dict[str, Any]] = []
    source_app_id: dict[str, str] = {}
    source_drift_id: dict[str, str] = {}

    harbor_apps = [a["app_id"] for a in canon.applications if app_source[a["app_id"]] == "harbor_trust"]
    summit_apps = [a["app_id"] for a in canon.applications if app_source[a["app_id"]] == "summit_capital"]
    harbor_numbers = rng.sample(range(10000, 99999), len(harbor_apps))
    summit_numbers = rng.sample(range(70001, 79999), len(summit_apps))
    for app_id, number in zip(harbor_apps, harbor_numbers):
        source_app_id[app_id] = f"HT-{number:05d}"
    for app_id, number in zip(summit_apps, summit_numbers):
        source_app_id[app_id] = str(number)
    for app in canon.applications:
        if app_source[app["app_id"]] == "northstar_core":
            source_app_id[app["app_id"]] = app["app_id"]
        app_xwalk.append(
            {
                "source_system": app_source[app["app_id"]],
                "source_app_id": source_app_id[app["app_id"]],
                "canonical_app_id": app["app_id"],
            }
        )

    harbor_drift = [d["drift_id"] for d in canon.drift_instances if app_source[d["app_id"]] == "harbor_trust"]
    summit_drift = [d["drift_id"] for d in canon.drift_instances if app_source[d["app_id"]] == "summit_capital"]
    for drift_id, number in zip(harbor_drift, rng.sample(range(100000, 999999), len(harbor_drift))):
        source_drift_id[drift_id] = f"HTD-{number:06d}"
    for drift_id, number in zip(summit_drift, rng.sample(range(100000, 999999), len(summit_drift))):
        source_drift_id[drift_id] = f"EVT-{number:06d}"
    for drift in canon.drift_instances:
        source = app_source[drift["app_id"]]
        if source == "northstar_core":
            source_drift_id[drift["drift_id"]] = drift["drift_id"]
        drift_xwalk.append(
            {
                "source_system": source,
                "source_drift_id": source_drift_id[drift["drift_id"]],
                "canonical_drift_id": drift["drift_id"],
            }
        )

    # Note IDs stay in creation order within each source (gold breaks same-day
    # ties on note_id, so the order has to survive the re-keying).
    source_note_id: dict[str, str] = {}
    counters = {"harbor_trust": 4100000, "summit_capital": 5000000}
    for note in canon.drift_notes:  # already sorted by canonical note_id
        source = app_source[note["app_id"]]
        if source == "northstar_core":
            source_note_id[note["note_id"]] = note["note_id"]
        else:
            counters[source] += 1
            prefix = "HTN-" if source == "harbor_trust" else ""
            source_note_id[note["note_id"]] = f"{prefix}{counters[source]}"

    # ------------------------------------------------------------ northstar_core
    out = output
    ns_apps = [a for a in canon.applications if app_source[a["app_id"]] == "northstar_core"]
    ns_drift = [d for d in canon.drift_instances if app_source[d["app_id"]] == "northstar_core"]
    ns_notes = [n for n in canon.drift_notes if app_source[n["app_id"]] == "northstar_core"]

    ns_app_cols = list(canon.applications[0].keys()) + ["extract_ts"]
    ns_drift_cols = list(canon.drift_instances[0].keys()) + ["extract_ts"]
    ns_note_cols = list(canon.drift_notes[0].keys()) + ["extract_ts"]
    ns_app_rows = [{**a, "rto_hours": _hours(a["rto_hours"]), "extract_ts": CURRENT_EXTRACT_TS} for a in ns_apps]
    # Dirty record: a test app that was never cleaned out of the legacy CMDB.
    ns_app_rows.append(
        {
            **{c: None for c in ns_app_cols},
            "app_id": "APP-TEST-01",
            "app_name": "Legacy Smoke Test App",
            "app_owner": "Unassigned",
            "lob": "Payments",
            "in_scope": "Y",
            "rto_score": 11,
            "rto_hours": "",
            "primary_dc": "Dallas",
            "extract_ts": CURRENT_EXTRACT_TS,
        }
    )
    _write_csv(out / "northstar_core" / "applications" / "applications_20260403.csv", ns_app_rows, ns_app_cols)
    _write_csv(
        out / "northstar_core" / "drift_instances" / "drift_instances_20260403.csv",
        [{**d, "rto_hours": _hours(d["rto_hours"]), "extract_ts": CURRENT_EXTRACT_TS} for d in ns_drift],
        ns_drift_cols,
    )
    _write_csv(
        out / "northstar_core" / "drift_notes" / "drift_notes_20260403.csv",
        [{**n, "extract_ts": CURRENT_EXTRACT_TS} for n in ns_notes],
        ns_note_cols,
    )

    # -------------------------------------------------------------- harbor_trust
    ht_app_cols = [
        "AppNumber", "ApplicationName", "BusinessOwner", "OwnerEmail", "OwnerTeam", "ResiliencyLead",
        "LineOfBusiness", "LifecycleStatus", "DeploymentStatus", "InComplianceScope", "RecoveryTimeHours",
        "PrimarySite", "SecondarySite", "EnvironmentTier", "HostingModel", "OSFamily", "LastDRTestDate",
        "LastDRTestOutcome", "ExtractedAt",
    ]
    ht_apps = []
    for a in canon.applications:
        if app_source[a["app_id"]] != "harbor_trust":
            continue
        ht_apps.append(
            {
                "AppNumber": source_app_id[a["app_id"]],
                "ApplicationName": a["app_name"],
                "BusinessOwner": a["app_owner"],
                "OwnerEmail": a["owner_email"],
                "OwnerTeam": a["owner_team"],
                "ResiliencyLead": a["resiliency_lead"],
                "LineOfBusiness": a["lob"],
                "LifecycleStatus": a["app_status"],
                "DeploymentStatus": a["deploy_status"],
                "InComplianceScope": "Yes" if a["in_scope"] == "Y" else "No",
                "RecoveryTimeHours": _hours(a["rto_hours"]),
                "PrimarySite": HARBOR_SITE_CODES[a["primary_dc"]],
                "SecondarySite": HARBOR_SITE_CODES[a["secondary_dc"]] if a["secondary_dc"] else "",
                "EnvironmentTier": a["env"],
                "HostingModel": a["hosting"],
                "OSFamily": a["os_family"],
                "LastDRTestDate": _us_date(a["last_test_date"]),
                "LastDRTestOutcome": a["last_test_result"],
                "ExtractedAt": CURRENT_EXTRACT_TS,
            }
        )
    rng.shuffle(ht_apps)
    _write_csv(out / "harbor_trust" / "applications" / "HT_APPLICATIONS_20260403.csv", ht_apps, ht_app_cols)

    ht_drift_cols = [
        "DriftRef", "AppNumber", "ProductCategory", "ProductName", "HostName", "ApprovedVersion",
        "DetectedVersion", "DriftState", "OpenedDate", "ClosedDate", "DaysOpen", "ExemptionRequested",
        "ExemptionDecision", "RootCause", "Environment", "ExtractedAt",
    ]

    def harbor_drift_row(d: dict[str, Any], extracted_at: str) -> dict[str, Any]:
        return {
            "DriftRef": source_drift_id[d["drift_id"]],
            "AppNumber": source_app_id[d["app_id"]],
            "ProductCategory": d["product_category"],
            "ProductName": PRODUCT_ALIASES[d["product"]][0],
            "HostName": d["instance_name"],
            "ApprovedVersion": d["approved_version"],
            "DetectedVersion": d["detected_version"],
            "DriftState": d["status"].upper(),
            "OpenedDate": _us_date(d["drift_open_date"]),
            "ClosedDate": _us_date(d["drift_close_date"]),
            "DaysOpen": d["drift_duration_days"],
            "ExemptionRequested": "Yes" if d["exemption_requested"] == "Y" else "No",
            "ExemptionDecision": d["exemption_result"] or "",
            "RootCause": d["root_cause"],
            "Environment": d["environment"],
            "ExtractedAt": extracted_at,
        }

    ht_current = [harbor_drift_row(drift_by_id[i], CURRENT_EXTRACT_TS) for i in harbor_drift]
    # Dirty records: two drift rows pointing at applications that don't exist in
    # the Harbor CMDB extract (orphans from a decommissioned subsidiary).
    for orphan_number, product in ((9901, "IBM MQ"), (9902, "Tomcat")):
        ht_current.append(
            {
                **harbor_drift_row(drift_by_id[harbor_drift[0]], CURRENT_EXTRACT_TS),
                "DriftRef": f"HTD-99{orphan_number}",
                "AppNumber": f"HT-{orphan_number}",
                "ProductName": PRODUCT_ALIASES[product][0],
                "ProductCategory": next(d["product_category"] for d in canon.drift_instances if d["product"] == product),
                "HostName": f"HTORPHAN{orphan_number}",
            }
        )
    rng.shuffle(ht_current)

    # The March 6 export of the same system, as it stood four weeks earlier: drift
    # opened after March 6 is not in it yet, items closed since then were still open,
    # and some exemption decisions made since then were still pending.
    prior_date = date.fromisoformat(HARBOR_PRIOR_EXTRACT_DATE)
    ht_prior = []
    changed = 0
    pending_flips = 0
    for drift_id in harbor_drift:
        d = dict(drift_by_id[drift_id])
        if d["drift_open_date"] > HARBOR_PRIOR_EXTRACT_DATE:
            continue
        if d["status"] == "Closed" and d["drift_close_date"] > HARBOR_PRIOR_EXTRACT_DATE:
            d["status"] = "Open"
            d["drift_close_date"] = None
            changed += 1
        elif d["exemption_result"] in ("Approved", "Denied") and pending_flips < 12:
            d["exemption_result"] = "Pending"
            pending_flips += 1
            changed += 1
        if d["status"] == "Open":
            d["drift_duration_days"] = (prior_date - date.fromisoformat(d["drift_open_date"])).days
        ht_prior.append(harbor_drift_row(d, HARBOR_PRIOR_EXTRACT_TS))
    rng.shuffle(ht_prior)
    _write_csv(out / "harbor_trust" / "drift_instances" / "HT_DRIFT_20260306.csv", ht_prior, ht_drift_cols)
    _write_csv(out / "harbor_trust" / "drift_instances" / "HT_DRIFT_20260403.csv", ht_current, ht_drift_cols)

    ht_note_cols = ["NoteRef", "DriftRef", "NoteDate", "Category", "AuthorRole", "Body", "Escalated", "ExtractedAt"]
    ht_notes = [
        {
            "NoteRef": source_note_id[n["note_id"]],
            "DriftRef": source_drift_id[n["drift_id"]],
            "NoteDate": _us_date(n["note_date"]),
            "Category": n["note_type"],
            "AuthorRole": n["author_role"],
            "Body": n["note_text"],
            "Escalated": "TRUE" if n["escalation_flag"] else "FALSE",
            "ExtractedAt": CURRENT_EXTRACT_TS,
        }
        for n in canon.drift_notes
        if app_source[n["app_id"]] == "harbor_trust"
    ]
    _write_csv(out / "harbor_trust" / "drift_notes" / "HT_DRIFT_NOTES_20260403.csv", ht_notes, ht_note_cols)

    # ------------------------------------------------------------ summit_capital
    sc_app_cols = [
        "asset_key", "asset_name", "owner", "owner_mail", "team", "resiliency_contact", "business_unit",
        "lifecycle", "deploy_state", "compliance_scope", "rto_minutes", "primary_location",
        "secondary_location", "environment", "hosting", "os", "dr_test_date", "dr_test_result", "exported_at",
    ]
    sc_apps = []
    for a in canon.applications:
        if app_source[a["app_id"]] != "summit_capital":
            continue
        sc_apps.append(
            {
                "asset_key": source_app_id[a["app_id"]],
                "asset_name": a["app_name"],
                "owner": a["app_owner"],
                "owner_mail": a["owner_email"],
                "team": a["owner_team"],
                "resiliency_contact": a["resiliency_lead"],
                "business_unit": a["lob"],
                "lifecycle": a["app_status"],
                "deploy_state": a["deploy_status"],
                "compliance_scope": 1 if a["in_scope"] == "Y" else 0,
                "rto_minutes": int(round(a["rto_hours"] * 60)),
                "primary_location": SUMMIT_SITE_CODES[a["primary_dc"]],
                "secondary_location": SUMMIT_SITE_CODES[a["secondary_dc"]] if a["secondary_dc"] else "none",
                "environment": a["env"],
                "hosting": a["hosting"],
                "os": a["os_family"],
                "dr_test_date": _compact_date(a["last_test_date"]),
                "dr_test_result": a["last_test_result"],
                "exported_at": CURRENT_EXTRACT_TS,
            }
        )
    rng.shuffle(sc_apps)
    _write_csv(out / "summit_capital" / "applications" / "assets_export_2026-04-03.csv", sc_apps, sc_app_cols)

    sc_drift_cols = [
        "event_id", "asset_key", "product_category", "product", "host", "baseline_version",
        "observed_version", "is_open", "opened_at", "closed_at", "exemption", "root_cause", "env", "exported_at",
    ]
    sc_drift = []
    for drift_id in summit_drift:
        d = drift_by_id[drift_id]
        sc_drift.append(
            {
                "event_id": source_drift_id[drift_id],
                "asset_key": source_app_id[d["app_id"]],
                "product_category": SUMMIT_CATEGORY_CODES[d["product_category"]],
                "product": PRODUCT_ALIASES[d["product"]][1],
                "host": d["instance_name"],
                "baseline_version": d["approved_version"],
                "observed_version": d["detected_version"],
                "is_open": "true" if d["status"] == "Open" else "false",
                "opened_at": _utc_timestamp(d["drift_open_date"], rng),
                "closed_at": _utc_timestamp(d["drift_close_date"], rng),
                "exemption": (d["exemption_result"] or "NONE").upper(),
                "root_cause": d["root_cause"],
                "env": d["environment"],
                "exported_at": CURRENT_EXTRACT_TS,
            }
        )
    # Dirty record: a mapped event whose scanner never reported a version.
    template = drift_by_id[summit_drift[-1]]
    sc_drift.append(
        {
            "event_id": "EVT-900001",
            "asset_key": source_app_id[template["app_id"]],
            "product_category": SUMMIT_CATEGORY_CODES[template["product_category"]],
            "product": PRODUCT_ALIASES[template["product"]][1],
            "host": template["instance_name"],
            "baseline_version": template["approved_version"],
            "observed_version": "",
            "is_open": "true",
            "opened_at": "2026-03-30T15:04:11Z",
            "closed_at": "",
            "exemption": "NONE",
            "root_cause": "Infrastructure constraint",
            "env": "Production",
            "exported_at": CURRENT_EXTRACT_TS,
        }
    )
    drift_xwalk.append(
        {"source_system": "summit_capital", "source_drift_id": "EVT-900001", "canonical_drift_id": "DR-2026-900001"}
    )
    rng.shuffle(sc_drift)
    _write_csv(out / "summit_capital" / "drift_instances" / "drift_events_export_2026-04-03.csv", sc_drift, sc_drift_cols)

    sc_note_cols = ["comment_id", "event_id", "created_at", "kind", "author_role", "comment", "escalated", "exported_at"]
    sc_notes = [
        {
            "comment_id": source_note_id[n["note_id"]],
            "event_id": source_drift_id[n["drift_id"]],
            "created_at": _utc_timestamp(n["note_date"], rng),
            "kind": SUMMIT_NOTE_KINDS[n["note_type"]],
            "author_role": n["author_role"],
            "comment": n["note_text"],
            "escalated": 1 if n["escalation_flag"] else 0,
            "exported_at": CURRENT_EXTRACT_TS,
        }
        for n in canon.drift_notes
        if app_source[n["app_id"]] == "summit_capital"
    ]
    # Dirty records: the ticketing export double-wrote a handful of comments.
    duplicates = rng.sample(sc_notes, SUMMIT_DUPLICATE_COMMENTS)
    sc_notes.extend(dict(row) for row in duplicates)
    rng.shuffle(sc_notes)
    _write_csv(out / "summit_capital" / "drift_notes" / "event_comments_export_2026-04-03.csv", sc_notes, sc_note_cols)

    # ------------------------------------------------------- reference / master data
    ref = out / "reference"
    _write_csv(ref / "app_crosswalk.csv", app_xwalk, ["source_system", "source_app_id", "canonical_app_id"])
    _write_csv(ref / "drift_crosswalk.csv", drift_xwalk, ["source_system", "source_drift_id", "canonical_drift_id"])

    product_rows = []
    category_of = {d["product"]: d["product_category"] for d in canon.drift_instances}
    for canonical, (harbor_name, summit_code) in sorted(PRODUCT_ALIASES.items()):
        for source, alias in (("northstar_core", canonical), ("harbor_trust", harbor_name), ("summit_capital", summit_code)):
            product_rows.append(
                {
                    "source_system": source,
                    "source_product": alias,
                    "canonical_product": canonical,
                    "canonical_category": category_of[canonical],
                }
            )
    _write_csv(ref / "product_alias.csv", product_rows,
               ["source_system", "source_product", "canonical_product", "canonical_category"])

    _write_csv(
        ref / "rto_scale.csv",
        [{"rto_score": s, "rto_hours": _hours(h), "criticality": c} for s, (h, c) in RTO_SCALE.items()],
        ["rto_score", "rto_hours", "criticality"],
    )

    code_rows: list[dict[str, Any]] = []

    def code(source: str, domain: str, source_value: str, canonical_value: str | None) -> None:
        code_rows.append(
            {"source_system": source, "domain": domain, "source_value": source_value, "canonical_value": canonical_value}
        )

    for city in sorted(HARBOR_SITE_CODES):
        code("northstar_core", "data_center", city, city)
        code("harbor_trust", "data_center", HARBOR_SITE_CODES[city], city)
        code("summit_capital", "data_center", SUMMIT_SITE_CODES[city], city)
    code("summit_capital", "data_center", "none", None)
    for value in ("Open", "Closed"):
        code("northstar_core", "status", value, value)
        code("harbor_trust", "status", value.upper(), value)
    code("summit_capital", "status", "true", "Open")
    code("summit_capital", "status", "false", "Closed")
    for value in ("Y", "N"):
        code("northstar_core", "yes_no", value, value)
    code("harbor_trust", "yes_no", "Yes", "Y")
    code("harbor_trust", "yes_no", "No", "N")
    code("summit_capital", "yes_no", "1", "Y")
    code("summit_capital", "yes_no", "0", "N")
    code("northstar_core", "escalation", "1", "true")
    code("northstar_core", "escalation", "0", "false")
    code("harbor_trust", "escalation", "TRUE", "true")
    code("harbor_trust", "escalation", "FALSE", "false")
    code("summit_capital", "escalation", "1", "true")
    code("summit_capital", "escalation", "0", "false")
    # Summit stores request and decision in one field, so it maps to two canonical domains.
    code("summit_capital", "exemption_requested", "NONE", "N")
    code("summit_capital", "exemption_result", "NONE", None)
    for result in ("Approved", "Pending", "Denied"):
        code("summit_capital", "exemption_requested", result.upper(), "Y")
        code("summit_capital", "exemption_result", result.upper(), result)
    for category, summit_code in sorted(SUMMIT_CATEGORY_CODES.items()):
        code("summit_capital", "product_category", summit_code, category)
    for note_type, kind in sorted(SUMMIT_NOTE_KINDS.items()):
        code("northstar_core", "note_type", note_type, note_type)
        code("harbor_trust", "note_type", note_type, note_type)
        code("summit_capital", "note_type", kind, note_type)
    _write_csv(ref / "code_map.csv", code_rows, ["source_system", "domain", "source_value", "canonical_value"])

    counts = {
        "northstar_core.applications": len(ns_app_rows),
        "northstar_core.drift_instances": len(ns_drift),
        "northstar_core.drift_notes": len(ns_notes),
        "harbor_trust.applications": len(ht_apps),
        "harbor_trust.drift_instances (prior + current)": len(ht_prior) + len(ht_current),
        "harbor_trust.drift_notes": len(ht_notes),
        "summit_capital.applications": len(sc_apps),
        "summit_capital.drift_instances": len(sc_drift),
        "summit_capital.drift_notes": len(sc_notes),
        "harbor_trust.prior_snapshot_rows": len(ht_prior),
        "harbor_trust.prior_snapshot_changed_rows": changed,
    }
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed-db", type=Path, default=DEFAULT_SEED_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    for name, count in build(args.seed_db, args.output).items():
        print(f"{name:50s} {count:>6}")


if __name__ == "__main__":
    main()
