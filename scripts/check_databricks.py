"""Check that the drift agent works end to end on Databricks.

Run from the repo root once the lakehouse job has built gold:

    python scripts/check_databricks.py

It reads the same settings the app uses (.env or environment variables), connects
to the SQL warehouse, and checks, in order:

1. settings and connector are in place
2. the warehouse answers (a stopped serverless warehouse can take a few seconds)
3. <catalog>.gold.applications has the 23 columns the agent was built against
4. 475 findings, scanned on 2026-04-03 (the as-of date the aging KPIs use)
5. every data center and product in gold is one the agent can filter on
6. unsafe SQL is stopped by the validator before it reaches the warehouse
7. the executive summary and every suggested question give the same answer and
   the same rows on Databricks as on the bundled SQLite demo

Exit code 0 when everything passes, 1 otherwise.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent import answer_question, build_executive_summary  # noqa: E402
from src.database import DriftDatabase  # noqa: E402
from src.domain import APPLICATIONS_TABLE  # noqa: E402
from src.parity import first_difference  # noqa: E402
from src.prompting import KNOWN_DATA_CENTERS, KNOWN_PRODUCTS, SUGGESTED_QUESTIONS  # noqa: E402
from src.sql_safety import SQLSafetyError  # noqa: E402

DEMO_DB = ROOT / "data" / "northstar_demo.sqlite"
EXPECTED_FINDINGS = 475
EXPECTED_SCAN_DATE = "2026-04-03"


@dataclass
class Result:
    name: str
    passed: bool
    detail: str = ""
    fix: str = ""


def run_checks(warehouse: Any, reference: DriftDatabase) -> list[Result]:
    """Run checks 2-7 against an open warehouse backend; the reference is the SQLite demo."""

    results: list[Result] = []

    def record(name: str, passed: bool, detail: str = "", fix: str = "") -> bool:
        results.append(Result(name, passed, detail, "" if passed else fix))
        return passed

    started = time.perf_counter()
    try:
        warehouse.scalar("SELECT COUNT(*) AS n FROM applications AS a")
    except Exception as exc:  # connection, auth, missing table: all surface here
        record(
            "warehouse reachable and gold.applications readable",
            False,
            str(exc)[:300],
            "Check the hostname, HTTP path and token; start the SQL warehouse; confirm the lakehouse job built "
            f"{getattr(getattr(warehouse, 'settings', None), 'location', 'gold')}.applications.",
        )
        return results
    record("warehouse reachable and gold.applications readable", True, f"{time.perf_counter() - started:.1f}s")

    _, _, columns = warehouse.execute_select("SELECT * FROM applications AS a LIMIT 1")
    expected_columns = [column.name for column in APPLICATIONS_TABLE.columns]
    record(
        "agent's 23-column contract",
        columns == expected_columns,
        "" if columns == expected_columns else f"got {columns}",
        "Rerun 03_gold; gold.applications must expose exactly the agent's columns, in order.",
    )

    count = warehouse.scalar("SELECT COUNT(*) AS n FROM applications AS a")
    latest = warehouse.scalar("SELECT MAX(a.last_scanned_at) AS latest_scan FROM applications AS a")
    latest_date = str(latest)[:10] if latest else None
    record(
        f"{EXPECTED_FINDINGS} findings scanned {EXPECTED_SCAN_DATE}",
        count == EXPECTED_FINDINGS and latest_date == EXPECTED_SCAN_DATE,
        f"{count} findings, latest scan {latest_date}",
        "Run 05_checks in Databricks; it shows which silver or gold step is off.",
    )

    for column, known in (("data_center", KNOWN_DATA_CENTERS), ("product", KNOWN_PRODUCTS)):
        _, rows, _ = warehouse.execute_select(f"SELECT DISTINCT a.{column} AS value FROM applications AS a")
        unknown = sorted({row["value"] for row in rows} - set(known))
        record(
            f"every {column.replace('_', ' ')} is filterable",
            not unknown,
            f"not in the agent's list: {unknown}" if unknown else f"{len(rows)} values",
            f"Add the missing values to KNOWN_{'DATA_CENTERS' if column == 'data_center' else 'PRODUCTS'} "
            "in src/prompting.py.",
        )

    blocked = []
    for unsafe in ("DROP TABLE applications", "SELECT * FROM applications AS a; DELETE FROM applications"):
        try:
            warehouse.execute_select(unsafe)
        except SQLSafetyError:
            blocked.append(unsafe)
    record("unsafe SQL blocked before the warehouse", len(blocked) == 2, f"{len(blocked)}/2 blocked")

    local_summary = build_executive_summary(reference)
    remote_summary = build_executive_summary(warehouse)
    same_metrics = [m.value for m in remote_summary.metrics] == [m.value for m in local_summary.metrics]
    record(
        "executive summary matches the demo",
        same_metrics and remote_summary.narrative == local_summary.narrative,
        remote_summary.narrative,
        "Compare gold.kpi tables with the demo in 05_checks.",
    )

    for question in SUGGESTED_QUESTIONS:
        local = answer_question(reference, question)
        remote = answer_question(warehouse, question)
        difference = remote.error or (
            "different answer" if remote.answer != local.answer else first_difference(remote.rows, local.rows)
        )
        record(f"question: {question}", difference is None, difference or remote.answer)

    return results


def print_report(results: list[Result], out: Callable[[str], None] = print) -> bool:
    width = max((len(r.name) for r in results), default=0)
    for result in results:
        mark = "PASS" if result.passed else "FAIL"
        out(f"[{mark}] {result.name:<{width}}  {result.detail}")
        if result.fix:
            out(f"       fix: {result.fix}")
    passed = sum(r.passed for r in results)
    out(f"\n{passed}/{len(results)} checks passed.")
    return passed == len(results)


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    from src.warehouse import DatabricksDriftDatabase, DatabricksSettings

    try:
        settings = DatabricksSettings.from_env()
    except ValueError as exc:
        print(f"[FAIL] settings  {exc}\n       fix: add them to .env (see .env.example and lakehouse/README.md).")
        return 1
    try:
        import databricks.sql  # noqa: F401
    except ImportError:
        print("[FAIL] connector  databricks-sql-connector is not installed\n"
              "       fix: pip install -r requirements-databricks.txt")
        return 1
    print(f"Checking {settings.location}.applications on {settings.server_hostname} ({settings.auth_type})\n")

    with tempfile.TemporaryDirectory() as tmp:
        reference_path = Path(tmp) / "northstar_demo.sqlite"
        shutil.copy(DEMO_DB, reference_path)
        warehouse = DatabricksDriftDatabase(settings, max_rows=1000)
        try:
            ok = print_report(run_checks(warehouse, DriftDatabase(reference_path, max_rows=1000)))
        finally:
            warehouse.close()
    if ok:
        print("The agent is ready: set DRIFT_BACKEND=databricks and run `streamlit run app.py`.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
