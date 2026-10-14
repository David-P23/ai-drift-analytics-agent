"""Test helper: rebuild the canonical tables from the three simulated source exports.

This is a small pandas version of the mapping rules, used only to prove that
lakehouse/source_data is lossless and to feed the local Spark gold test. The
real silver layer is the PySpark notebook lakehouse/notebooks/02_silver.py.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DATA = ROOT / "lakehouse" / "source_data"
SEED_DB = ROOT / "lakehouse" / "seed" / "northstar_v1_normalized.sqlite"


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _read_entity(source: str, entity: str) -> pd.DataFrame:
    frames = [_read(p) for p in sorted((SOURCE_DATA / source / entity).glob("*.csv"))]
    return pd.concat(frames, ignore_index=True)


def _codes(source: str, domain: str) -> dict[str, str | None]:
    codes = _read(SOURCE_DATA / "reference" / "code_map.csv")
    sub = codes[(codes.source_system == source) & (codes.domain == domain)]
    return {row.source_value: (row.canonical_value or None) for row in sub.itertuples()}


def _blank_to_none(series: pd.Series) -> pd.Series:
    return series.where(series != "", None)


def reconstruct() -> dict[str, pd.DataFrame]:
    """Return canonical applications, drift_instances, drift_notes and a quarantine log."""

    ref = SOURCE_DATA / "reference"
    app_x = _read(ref / "app_crosswalk.csv")
    drift_x = _read(ref / "drift_crosswalk.csv")
    products = _read(ref / "product_alias.csv")
    rto = _read(ref / "rto_scale.csv")
    hours_to_score = {float(h): int(s) for s, h in zip(rto.rto_score, rto.rto_hours)}

    quarantine: list[dict[str, str]] = []

    # ---------------------------------------------------------------- applications
    apps = []
    ns = _read_entity("northstar_core", "applications")
    apps.append(
        pd.DataFrame(
            {
                "source_system": "northstar_core",
                "source_app_id": ns.app_id,
                "app_name": ns.app_name,
                "app_owner": ns.app_owner,
                "owner_email": ns.owner_email,
                "owner_team": ns.owner_team,
                "resiliency_lead": ns.resiliency_lead,
                "lob": ns.lob,
                "app_status": ns.app_status,
                "deploy_status": ns.deploy_status,
                "in_scope": ns.in_scope.map(_codes("northstar_core", "yes_no")),
                "rto_score": pd.to_numeric(ns.rto_score),
                "primary_dc": ns.primary_dc.map(_codes("northstar_core", "data_center")),
                "secondary_dc": _blank_to_none(ns.secondary_dc).map(_codes("northstar_core", "data_center")),
                "env": ns.env,
                "hosting": ns.hosting,
                "os_family": ns.os_family,
                "last_test_date": _blank_to_none(ns.last_test_date),
                "last_test_result": ns.last_test_result,
            }
        )
    )
    ht = _read_entity("harbor_trust", "applications")
    apps.append(
        pd.DataFrame(
            {
                "source_system": "harbor_trust",
                "source_app_id": ht.AppNumber,
                "app_name": ht.ApplicationName,
                "app_owner": ht.BusinessOwner,
                "owner_email": ht.OwnerEmail,
                "owner_team": ht.OwnerTeam,
                "resiliency_lead": ht.ResiliencyLead,
                "lob": ht.LineOfBusiness,
                "app_status": ht.LifecycleStatus,
                "deploy_status": ht.DeploymentStatus,
                "in_scope": ht.InComplianceScope.map(_codes("harbor_trust", "yes_no")),
                "rto_score": ht.RecoveryTimeHours.astype(float).map(hours_to_score),
                "primary_dc": ht.PrimarySite.map(_codes("harbor_trust", "data_center")),
                "secondary_dc": _blank_to_none(ht.SecondarySite).map(_codes("harbor_trust", "data_center")),
                "env": ht.EnvironmentTier,
                "hosting": ht.HostingModel,
                "os_family": ht.OSFamily,
                "last_test_date": pd.to_datetime(ht.LastDRTestDate, format="%m/%d/%Y").dt.strftime("%Y-%m-%d"),
                "last_test_result": ht.LastDRTestOutcome,
            }
        )
    )
    sc = _read_entity("summit_capital", "applications")
    apps.append(
        pd.DataFrame(
            {
                "source_system": "summit_capital",
                "source_app_id": sc.asset_key,
                "app_name": sc.asset_name,
                "app_owner": sc.owner,
                "owner_email": sc.owner_mail,
                "owner_team": sc.team,
                "resiliency_lead": sc.resiliency_contact,
                "lob": sc.business_unit,
                "app_status": sc.lifecycle,
                "deploy_status": sc.deploy_state,
                "in_scope": sc.compliance_scope.map(_codes("summit_capital", "yes_no")),
                "rto_score": (sc.rto_minutes.astype(float) / 60).map(hours_to_score),
                "primary_dc": sc.primary_location.map(_codes("summit_capital", "data_center")),
                "secondary_dc": sc.secondary_location.map(_codes("summit_capital", "data_center")),
                "env": sc.environment,
                "hosting": sc.hosting,
                "os_family": sc.os,
                "last_test_date": pd.to_datetime(sc.dr_test_date, format="%Y%m%d").dt.strftime("%Y-%m-%d"),
                "last_test_result": sc.dr_test_result,
            }
        )
    )
    apps_df = pd.concat(apps, ignore_index=True)
    apps_df = apps_df.merge(
        app_x, how="left", on=["source_system", "source_app_id"]
    ).rename(columns={"canonical_app_id": "app_id"})
    bad_rto = ~apps_df.rto_score.between(1, 10)
    for row in apps_df[bad_rto].itertuples():
        quarantine.append({"entity": "applications", "source_system": row.source_system,
                           "source_record_id": row.source_app_id, "reason": "rto_score outside 1-10"})
    apps_df = apps_df[~bad_rto & apps_df.app_id.notna()].copy()
    apps_df["rto_score"] = apps_df.rto_score.astype(int)
    apps_df["rto_hours"] = apps_df.rto_score.map({s: h for h, s in hours_to_score.items()})

    # ------------------------------------------------------------- drift instances
    def product_map(source: str) -> tuple[dict[str, str], dict[str, str]]:
        sub = products[products.source_system == source]
        return dict(zip(sub.source_product, sub.canonical_product)), dict(zip(sub.source_product, sub.canonical_category))

    drift = []
    ns = _read_entity("northstar_core", "drift_instances")
    prod, _ = product_map("northstar_core")
    drift.append(
        pd.DataFrame(
            {
                "source_system": "northstar_core",
                "source_drift_id": ns.drift_id,
                "source_app_id": ns.app_id,
                "product_category": ns.product_category,
                "product": ns["product"].map(prod),
                "instance_name": ns.instance_name,
                "approved_version": ns.approved_version,
                "detected_version": ns.detected_version,
                "status": ns.status.map(_codes("northstar_core", "status")),
                "drift_open_date": ns.drift_open_date,
                "drift_close_date": _blank_to_none(ns.drift_close_date),
                "exemption_requested": ns.exemption_requested.map(_codes("northstar_core", "yes_no")),
                "exemption_result": _blank_to_none(ns.exemption_result),
                "root_cause": ns.root_cause,
                "environment": ns.environment,
                "extracted_at": ns.extract_ts,
            }
        )
    )
    ht = _read_entity("harbor_trust", "drift_instances")
    prod, _ = product_map("harbor_trust")
    us_date = lambda s: pd.to_datetime(_blank_to_none(s), format="%m/%d/%Y").dt.strftime("%Y-%m-%d")  # noqa: E731
    drift.append(
        pd.DataFrame(
            {
                "source_system": "harbor_trust",
                "source_drift_id": ht.DriftRef,
                "source_app_id": ht.AppNumber,
                "product_category": ht.ProductCategory,
                "product": ht.ProductName.map(prod),
                "instance_name": ht.HostName,
                "approved_version": ht.ApprovedVersion,
                "detected_version": ht.DetectedVersion,
                "status": ht.DriftState.map(_codes("harbor_trust", "status")),
                "drift_open_date": us_date(ht.OpenedDate),
                "drift_close_date": us_date(ht.ClosedDate),
                "exemption_requested": ht.ExemptionRequested.map(_codes("harbor_trust", "yes_no")),
                "exemption_result": _blank_to_none(ht.ExemptionDecision),
                "root_cause": ht.RootCause,
                "environment": ht.Environment,
                "extracted_at": ht.ExtractedAt,
            }
        )
    )
    sc = _read_entity("summit_capital", "drift_instances")
    prod, _ = product_map("summit_capital")
    drift.append(
        pd.DataFrame(
            {
                "source_system": "summit_capital",
                "source_drift_id": sc.event_id,
                "source_app_id": sc.asset_key,
                "product_category": sc.product_category.map(_codes("summit_capital", "product_category")),
                "product": sc["product"].map(prod),
                "instance_name": sc.host,
                "approved_version": sc.baseline_version,
                "detected_version": _blank_to_none(sc.observed_version),
                "status": sc.is_open.map(_codes("summit_capital", "status")),
                "drift_open_date": _blank_to_none(sc.opened_at).str[:10],
                "drift_close_date": _blank_to_none(sc.closed_at).str[:10],
                "exemption_requested": sc.exemption.map(_codes("summit_capital", "exemption_requested")),
                "exemption_result": sc.exemption.map(_codes("summit_capital", "exemption_result")),
                "root_cause": sc.root_cause,
                "environment": sc.env,
                "extracted_at": sc.exported_at,
            }
        )
    )
    drift_df = pd.concat(drift, ignore_index=True)
    # Latest extract wins (Harbor sent two snapshots).
    drift_df = drift_df.sort_values("extracted_at").drop_duplicates(["source_system", "source_drift_id"], keep="last")
    drift_df = drift_df.merge(app_x, how="left", on=["source_system", "source_app_id"]).rename(
        columns={"canonical_app_id": "app_id"}
    )
    drift_df = drift_df.merge(drift_x, how="left", on=["source_system", "source_drift_id"]).rename(
        columns={"canonical_drift_id": "drift_id"}
    )
    orphan = drift_df.app_id.isna() | ~drift_df.app_id.isin(apps_df.app_id)
    missing_version = drift_df.detected_version.isna()
    for row in drift_df[orphan].itertuples():
        quarantine.append({"entity": "drift_instances", "source_system": row.source_system,
                           "source_record_id": row.source_drift_id, "reason": "unknown application"})
    for row in drift_df[~orphan & missing_version].itertuples():
        quarantine.append({"entity": "drift_instances", "source_system": row.source_system,
                           "source_record_id": row.source_drift_id, "reason": "missing detected_version"})
    drift_df = drift_df[~orphan & ~missing_version].drop(columns=["source_app_id"])

    # ------------------------------------------------------------------ drift notes
    notes = []
    ns = _read_entity("northstar_core", "drift_notes")
    notes.append(
        pd.DataFrame(
            {
                "source_system": "northstar_core",
                "note_id": ns.note_id,
                "source_drift_id": ns.drift_id,
                "note_date": ns.note_date,
                "note_type": ns.note_type.map(_codes("northstar_core", "note_type")),
                "author_role": ns.author_role,
                "note_text": ns.note_text,
                "escalation_flag": ns.escalation_flag.map(_codes("northstar_core", "escalation")) == "true",
                "extracted_at": ns.extract_ts,
            }
        )
    )
    ht = _read_entity("harbor_trust", "drift_notes")
    notes.append(
        pd.DataFrame(
            {
                "source_system": "harbor_trust",
                "note_id": ht.NoteRef,
                "source_drift_id": ht.DriftRef,
                "note_date": pd.to_datetime(ht.NoteDate, format="%m/%d/%Y").dt.strftime("%Y-%m-%d"),
                "note_type": ht.Category.map(_codes("harbor_trust", "note_type")),
                "author_role": ht.AuthorRole,
                "note_text": ht.Body,
                "escalation_flag": ht.Escalated.map(_codes("harbor_trust", "escalation")) == "true",
                "extracted_at": ht.ExtractedAt,
            }
        )
    )
    sc = _read_entity("summit_capital", "drift_notes")
    notes.append(
        pd.DataFrame(
            {
                "source_system": "summit_capital",
                "note_id": sc.comment_id,
                "source_drift_id": sc.event_id,
                "note_date": sc.created_at.str[:10],
                "note_type": sc.kind.map(_codes("summit_capital", "note_type")),
                "author_role": sc.author_role,
                "note_text": sc.comment,
                "escalation_flag": sc.escalated.map(_codes("summit_capital", "escalation")) == "true",
                "extracted_at": sc.exported_at,
            }
        )
    )
    notes_df = pd.concat(notes, ignore_index=True).drop_duplicates()
    notes_df = notes_df.merge(
        drift_x.rename(columns={"source_drift_id": "source_drift_id"}),
        how="left",
        on=["source_system", "source_drift_id"],
    ).rename(columns={"canonical_drift_id": "drift_id"})
    notes_df = notes_df[notes_df.drift_id.isin(drift_df.drift_id)].drop(columns=["source_drift_id"])

    return {
        "applications": apps_df.reset_index(drop=True),
        "drift_instances": drift_df.reset_index(drop=True),
        "drift_notes": notes_df.reset_index(drop=True),
        "quarantine": pd.DataFrame(quarantine),
    }
