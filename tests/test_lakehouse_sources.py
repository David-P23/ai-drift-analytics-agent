"""The simulated source systems must be deterministic and lossless."""

from __future__ import annotations

import filecmp
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from lakehouse import generate_sources
from lakehouse.pipeline import contracts
from tests.lakehouse_reference import SEED_DB, SOURCE_DATA, reconstruct


def _nulls_as_none(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.astype(object)
    return frame.where(frame.notna(), None)


@pytest.fixture(scope="module")
def rebuilt() -> dict[str, pd.DataFrame]:
    return reconstruct()


@pytest.fixture(scope="module")
def seed() -> dict[str, pd.DataFrame]:
    conn = sqlite3.connect(str(SEED_DB))
    try:
        return {
            name: pd.read_sql(f"SELECT * FROM {name}", conn)
            for name in ("applications", "drift_instances", "drift_notes")
        }
    finally:
        conn.close()


def test_generator_is_deterministic_and_matches_committed_files(tmp_path: Path) -> None:
    generate_sources.build(SEED_DB, tmp_path)
    committed = sorted(p.relative_to(SOURCE_DATA) for p in SOURCE_DATA.rglob("*.csv"))
    regenerated = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*.csv"))
    assert committed == regenerated
    for rel in committed:
        assert filecmp.cmp(SOURCE_DATA / rel, tmp_path / rel, shallow=False), f"{rel} differs from a fresh build"


def test_each_source_uses_its_own_ids_and_formats() -> None:
    harbor = pd.read_csv(SOURCE_DATA / "harbor_trust/drift_instances/HT_DRIFT_20260403.csv", dtype=str)
    summit = pd.read_csv(SOURCE_DATA / "summit_capital/applications/assets_export_2026-04-03.csv", dtype=str)
    assert harbor.DriftRef.str.startswith("HTD-").all()
    assert harbor.OpenedDate.str.match(r"^\d{2}/\d{2}/\d{4}$").all()
    assert summit.asset_key.str.match(r"^\d{5}$").all()
    assert "rto_score" not in summit.columns and "rto_minutes" in summit.columns


def test_applications_round_trip(rebuilt, seed) -> None:
    columns = [c for c, _ in contracts.SILVER_APPLICATIONS if c not in {"source_system", "source_app_id", "extracted_at"}]
    got = rebuilt["applications"][columns].sort_values("app_id").reset_index(drop=True)
    want = seed["applications"][columns].sort_values("app_id").reset_index(drop=True)
    assert len(got) == contracts.EXPECTED_ROWS["silver.applications"]
    pd.testing.assert_frame_equal(_nulls_as_none(got), _nulls_as_none(want), check_dtype=False)


def test_drift_instances_round_trip(rebuilt, seed) -> None:
    columns = [
        c for c, _ in contracts.SILVER_DRIFT_INSTANCES if c not in {"source_system", "source_drift_id", "extracted_at"}
    ]
    got = rebuilt["drift_instances"][columns].sort_values("drift_id").reset_index(drop=True)
    want = seed["drift_instances"][columns].sort_values("drift_id").reset_index(drop=True)
    assert len(got) == contracts.EXPECTED_ROWS["silver.drift_instances"]
    pd.testing.assert_frame_equal(_nulls_as_none(got), _nulls_as_none(want), check_dtype=False)


def test_drift_notes_round_trip_including_same_day_order(rebuilt, seed) -> None:
    got = rebuilt["drift_notes"].sort_values(["drift_id", "note_date", "note_id"])
    want = seed["drift_notes"].assign(escalation_flag=lambda d: d.escalation_flag == 1)
    want = want.sort_values(["drift_id", "note_date", "note_id"])
    assert len(got) == contracts.EXPECTED_ROWS["silver.drift_notes"]
    fields = ["drift_id", "note_date", "note_type", "author_role", "note_text", "escalation_flag"]
    pd.testing.assert_frame_equal(
        got[fields].reset_index(drop=True), want[fields].reset_index(drop=True), check_dtype=False
    )


def test_injected_dirty_records_are_caught(rebuilt) -> None:
    quarantine = rebuilt["quarantine"]
    assert quarantine.groupby("entity").size().to_dict() == contracts.EXPECTED_QUARANTINE_BY_ENTITY
    assert len(quarantine) == contracts.EXPECTED_ROWS["silver.quarantine"]


def test_every_bronze_row_is_accounted_for() -> None:
    """bronze rows = silver rows + quarantined records + superseded copies, per entity."""

    for entity in ("applications", "drift_instances", "drift_notes"):
        bronze_rows = sum(len(pd.read_csv(p, dtype=str)) for p in SOURCE_DATA.glob(f"*/{entity}/*.csv"))
        expected = (
            contracts.EXPECTED_ROWS[f"silver.{entity}"]
            + contracts.EXPECTED_QUARANTINE_BY_ENTITY.get(entity, 0)
            + contracts.EXPECTED_SUPERSEDED[entity]
        )
        assert bronze_rows == expected, entity
