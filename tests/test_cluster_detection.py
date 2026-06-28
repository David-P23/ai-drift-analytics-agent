from __future__ import annotations

import pytest

from src.cluster_detection import detect_drift_clusters, summarize_cluster_periods


def _row(app: str, product: str, approved: str, detected_on: str, rto: int = 3) -> dict[str, object]:
    return {
        "finding_id": f"{app}-{detected_on}",
        "app_id": app,
        "drift_id": f"DR-{app}-{detected_on}",
        "app_name": app,
        "product": product,
        "approved_version": approved,
        "detected_version": "old",
        "data_center": "DAL-1",
        "rto_score": rto,
        "status": "Open",
        "drift_detected_on": detected_on,
    }


def test_detects_product_update_cluster_inside_window() -> None:
    rows = [
        _row("Billing API", "Billing", "2026.04", "2026-01-01", 2),
        _row("Invoice UI", "Billing", "2026.04", "2026-01-05", 4),
        _row("Ledger Sync", "Billing", "2026.04", "2026-01-10", 6),
        _row("Claims API", "Claims", "2026.04", "2026-01-02", 2),
    ]

    clusters = detect_drift_clusters(
        rows,
        min_drift_count=3,
        window_days=10,
        group_fields=["product", "approved_version"],
    )

    assert len(clusters) == 1
    assert clusters[0]["cluster_key"] == "Billing | 2026.04"
    assert clusters[0]["drift_count"] == 3
    assert clusters[0]["critical_high_count"] == 2
    assert clusters[0]["first_detected"] == "2026-01-01"
    assert clusters[0]["last_detected"] == "2026-01-10"


def test_no_cluster_when_threshold_is_not_met() -> None:
    rows = [
        _row("Billing API", "Billing", "2026.04", "2026-01-01"),
        _row("Invoice UI", "Billing", "2026.04", "2026-02-15"),
    ]

    clusters = detect_drift_clusters(
        rows,
        min_drift_count=2,
        window_days=10,
        group_fields=["product", "approved_version"],
    )

    assert clusters == []


def test_window_days_are_inclusive_calendar_days() -> None:
    rows = [
        _row("Billing API", "Billing", "2026.04", "2026-01-01"),
        _row("Invoice UI", "Billing", "2026.04", "2026-01-31"),
    ]

    clusters = detect_drift_clusters(
        rows,
        min_drift_count=2,
        window_days=30,
        group_fields=["product", "approved_version"],
    )

    assert clusters == []


def test_summarizes_current_window_against_prior_window() -> None:
    rows = [
        _row("Prior A", "Billing", "2026.04", "2026-01-13", 2),
        _row("Prior B", "Billing", "2026.04", "2026-01-18", 5),
        _row("Current A", "Billing", "2026.04", "2026-01-23", 2),
        _row("Current B", "Billing", "2026.04", "2026-01-27", 3),
        _row("Current C", "Billing", "2026.04", "2026-01-31", 6),
    ]

    summary = summarize_cluster_periods(
        rows,
        min_drift_count=3,
        window_days=10,
        group_fields=["product", "approved_version"],
    )

    assert summary["current"]["start"] == "2026-01-22"
    assert summary["current"]["end"] == "2026-01-31"
    assert summary["current"]["drift_count"] == 3
    assert summary["current"]["cluster_count"] == 1
    assert summary["current"]["critical_high_count"] == 2
    assert summary["current"]["largest_cluster"] == 3
    assert summary["prior"]["start"] == "2026-01-12"
    assert summary["prior"]["end"] == "2026-01-21"
    assert summary["prior"]["drift_count"] == 2
    assert summary["prior"]["cluster_count"] == 0


def test_rejects_invalid_controls() -> None:
    with pytest.raises(ValueError):
        detect_drift_clusters([], min_drift_count=1, window_days=10, group_fields=["product"])

    with pytest.raises(ValueError):
        detect_drift_clusters([], min_drift_count=2, window_days=0, group_fields=["product"])
