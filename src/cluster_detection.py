"""Rolling-window drift cluster detection."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd


GROUPING_OPTIONS: dict[str, list[str]] = {
    "Product update": ["product", "approved_version"],
    "Product only": ["product"],
    "Product/version pair": ["product", "approved_version", "detected_version"],
    "Product update + data center": ["product", "approved_version", "data_center"],
}


def summarize_cluster_periods(
    rows: Iterable[dict[str, Any]],
    *,
    min_drift_count: int,
    window_days: int,
    group_fields: list[str],
    reference_date: date | datetime | str | None = None,
) -> dict[str, Any]:
    """Compare the selected drift window with the immediately prior same-length window."""

    if min_drift_count < 2:
        raise ValueError("min_drift_count must be at least 2.")
    if window_days < 1:
        raise ValueError("window_days must be at least 1.")
    if not group_fields:
        raise ValueError("At least one grouping field is required.")

    frame = pd.DataFrame(list(rows))
    empty = _empty_period_summary(window_days)
    if frame.empty:
        return empty

    required = set(group_fields) | {"drift_detected_on", "app_name", "rto_score", "status"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Cluster source rows are missing required columns: {', '.join(missing)}.")

    frame = frame.copy()
    frame["detected_at"] = pd.to_datetime(frame["drift_detected_on"], errors="coerce").dt.normalize()
    frame = frame.dropna(subset=["detected_at"])
    if frame.empty:
        return empty

    reference = pd.to_datetime(reference_date, errors="coerce") if reference_date is not None else frame["detected_at"].max()
    if pd.isna(reference):
        reference = frame["detected_at"].max()
    reference = pd.Timestamp(reference).normalize()

    current_start = reference - pd.Timedelta(days=window_days - 1)
    prior_end = current_start - pd.Timedelta(days=1)
    prior_start = prior_end - pd.Timedelta(days=window_days - 1)

    current_frame = frame[(frame["detected_at"] >= current_start) & (frame["detected_at"] <= reference)]
    prior_frame = frame[(frame["detected_at"] >= prior_start) & (frame["detected_at"] <= prior_end)]

    current = _summarize_period_frame(current_frame, min_drift_count, window_days, group_fields)
    prior = _summarize_period_frame(prior_frame, min_drift_count, window_days, group_fields)
    return {
        "window_days": window_days,
        "current": {
            **current,
            "start": current_start.date().isoformat(),
            "end": reference.date().isoformat(),
        },
        "prior": {
            **prior,
            "start": prior_start.date().isoformat(),
            "end": prior_end.date().isoformat(),
        },
    }


def detect_drift_clusters(
    rows: Iterable[dict[str, Any]],
    *,
    min_drift_count: int,
    window_days: int,
    group_fields: list[str],
) -> list[dict[str, Any]]:
    """Find non-overlapping drift waves that cross a count threshold in a rolling time window."""

    if min_drift_count < 2:
        raise ValueError("min_drift_count must be at least 2.")
    if window_days < 1:
        raise ValueError("window_days must be at least 1.")
    if not group_fields:
        raise ValueError("At least one grouping field is required.")

    frame = pd.DataFrame(list(rows))
    if frame.empty:
        return []

    required = set(group_fields) | {"drift_detected_on", "app_name", "rto_score", "status"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Cluster source rows are missing required columns: {', '.join(missing)}.")

    frame = frame.copy()
    frame["detected_at"] = pd.to_datetime(frame["drift_detected_on"], errors="coerce")
    frame = frame.dropna(subset=["detected_at"])
    if frame.empty:
        return []

    for field in group_fields:
        frame[field] = frame[field].fillna("Unassigned").astype(str)

    clusters: list[dict[str, Any]] = []
    for group_key, group in frame.sort_values("detected_at").groupby(group_fields, dropna=False):
        group = group.sort_values("detected_at").reset_index(drop=True)
        start_index = 0
        while start_index < len(group):
            start_date = group.loc[start_index, "detected_at"]
            window_end = start_date + timedelta(days=window_days - 1)
            window = group[(group["detected_at"] >= start_date) & (group["detected_at"] <= window_end)]
            if len(window) < min_drift_count:
                start_index += 1
                continue

            cluster = _summarize_cluster(
                window,
                group_key=group_key,
                group_fields=group_fields,
                window_days=window_days,
            )
            clusters.append(cluster)
            start_index = int(window.index.max()) + 1

    return sorted(
        clusters,
        key=lambda row: (
            row["drift_count"],
            row["critical_high_count"],
            row["first_detected"],
        ),
        reverse=True,
    )


def _empty_period_summary(window_days: int) -> dict[str, Any]:
    return {
        "window_days": window_days,
        "current": {
            "start": None,
            "end": None,
            "drift_count": 0,
            "cluster_count": 0,
            "critical_high_count": 0,
            "largest_cluster": 0,
        },
        "prior": {
            "start": None,
            "end": None,
            "drift_count": 0,
            "cluster_count": 0,
            "critical_high_count": 0,
            "largest_cluster": 0,
        },
    }


def _summarize_period_frame(
    frame: pd.DataFrame,
    min_drift_count: int,
    window_days: int,
    group_fields: list[str],
) -> dict[str, int]:
    records = frame.drop(columns=["detected_at"], errors="ignore").to_dict("records")
    clusters = detect_drift_clusters(
        records,
        min_drift_count=min_drift_count,
        window_days=window_days,
        group_fields=group_fields,
    )
    return {
        "drift_count": int(len(frame)),
        "cluster_count": int(len(clusters)),
        "critical_high_count": int((pd.to_numeric(frame.get("rto_score"), errors="coerce") <= 4).sum()),
        "largest_cluster": int(max((cluster["drift_count"] for cluster in clusters), default=0)),
    }


def _summarize_cluster(
    window: pd.DataFrame,
    *,
    group_key: Any,
    group_fields: list[str],
    window_days: int,
) -> dict[str, Any]:
    if not isinstance(group_key, tuple):
        group_key = (group_key,)
    group_values = {field: str(value) for field, value in zip(group_fields, group_key, strict=False)}
    first_detected = window["detected_at"].min()
    last_detected = window["detected_at"].max()
    unique_apps = sorted(window["app_name"].dropna().astype(str).unique())
    data_centers = sorted(window.get("data_center", pd.Series(dtype=object)).dropna().astype(str).unique())

    cluster: dict[str, Any] = {
        **group_values,
        "cluster_key": " | ".join(group_values.values()),
        "drift_count": int(len(window)),
        "unique_app_count": int(len(unique_apps)),
        "critical_high_count": int((pd.to_numeric(window["rto_score"], errors="coerce") <= 4).sum()),
        "open_count": int((window["status"].astype(str).str.lower() == "open").sum()),
        "first_detected": first_detected.date().isoformat(),
        "last_detected": last_detected.date().isoformat(),
        "span_days": int((last_detected - first_detected).days),
        "window_days": window_days,
        "sample_apps": ", ".join(unique_apps[:5]),
        "data_centers": ", ".join(data_centers[:5]),
    }
    return cluster
