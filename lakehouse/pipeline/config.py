"""Names and paths shared by every lakehouse notebook and test.

One Unity Catalog catalog holds four schemas:

    bronze  raw rows exactly as each source system delivered them (all STRING)
    ref     master data: ID crosswalks, code maps, product aliases, RTO scale
    silver  cleaned, conformed, deduplicated rows on shared IDs
    gold    analytics tables, the agent-facing view, and KPI tables
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_CATALOG = "drift_demo"
LANDING_VOLUME = "landing"

SOURCE_SYSTEMS: tuple[str, ...] = ("northstar_core", "harbor_trust", "summit_capital")
ENTITIES: tuple[str, ...] = ("applications", "drift_instances", "drift_notes")

REFERENCE_TABLES: dict[str, str] = {
    # table name -> CSV file under source_data/reference, plus its typed schema
    "app_crosswalk": "source_system STRING, source_app_id STRING, canonical_app_id STRING",
    "drift_crosswalk": "source_system STRING, source_drift_id STRING, canonical_drift_id STRING",
    "product_alias": "source_system STRING, source_product STRING, canonical_product STRING, canonical_category STRING",
    "code_map": "source_system STRING, domain STRING, source_value STRING, canonical_value STRING",
    "rto_scale": "rto_score INT, rto_hours DOUBLE, criticality STRING",
}


@dataclass(frozen=True)
class Schemas:
    catalog: str

    @property
    def bronze(self) -> str:
        return f"{self.catalog}.bronze"

    @property
    def ref(self) -> str:
        return f"{self.catalog}.ref"

    @property
    def silver(self) -> str:
        return f"{self.catalog}.silver"

    @property
    def gold(self) -> str:
        return f"{self.catalog}.gold"

    @property
    def landing_root(self) -> str:
        return f"/Volumes/{self.catalog}/bronze/{LANDING_VOLUME}"

    def as_template_vars(self) -> dict[str, str]:
        return {"catalog": self.catalog, "bronze": self.bronze, "ref": self.ref, "silver": self.silver, "gold": self.gold}


def bronze_table(source_system: str, entity: str) -> str:
    """Bronze keeps one table per source system and entity, e.g. harbor_trust_drift_instances."""

    return f"{source_system}_{entity}"
