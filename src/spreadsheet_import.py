"""Excel workbook ingestion for fresh drift analytics data."""

from __future__ import annotations

from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import pandas as pd

from src.domain import APPLICATIONS_TABLE
from src.models import SpreadsheetImportIssue, SpreadsheetImportResult


class SpreadsheetImportError(ValueError):
    """Raised when uploaded workbook data cannot be normalized safely."""

    def __init__(self, issues: list[SpreadsheetImportIssue]) -> None:
        self.issues = issues
        message = "; ".join(issue.message for issue in issues[:5])
        if len(issues) > 5:
            message += f"; plus {len(issues) - 5} more issue(s)"
        super().__init__(message)


CANONICAL_COLUMNS = [column.name for column in APPLICATIONS_TABLE.columns]

DEFAULTS: dict[str, Any] = {
    "business_owner": "Unassigned",
    "technology_owner": "Unassigned",
    "product": "Unassigned",
    "data_center": "Unassigned",
    "status": "Open",
    "exemption_requested": "N",
    "exemption_result": None,
    "in_scope": "Y",
    "note_count": 0,
}

ESSENTIAL_COLUMNS = {
    "app_name",
    "approved_version",
    "detected_version",
    "rto_score",
}

JOIN_APP_COLUMNS = {"app_id", "app_name"}
JOIN_DRIFT_COLUMNS = {"approved_version", "detected_version", "rto_score"}
JOIN_NOTE_COLUMNS = {"app_id", "drift_id"}
NOTE_EVIDENCE_COLUMNS = {
    "latest_note_date",
    "latest_note_type",
    "latest_note_author_role",
    "latest_note_text",
    "escalation_flag",
}
VERSION_COLUMNS = {"approved_version", "detected_version"}
SUPPORTED_EXTENSIONS = {".csv", ".tsv", ".xls", ".xlsx", ".xlsm"}
CSV_SHEET_NAME = "CSV Upload"

COLUMN_ALIASES = {
    "app_id": {
        "app_id",
        "app id",
        "appid",
        "application_id",
        "application id",
        "applicationid",
        "cmdb id",
        "ci id",
        "asset id",
        "id",
    },
    "finding_id": {
        "finding_id",
        "finding id",
        "finding_key",
        "finding key",
        "merged_id",
        "merged id",
    },
    "drift_id": {
        "drift_id",
        "drift id",
        "finding id",
        "finding_id",
        "issue id",
        "issue_id",
        "control id",
        "control_id",
    },
    "app_name": {
        "app_name",
        "app name",
        "application_name",
        "application name",
        "application",
        "app",
        "service",
        "system",
        "name",
    },
    "business_owner": {
        "business_owner",
        "business owner",
        "business",
        "owner",
        "app_owner",
        "app owner",
        "application owner",
        "owner_email",
        "owner email",
        "owner_team",
        "owner team",
    },
    "technology_owner": {
        "technology_owner",
        "technology owner",
        "tech_owner",
        "tech owner",
        "technical owner",
        "resiliency_lead",
        "resiliency lead",
        "resilience lead",
        "recovery lead",
    },
    "product": {
        "product",
        "product name",
        "product_family",
        "product family",
        "platform",
        "portfolio",
        "portfolio name",
        "business service",
        "service line",
        "line of business",
        "lob",
        "product_category",
        "product category",
        "category",
    },
    "data_center": {
        "data_center",
        "data center",
        "data centre",
        "datacenter",
        "dc",
        "hosting location",
        "hosting",
        "location",
        "region",
        "site",
        "environment",
        "primary_dc",
        "primary dc",
        "primary data center",
        "primary datacenter",
    },
    "approved_version": {
        "approved_version",
        "approved version",
        "approved",
        "approved sw version",
        "standard version",
        "gold version",
        "target_version",
        "target version",
        "target",
        "baseline version",
        "baseline",
    },
    "detected_version": {
        "detected_version",
        "detected version",
        "detected",
        "current_version",
        "current version",
        "actual version",
        "observed version",
        "running version",
        "installed version",
        "discovered version",
    },
    "rto_score": {
        "rto_score",
        "rto score",
        "rto",
        "rto rating",
        "rto tier",
        "rto criticality",
        "recovery score",
        "criticality",
        "priority",
        "risk score",
    },
    "status": {"status", "finding status", "drift status", "state", "remediation status"},
    "exemption_requested": {
        "exemption_requested",
        "exemption requested",
        "exception requested",
        "waiver requested",
    },
    "exemption_result": {
        "exemption_result",
        "exemption result",
        "exception result",
        "exemption status",
        "waiver status",
    },
    "in_scope": {"in_scope", "in scope", "scope", "scoped", "compliance scope"},
    "drift_detected_on": {
        "drift_detected_on",
        "drift detected on",
        "detected_on",
        "detected on",
        "first detected",
        "drift date",
        "opened date",
        "created date",
        "created",
        "drift_open_date",
        "drift open date",
        "open date",
    },
    "last_scanned_at": {"last_scanned_at", "last scanned at", "last scan", "scan timestamp", "scanned at"},
    "latest_note_date": {"latest_note_date", "latest note date", "note_date", "note date"},
    "latest_note_type": {"latest_note_type", "latest note type", "note_type", "note type"},
    "latest_note_author_role": {
        "latest_note_author_role",
        "latest note author role",
        "author_role",
        "author role",
    },
    "latest_note_text": {"latest_note_text", "latest note text", "note_text", "note text"},
    "escalation_flag": {"escalation_flag", "escalation flag", "escalated", "escalation"},
    "note_count": {"note_count", "note count"},
}


def _normalize_header(header: str) -> str:
    return " ".join(header.replace("_", " ").replace("-", " ").lower().split())


NORMALIZED_ALIASES = {
    _normalize_header(alias): canonical
    for canonical, aliases in COLUMN_ALIASES.items()
    for alias in aliases | {canonical}
}


def list_excel_sheets(workbook_bytes: bytes, *, filename: str | None = None) -> list[str]:
    """Return importable worksheet names from an uploaded workbook or tabular file."""

    source = _read_tabular_source(workbook_bytes, filename=filename)
    return list(source.keys())


def import_excel_workbook(
    workbook_bytes: bytes,
    *,
    selected_sheets: list[str] | None = None,
    filename: str | None = None,
) -> SpreadsheetImportResult:
    """Normalize one or more workbook sheets into application drift rows."""

    source = _read_tabular_source(workbook_bytes, filename=filename)
    sheets = selected_sheets or list(source.keys())
    if not sheets:
        raise SpreadsheetImportError([SpreadsheetImportIssue(sheet="Workbook", message="Select at least one sheet.")])

    issues: list[SpreadsheetImportIssue] = []
    warnings: list[SpreadsheetImportIssue] = []
    parsed_sheets: list[dict[str, Any]] = []

    for sheet in sheets:
        if sheet not in source:
            issues.append(SpreadsheetImportIssue(sheet=sheet, message="Selected sheet does not exist in workbook."))
            continue

        frame = source[sheet].dropna(how="all")
        if frame.empty:
            warnings.append(SpreadsheetImportIssue(sheet=sheet, message="Sheet contained no data rows."))
            continue
        parsed_sheets.append({"sheet": sheet, "frame": frame, "column_map": _map_columns(frame.columns)})

    if issues:
        raise SpreadsheetImportError(issues)

    joined_result = _import_joined_workbook(parsed_sheets, warnings=warnings, filename=filename)
    if joined_result is not None:
        return joined_result

    rows: list[dict[str, Any]] = []
    seen_app_ids: dict[str, str] = {}
    ingested_sheets: list[str] = []

    for parsed_sheet in parsed_sheets:
        sheet = parsed_sheet["sheet"]
        frame = parsed_sheet["frame"]
        column_map = parsed_sheet["column_map"]
        missing = sorted(ESSENTIAL_COLUMNS - set(column_map.values()))
        if missing:
            warnings.append(
                SpreadsheetImportIssue(
                    sheet=sheet,
                    message=(
                        f"Missing essential columns: {', '.join(missing)}. "
                        f"Detected columns: {', '.join(str(column) for column in frame.columns)}."
                    ),
                )
            )
            continue

        for index, raw_row in frame.iterrows():
            row_number = int(index) + 2
            normalized, row_issues = _normalize_row(raw_row, column_map, sheet, row_number)
            if row_issues:
                warnings.extend(row_issues)
                continue
            app_id = normalized["app_id"]
            if app_id in seen_app_ids:
                warnings.append(
                    SpreadsheetImportIssue(
                        sheet=sheet,
                        row_number=row_number,
                        message=f"Duplicate app_id '{app_id}' already appeared on sheet '{seen_app_ids[app_id]}'.",
                    )
                )
                continue
            seen_app_ids[app_id] = sheet
            if sheet not in ingested_sheets:
                ingested_sheets.append(sheet)
            rows.append(normalized)

    if not rows:
        diagnostics = warnings or [
            SpreadsheetImportIssue(sheet="Workbook", message="No valid drift rows were found in selected sheets.")
        ]
        raise SpreadsheetImportError(diagnostics)

    return SpreadsheetImportResult(
        filename=filename,
        sheets=ingested_sheets,
        row_count=len(rows),
        rows=rows,
        warnings=warnings,
    )


def preview_excel_workbook(
    workbook_bytes: bytes,
    selected_sheets: list[str] | None = None,
    *,
    filename: str | None = None,
) -> list[dict[str, Any]]:
    """Return sheet-level import diagnostics without mutating the database."""

    source = _read_tabular_source(workbook_bytes, filename=filename)
    sheets = selected_sheets or list(source.keys())
    parsed: list[dict[str, Any]] = []
    for sheet in sheets:
        if sheet not in source:
            continue
        frame = source[sheet].dropna(how="all")
        column_map = _map_columns(frame.columns)
        parsed.append({"sheet": sheet, "frame": frame, "column_map": column_map})
    join_available = _has_join_shape(parsed)

    previews: list[dict[str, Any]] = []
    for parsed_sheet in parsed:
        sheet = parsed_sheet["sheet"]
        frame = parsed_sheet["frame"]
        column_map = parsed_sheet["column_map"]
        detected = sorted(set(column_map.values()))
        missing_essential = sorted(ESSENTIAL_COLUMNS - set(column_map.values()))
        role = _sheet_import_role(set(column_map.values()), join_available=join_available)
        if join_available and role == "Application metadata":
            missing_display = "Joined to drift instances by app_id"
        elif join_available and role == "Drift instance rows" and missing_essential == ["app_name"]:
            missing_display = "Resolved from application metadata by app_id + drift_id"
        elif join_available and role == "Drift note evidence":
            missing_display = "Merged into drift instances by app_id + drift_id"
        elif missing_essential:
            missing_display = ", ".join(missing_essential)
        else:
            missing_display = "Ready"
        previews.append(
            {
                "sheet": sheet,
                "rows": int(len(frame)),
                "import_role": role,
                "detected_fields": ", ".join(detected) if detected else "None",
                "missing_essential": missing_display,
            }
        )
    return previews


def _read_tabular_source(workbook_bytes: bytes, *, filename: str | None) -> dict[str, pd.DataFrame]:
    extension = Path(filename or "").suffix.lower()
    if extension and extension not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise SpreadsheetImportError(
            [
                SpreadsheetImportIssue(
                    sheet="Workbook",
                    message=f"Unsupported file type '{extension}'. Upload one of: {allowed}.",
                )
            ]
        )

    try:
        if extension in {".csv", ".tsv"}:
            return {CSV_SHEET_NAME: _read_delimited_frame(workbook_bytes, extension=extension)}

        engine = "xlrd" if extension == ".xls" else "openpyxl"
        workbook = pd.ExcelFile(BytesIO(workbook_bytes), engine=engine)
        return {
            sheet_name: workbook.parse(sheet_name=sheet_name, dtype=object)
            for sheet_name in workbook.sheet_names
        }
    except ImportError as exc:
        raise SpreadsheetImportError(
            [
                SpreadsheetImportIssue(
                    sheet="Workbook",
                    message=(
                        f"Unable to read '{extension or 'workbook'}' because an optional parser is missing: {exc}."
                    ),
                )
            ]
        ) from exc
    except Exception as exc:  # pragma: no cover - pandas/openpyxl exception types vary
        raise SpreadsheetImportError(
            [SpreadsheetImportIssue(sheet="Workbook", message=f"Unable to read workbook: {exc}")]
        ) from exc


def _read_delimited_frame(workbook_bytes: bytes, *, extension: str) -> pd.DataFrame:
    separator = "\t" if extension == ".tsv" else None
    read_options: dict[str, Any] = {"dtype": object}
    if separator:
        read_options["sep"] = separator
    else:
        read_options["sep"] = None
        read_options["engine"] = "python"
    return pd.read_csv(BytesIO(workbook_bytes), **read_options)


def _map_columns(columns: pd.Index) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for column in columns:
        normalized = _normalize_header(str(column))
        canonical = NORMALIZED_ALIASES.get(normalized)
        if canonical and column not in mapping:
            mapping[str(column)] = canonical
    return mapping


def _has_join_shape(parsed_sheets: list[dict[str, Any]]) -> bool:
    mapped_sets = [set(parsed_sheet["column_map"].values()) for parsed_sheet in parsed_sheets]
    has_app_metadata = any(_is_application_metadata_sheet(mapped) for mapped in mapped_sets)
    has_drift_instances = any(_is_drift_instance_sheet(mapped) for mapped in mapped_sets)
    return has_app_metadata and has_drift_instances


def _is_application_metadata_sheet(mapped_columns: set[str]) -> bool:
    return JOIN_APP_COLUMNS <= mapped_columns and not VERSION_COLUMNS <= mapped_columns


def _is_drift_instance_sheet(mapped_columns: set[str]) -> bool:
    return JOIN_DRIFT_COLUMNS <= mapped_columns and bool({"app_id", "app_name"} & mapped_columns)


def _is_note_evidence_sheet(mapped_columns: set[str]) -> bool:
    return JOIN_NOTE_COLUMNS <= mapped_columns and bool(NOTE_EVIDENCE_COLUMNS & mapped_columns) and not VERSION_COLUMNS <= mapped_columns


def _sheet_import_role(mapped_columns: set[str], *, join_available: bool) -> str:
    if join_available and _is_application_metadata_sheet(mapped_columns):
        return "Application metadata"
    if join_available and _is_drift_instance_sheet(mapped_columns):
        return "Drift instance rows"
    if join_available and _is_note_evidence_sheet(mapped_columns):
        return "Drift note evidence"
    if ESSENTIAL_COLUMNS <= mapped_columns:
        return "Standalone drift rows"
    return "Supporting or skipped"


def _import_joined_workbook(
    parsed_sheets: list[dict[str, Any]],
    *,
    warnings: list[SpreadsheetImportIssue],
    filename: str | None,
) -> SpreadsheetImportResult | None:
    """Import normalized workbooks where app metadata and drift findings are split across sheets."""

    if not _has_join_shape(parsed_sheets):
        return None

    app_sheets = [
        parsed_sheet
        for parsed_sheet in parsed_sheets
        if _is_application_metadata_sheet(set(parsed_sheet["column_map"].values()))
    ]
    drift_sheets = [
        parsed_sheet
        for parsed_sheet in parsed_sheets
        if _is_drift_instance_sheet(set(parsed_sheet["column_map"].values()))
    ]
    note_sheets = [
        parsed_sheet
        for parsed_sheet in parsed_sheets
        if _is_note_evidence_sheet(set(parsed_sheet["column_map"].values()))
    ]

    app_lookup: dict[str, dict[str, Any]] = {}
    ingested_sheets: list[str] = []

    for parsed_sheet in app_sheets:
        sheet = parsed_sheet["sheet"]
        frame = parsed_sheet["frame"]
        column_map = parsed_sheet["column_map"]
        sheet_contributed = False
        for index, raw_row in frame.iterrows():
            row_number = int(index) + 2
            values = _extract_partial_values(raw_row, column_map)
            app_id = values.get("app_id")
            app_name = values.get("app_name")
            if _is_blank(app_id):
                warnings.append(
                    SpreadsheetImportIssue(sheet=sheet, row_number=row_number, message="Missing app_id in metadata row.")
                )
                continue
            if _is_blank(app_name):
                warnings.append(
                    SpreadsheetImportIssue(
                        sheet=sheet,
                        row_number=row_number,
                        message=f"Missing app_name for app_id '{app_id}' in metadata row.",
                    )
                )
                continue
            app_key = str(app_id)
            if app_key in app_lookup:
                warnings.append(
                    SpreadsheetImportIssue(
                        sheet=sheet,
                        row_number=row_number,
                        message=f"Duplicate application metadata for app_id '{app_key}' was ignored.",
                    )
                )
                continue
            app_lookup[app_key] = values
            sheet_contributed = True
        if sheet_contributed:
            ingested_sheets.append(sheet)

    note_lookup = _build_note_lookup(note_sheets, warnings=warnings)
    if note_lookup:
        ingested_sheets.extend(parsed_sheet["sheet"] for parsed_sheet in note_sheets)

    seen_row_ids: set[str] = set()
    rows: list[dict[str, Any]] = []

    for parsed_sheet in drift_sheets:
        sheet = parsed_sheet["sheet"]
        frame = parsed_sheet["frame"]
        column_map = parsed_sheet["column_map"]
        sheet_contributed = False
        for index, raw_row in frame.iterrows():
            row_number = int(index) + 2
            drift_values = _extract_partial_values(raw_row, column_map)
            base_app_id = drift_values.get("app_id")
            if _is_blank(base_app_id):
                warnings.append(
                    SpreadsheetImportIssue(sheet=sheet, row_number=row_number, message="Missing app_id in drift row.")
                )
                continue

            base_key = str(base_app_id)
            drift_id = drift_values.get("drift_id")
            if _is_blank(drift_id):
                drift_id = _raw_row_value(raw_row, {"instance name", "instance_name"})
            if _is_blank(drift_id):
                drift_id = f"row-{row_number}"
                warnings.append(
                    SpreadsheetImportIssue(
                        sheet=sheet,
                        row_number=row_number,
                        message="Missing drift_id in drift row; generated one from the row number.",
                    )
                )
            drift_key = str(drift_id)
            combined = dict(app_lookup.get(base_key, {}))
            combined.update({key: value for key, value in drift_values.items() if not _is_blank(value)})
            if base_key not in app_lookup and _is_blank(combined.get("app_name")):
                warnings.append(
                    SpreadsheetImportIssue(
                        sheet=sheet,
                        row_number=row_number,
                        message=f"No application metadata matched app_id '{base_key}'; using app_id as app_name.",
                    )
                )
                combined["app_name"] = base_key
            combined["app_id"] = base_key
            combined["drift_id"] = drift_key
            combined.update(note_lookup.get((base_key, drift_key), {}))
            combined["finding_id"] = _joined_finding_identifier(base_key, drift_key, seen_row_ids=seen_row_ids)

            normalized, row_issues = _finalize_normalized_values(combined, sheet, row_number)
            if row_issues:
                warnings.extend(row_issues)
                continue
            rows.append(normalized)
            sheet_contributed = True
        if sheet_contributed:
            ingested_sheets.append(sheet)

    for parsed_sheet in parsed_sheets:
        sheet = parsed_sheet["sheet"]
        mapped = set(parsed_sheet["column_map"].values())
        if (
            not _is_application_metadata_sheet(mapped)
            and not _is_drift_instance_sheet(mapped)
            and not _is_note_evidence_sheet(mapped)
        ):
            warnings.append(
                SpreadsheetImportIssue(
                    sheet=sheet,
                    message=(
                        "Skipped supporting sheet because it does not contain application metadata, drift rows, "
                        "or drift note evidence."
                    ),
                )
            )

    if not rows:
        diagnostics = warnings or [
            SpreadsheetImportIssue(
                sheet="Workbook",
                message="No valid drift rows were found after joining application metadata to drift instances.",
            )
        ]
        raise SpreadsheetImportError(diagnostics)

    return SpreadsheetImportResult(
        filename=filename,
        sheets=_dedupe_preserve_order(ingested_sheets),
        row_count=len(rows),
        rows=rows,
        warnings=warnings,
    )


def _normalize_row(
    raw_row: pd.Series,
    column_map: dict[str, str],
    sheet: str,
    row_number: int,
) -> tuple[dict[str, Any], list[SpreadsheetImportIssue]]:
    values = _extract_partial_values(raw_row, column_map)
    return _finalize_normalized_values(values, sheet, row_number)


def _build_note_lookup(
    note_sheets: list[dict[str, Any]],
    *,
    warnings: list[SpreadsheetImportIssue],
) -> dict[tuple[str, str], dict[str, Any]]:
    notes: dict[tuple[str, str], dict[str, Any]] = {}
    note_dates: dict[tuple[str, str], pd.Timestamp] = {}

    for parsed_sheet in note_sheets:
        sheet = parsed_sheet["sheet"]
        frame = parsed_sheet["frame"]
        column_map = parsed_sheet["column_map"]
        for index, raw_row in frame.iterrows():
            row_number = int(index) + 2
            values = _extract_partial_values(raw_row, column_map)
            app_id = values.get("app_id")
            drift_id = values.get("drift_id")
            if _is_blank(app_id) or _is_blank(drift_id):
                warnings.append(
                    SpreadsheetImportIssue(
                        sheet=sheet,
                        row_number=row_number,
                        message="Drift note evidence requires both app_id and drift_id.",
                    )
                )
                continue

            key = (str(app_id), str(drift_id))
            current = notes.setdefault(key, {"note_count": 0, "escalation_flag": "N"})
            current["note_count"] = int(current.get("note_count") or 0) + 1

            escalation_flag = values.get("escalation_flag")
            if not _is_blank(escalation_flag):
                try:
                    if _normalize_yes_no(escalation_flag, "escalation_flag") == "Y":
                        current["escalation_flag"] = "Y"
                except ValueError as exc:
                    warnings.append(SpreadsheetImportIssue(sheet=sheet, row_number=row_number, message=str(exc)))

            candidate_date = _coerce_note_date(values.get("latest_note_date"), sheet, row_number, warnings)
            current_date = note_dates.get(key)
            should_replace = current_date is None or candidate_date >= current_date
            if should_replace:
                note_dates[key] = candidate_date
                for column in (
                    "latest_note_date",
                    "latest_note_type",
                    "latest_note_author_role",
                    "latest_note_text",
                ):
                    if column == "latest_note_date":
                        if candidate_date != pd.Timestamp.min:
                            current[column] = candidate_date.date().isoformat()
                        else:
                            current.pop(column, None)
                    elif not _is_blank(values.get(column)):
                        current[column] = values[column]
    return notes


def _coerce_note_date(
    value: Any,
    sheet: str,
    row_number: int,
    warnings: list[SpreadsheetImportIssue],
) -> pd.Timestamp:
    if _is_blank(value):
        return pd.Timestamp.min
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        warnings.append(
            SpreadsheetImportIssue(
                sheet=sheet,
                row_number=row_number,
                message=f"Unable to parse note date value '{value}'; note still counted.",
            )
        )
        return pd.Timestamp.min
    return pd.Timestamp(timestamp).tz_localize(None) if getattr(timestamp, "tzinfo", None) else pd.Timestamp(timestamp)


def _extract_partial_values(raw_row: pd.Series, column_map: dict[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    first_value_wins = {"business_owner", "technology_owner", "data_center"}
    for source, canonical in column_map.items():
        if canonical not in CANONICAL_COLUMNS:
            continue
        value = _normalize_value(canonical, raw_row[source])
        if not _is_blank(value):
            if canonical in first_value_wins and not _is_blank(values.get(canonical)):
                continue
            values[canonical] = value
    return values


def _finalize_normalized_values(
    raw_values: dict[str, Any],
    sheet: str,
    row_number: int,
) -> tuple[dict[str, Any], list[SpreadsheetImportIssue]]:
    issues: list[SpreadsheetImportIssue] = []
    values: dict[str, Any] = dict(raw_values)
    if _is_blank(values.get("last_scanned_at")):
        values["last_scanned_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    if _is_blank(values.get("app_name")) and not _is_blank(values.get("app_id")):
        values["app_name"] = str(values["app_id"])
    if _is_blank(values.get("app_id")) and not _is_blank(values.get("app_name")):
        safe_name = "".join(char if char.isalnum() else "-" for char in str(values["app_name"]).upper())
        safe_name = "-".join(part for part in safe_name.split("-") if part)
        safe_sheet = "".join(char if char.isalnum() else "-" for char in sheet.upper())
        safe_sheet = "-".join(part for part in safe_sheet.split("-") if part) or "SHEET"
        values["app_id"] = f"{safe_sheet[:12]}-{row_number}-{safe_name[:24]}"
    if _is_blank(values.get("finding_id")) and not _is_blank(values.get("app_id")):
        if _is_blank(values.get("drift_id")):
            values["finding_id"] = str(values["app_id"])
        else:
            values["finding_id"] = _joined_finding_identifier(
                str(values["app_id"]),
                str(values["drift_id"]),
                seen_row_ids=set(),
            )
    for column, default in DEFAULTS.items():
        if _is_blank(values.get(column)):
            values[column] = default
    if _is_blank(values.get("drift_detected_on")):
        values["drift_detected_on"] = date.today().isoformat()

    for column in ESSENTIAL_COLUMNS:
        if _is_blank(values.get(column)):
            issues.append(
                SpreadsheetImportIssue(
                    sheet=sheet,
                    row_number=row_number,
                    message=f"Missing value for essential column '{column}'.",
                )
            )

    if issues:
        return values, issues

    try:
        values["rto_score"] = _normalize_rto(values["rto_score"])
    except ValueError as exc:
        issues.append(SpreadsheetImportIssue(sheet=sheet, row_number=row_number, message=str(exc)))

    try:
        values["status"] = _normalize_choice(values["status"], {"open": "Open", "closed": "Closed"}, "status")
        values["exemption_requested"] = _normalize_yes_no(values["exemption_requested"], "exemption_requested")
        values["in_scope"] = _normalize_yes_no(values["in_scope"], "in_scope")
    except ValueError as exc:
        issues.append(SpreadsheetImportIssue(sheet=sheet, row_number=row_number, message=str(exc)))

    try:
        values["note_count"] = int(float(values.get("note_count") or 0))
    except (TypeError, ValueError) as exc:
        issues.append(
            SpreadsheetImportIssue(sheet=sheet, row_number=row_number, message="note_count must be a whole number.")
        )

    escalation_flag = values.get("escalation_flag")
    if _is_blank(escalation_flag):
        values["escalation_flag"] = None
    else:
        try:
            values["escalation_flag"] = _normalize_yes_no(escalation_flag, "escalation_flag")
        except ValueError as exc:
            issues.append(SpreadsheetImportIssue(sheet=sheet, row_number=row_number, message=str(exc)))

    exemption_result = values.get("exemption_result")
    if _is_blank(exemption_result):
        values["exemption_result"] = None
    else:
        try:
            values["exemption_result"] = _normalize_choice(
                exemption_result,
                {"approved": "Approved", "pending": "Pending", "denied": "Denied"},
                "exemption_result",
            )
        except ValueError as exc:
            issues.append(SpreadsheetImportIssue(sheet=sheet, row_number=row_number, message=str(exc)))

    for column in ("drift_detected_on", "last_scanned_at"):
        try:
            values[column] = _normalize_datetime(values[column], date_only=(column == "drift_detected_on"))
        except ValueError as exc:
            issues.append(SpreadsheetImportIssue(sheet=sheet, row_number=row_number, message=str(exc)))

    if not _is_blank(values.get("latest_note_date")):
        try:
            values["latest_note_date"] = _normalize_datetime(values["latest_note_date"], date_only=True)
        except ValueError as exc:
            issues.append(SpreadsheetImportIssue(sheet=sheet, row_number=row_number, message=str(exc)))
    else:
        values["latest_note_date"] = None

    for column in CANONICAL_COLUMNS:
        values.setdefault(column, None)

    return values, issues


def _joined_finding_identifier(base_app_id: str, drift_id: str, *, seen_row_ids: set[str]) -> str:
    safe_suffix = _safe_identifier_fragment(drift_id, fallback="DRIFT")
    candidate = f"{base_app_id}-{safe_suffix}"
    counter = 2
    while candidate in seen_row_ids:
        candidate = f"{base_app_id}-{safe_suffix}-{counter}"
        counter += 1
    seen_row_ids.add(candidate)
    return candidate


def _raw_row_value(raw_row: pd.Series, aliases: set[str]) -> Any:
    normalized_aliases = {_normalize_header(alias) for alias in aliases}
    for column in raw_row.index:
        if _normalize_header(str(column)) in normalized_aliases:
            return raw_row[column]
    return None


def _safe_identifier_fragment(value: Any, *, fallback: str) -> str:
    if _is_blank(value):
        value = fallback
    safe = "".join(char if char.isalnum() else "-" for char in str(value).upper())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe or fallback.upper()


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize_value(column: str, value: Any) -> Any:
    if _is_blank(value):
        return None
    if column == "rto_score":
        return value
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _normalize_rto(value: Any) -> int:
    normalized = str(value).strip().lower()
    tier_scores = {
        "mission critical": 1,
        "critical": 2,
        "p1": 1,
        "p2": 2,
        "tier 1": 1,
        "tier 2": 2,
        "high": 3,
        "p3": 3,
        "tier 3": 3,
        "medium": 5,
        "med": 5,
        "p4": 5,
        "tier 4": 5,
        "low": 8,
        "p5": 8,
        "tier 5": 8,
    }
    if normalized in tier_scores:
        return tier_scores[normalized]
    try:
        score = int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"rto_score must be a number from 1 to 10; got '{value}'.") from exc
    if score < 1 or score > 10:
        raise ValueError(f"rto_score must be between 1 and 10; got {score}.")
    return score


def _normalize_choice(value: Any, allowed: dict[str, str], column: str) -> str:
    normalized = str(value).strip().lower()
    if column == "status":
        if normalized in {"active", "open finding", "new", "in progress", "pending"}:
            normalized = "open"
        if normalized in {"resolved", "remediated", "done", "complete", "completed"}:
            normalized = "closed"
    if normalized not in allowed:
        choices = ", ".join(sorted(set(allowed.values())))
        raise ValueError(f"{column} must be one of {choices}; got '{value}'.")
    return allowed[normalized]


def _normalize_yes_no(value: Any, column: str) -> str:
    normalized = str(value).strip().lower()
    if normalized in {"y", "yes", "true", "1"}:
        return "Y"
    if normalized in {"n", "no", "false", "0"}:
        return "N"
    raise ValueError(f"{column} must be Y or N; got '{value}'.")


def _normalize_datetime(value: Any, *, date_only: bool) -> str:
    if _is_blank(value):
        raise ValueError("Date value cannot be blank.")
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        raise ValueError(f"Unable to parse date value '{value}'.")
    if date_only:
        return timestamp.date().isoformat()
    return timestamp.to_pydatetime().replace(microsecond=0).isoformat()


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except TypeError:
        pass
    return str(value).strip() == ""
