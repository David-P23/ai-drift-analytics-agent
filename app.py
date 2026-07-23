"""Streamlit entry point for the AI Drift Analytics Agent."""

from __future__ import annotations

import os
import json
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import altair as alt
import pandas as pd

from src import analytics
from src.agent import answer_question, build_executive_summary, run_query_plan
from src.cluster_detection import GROUPING_OPTIONS, detect_drift_clusters, summarize_cluster_periods
from src.database import (
    DriftDatabase,
    clear_applications_rows,
    initialize_demo_database,
    initialize_empty_database,
    replace_applications_rows,
)
from src.models import ExecutiveSummary, Metric, QueryResponse, SpreadsheetImportIssue
from src.prompting import SUGGESTED_QUESTIONS
from src.spreadsheet_import import (
    SpreadsheetImportError,
    import_excel_workbook,
    list_excel_sheets,
    preview_excel_workbook,
)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


DEFAULT_DB_PATH = Path(os.getenv("DRIFT_DB_PATH", "data/northstar_demo.sqlite"))
DEMO_ROWS_PATH = Path("data/northstar_demo_rows.json")
DEFAULT_ROW_LIMIT = int(os.getenv("SQL_ROW_LIMIT", "1000"))
TABLEAU_DASHBOARD_URL = os.getenv("TABLEAU_DASHBOARD_URL", "").strip()
TABLEAU_GEO_VIEW_URL = os.getenv(
    "TABLEAU_GEO_VIEW_URL",
    "https://public.tableau.com/views/ExecutiveDRIFTCommandCenter/GeographicRiskCommandCenter",
).strip()
TABLEAU_KPI_VIEW_URL = os.getenv(
    "TABLEAU_KPI_VIEW_URL",
    "https://public.tableau.com/views/ExecutiveDRIFTCommandCenter/ExecutiveCommandCenter",
).strip()
TABLEAU_EMBED_WIDTH = int(os.getenv("TABLEAU_EMBED_WIDTH", "1420"))
TABLEAU_EMBED_HEIGHT = int(os.getenv("TABLEAU_EMBED_HEIGHT", "1120"))
SHOW_PORTFOLIO_CONTROLS = os.getenv("SHOW_PORTFOLIO_CONTROLS", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "y",
}
APP_STATE_VERSION = "executive-dashboard-v1"
DEMO_DATA_SOURCE_LABEL = "NorthStar demo portfolio: 475 drift rows from Applications, Drift Instances, and Drift Notes"
FALLBACK_DEMO_DATA_SOURCE_LABEL = "NorthStar fallback demo portfolio: bundled deterministic demo rows"

CHART_COLORS = ["#1446A0", "#00A6A6", "#F2A541", "#D1495B", "#5C677D", "#3A7D44"]
ACTION_COLORS = {
    "Executive intervention": "#B42318",
    "Remediation command": "#DC6803",
    "Governance watch": "#1570EF",
    "Managed backlog": "#039855",
}
RTO_ORDER = ["Mission Critical", "High", "Medium", "Low"]
ACTION_ORDER = ["Executive intervention", "Remediation command", "Governance watch", "Managed backlog"]


def tableau_public_view_url(raw_url: str) -> str:
    """Convert a Tableau Public browser URL into the view URL expected by tableau-viz."""
    raw_url = raw_url.strip()
    if not raw_url:
        return ""

    parts = urlsplit(raw_url)
    path = parts.path
    if "/viz/" in path:
        path = "/views/" + path.split("/viz/", 1)[1]

    base_url = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
    return base_url


CLUSTER_GROUPING_EXPLANATIONS = {
    "Product update": (
        "Original detector mode. Flags a wave when several applications drift from the same approved product version "
        "inside the selected window."
    ),
    "Product only": (
        "Broader portfolio scan. Groups all drift for the product together, regardless of the approved or detected "
        "version."
    ),
    "Product/version pair": (
        "Most precise version mismatch. Requires the same product, approved version, and detected version."
    ),
    "Product update + data center": (
        "Operational concentration view. Requires the same product update in the same hosting location."
    ),
}

PAGE_CSS = """
<style>
    .block-container {
        padding-top: 1.4rem;
        padding-bottom: 2.4rem;
        max-width: 1420px;
    }
    h1, h2, h3 {
        letter-spacing: 0;
    }
    div[data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid #e6eaf0;
        border-radius: 8px;
        padding: 0.85rem 1rem;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .hero-panel {
        background: linear-gradient(135deg, #101828 0%, #17324d 62%, #0f766e 100%);
        color: white;
        border-radius: 8px;
        padding: 1.4rem 1.6rem;
        margin-bottom: 1rem;
    }
    .hero-eyebrow {
        color: #a7f3d0;
        font-size: 0.78rem;
        font-weight: 700;
        letter-spacing: 0.08rem;
        text-transform: uppercase;
        margin-bottom: 0.35rem;
    }
    .hero-title {
        font-size: 2.05rem;
        line-height: 1.12;
        font-weight: 760;
        margin-bottom: 0.45rem;
    }
    .hero-copy {
        color: #dbeafe;
        max-width: 900px;
        font-size: 1rem;
        line-height: 1.55;
    }
    .metric-card {
        background: #ffffff;
        border: 1px solid #e6eaf0;
        border-left: 5px solid #1446a0;
        border-radius: 8px;
        padding: 0.9rem 1rem;
        min-height: 118px;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .metric-card.alert {
        border-left-color: #d1495b;
    }
    .metric-card.warn {
        border-left-color: #f2a541;
    }
    .metric-card.good {
        border-left-color: #00a6a6;
    }
    .metric-label {
        color: #475467;
        font-size: 0.78rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.04rem;
        margin-bottom: 0.35rem;
    }
    .metric-value {
        color: #101828;
        font-size: 2rem;
        font-weight: 760;
        line-height: 1;
        margin-bottom: 0.45rem;
    }
    .metric-caption {
        color: #667085;
        font-size: 0.86rem;
        line-height: 1.35;
    }
    .brief-card {
        background: #ffffff;
        border: 1px solid #e6eaf0;
        border-radius: 8px;
        padding: 1rem 1.1rem;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .brief-title {
        color: #101828;
        font-size: 0.95rem;
        font-weight: 760;
        margin-bottom: 0.45rem;
    }
    .brief-copy {
        color: #344054;
        font-size: 0.95rem;
        line-height: 1.5;
    }
    .incident-card {
        background: #fff7ed;
        border: 1px solid #fed7aa;
        border-left: 5px solid #c2410c;
        border-radius: 8px;
        padding: 0.95rem 1.05rem;
        margin-top: 0.75rem;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .incident-title {
        color: #9a3412;
        font-size: 0.93rem;
        font-weight: 760;
        margin-bottom: 0.4rem;
    }
    .incident-copy {
        color: #431407;
        font-size: 0.93rem;
        line-height: 1.45;
    }
    .answer-card {
        background: #f8fafc;
        border: 1px solid #dbe3ed;
        border-radius: 8px;
        padding: 1rem 1.1rem;
        margin: 0.4rem 0 0.8rem;
    }
    .status-chip {
        display: inline-block;
        background: #eef6ff;
        color: #1446a0;
        border: 1px solid #bfd7ff;
        border-radius: 999px;
        padding: 0.18rem 0.55rem;
        margin: 0 0.25rem 0.35rem 0;
        font-size: 0.78rem;
        font-weight: 700;
    }
    .section-kicker {
        color: #475467;
        font-size: 0.8rem;
        font-weight: 760;
        text-transform: uppercase;
        letter-spacing: 0.06rem;
        margin-bottom: 0.2rem;
    }
    .signal-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 0.85rem;
        margin: 0.5rem 0 1rem;
    }
    .signal-card {
        background: #ffffff;
        border: 1px solid #e6eaf0;
        border-radius: 8px;
        padding: 0.85rem 0.95rem;
        min-height: 104px;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .signal-label {
        color: #667085;
        font-size: 0.75rem;
        font-weight: 760;
        text-transform: uppercase;
        letter-spacing: 0.05rem;
        margin-bottom: 0.3rem;
    }
    .signal-main {
        color: #101828;
        font-size: 1.08rem;
        line-height: 1.25;
        font-weight: 760;
        margin-bottom: 0.35rem;
    }
    .signal-sub {
        color: #667085;
        font-size: 0.84rem;
        line-height: 1.35;
    }
    @media (max-width: 900px) {
        .signal-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }
</style>
"""


def response_to_dataframe(response: QueryResponse) -> pd.DataFrame:
    return pd.DataFrame(response.rows, columns=response.columns or None)


def clean_label(value: str) -> str:
    return value.replace("_", " ").title()


def metric_by_label(summary: ExecutiveSummary, label: str) -> Metric:
    return next(metric for metric in summary.metrics if metric.label == label)


def render_metric_card(column: Any, metric: Metric, caption: str, tone: str = "") -> None:
    column.markdown(
        f"""
        <div class="metric-card {escape(tone)}">
            <div class="metric-label">{escape(metric.label)}</div>
            <div class="metric-value">{escape(str(metric.value))}</div>
            <div class="metric-caption">{escape(caption)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def format_period_delta(current_value: int | float, prior_value: int | float, window_days: int) -> str:
    current = float(current_value or 0)
    prior = float(prior_value or 0)
    if prior == 0:
        if current == 0:
            return f"0% vs prior {window_days}d"
        return f"New vs prior {window_days}d"
    percent_change = ((current - prior) / prior) * 100
    return f"{percent_change:+.0f}% vs prior {window_days}d"


def render_shell_header(st: Any, summary: ExecutiveSummary) -> None:
    st.markdown(PAGE_CSS, unsafe_allow_html=True)
    st.markdown(
        f"""
        <div class="hero-panel">
            <div class="hero-eyebrow">Enterprise Application Drift Analytics</div>
            <div class="hero-title">Executive Drift Command Center</div>
            <div class="hero-copy">{escape(summary.narrative)}
            The agent converts governance questions into validated read-only SQL, then returns the answer,
            evidence table, and board-ready chart in one flow.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if "data_source_label" in st.session_state:
        st.caption(f"Data source: {st.session_state.data_source_label}")


def render_upload_landing(st: Any) -> None:
    st.markdown(PAGE_CSS, unsafe_allow_html=True)
    st.markdown(
        """
        <div class="hero-panel">
            <div class="hero-eyebrow">Enterprise Application Drift Analytics</div>
            <div class="hero-title">Upload a workbook to start the agent</div>
            <div class="hero-copy">
            No portfolio metrics, board brief, AI risk graphs, decision queues, or SQL answers are shown
            until a spreadsheet has been successfully imported. Use Workbook Refresh in the sidebar to
            load fresh data from one or more Excel sheets.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown('<div class="section-kicker">Waiting For Spreadsheet Source</div>', unsafe_allow_html=True)
    st.info("Upload an `.xlsx`, `.xlsm`, `.xls`, `.csv`, or `.tsv` file in the sidebar, then run refresh.")
    col1, col2, col3 = st.columns(3)
    col1.markdown(
        """
        <div class="brief-card">
            <div class="brief-title">1. Upload Workbook</div>
            <div class="brief-copy">The app reads available sheets and detects drift columns with enterprise aliases.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    col2.markdown(
        """
        <div class="brief-card">
            <div class="brief-title">2. Select Sheets</div>
            <div class="brief-copy">Choose one or more data sheets. Cover or notes sheets can be skipped automatically.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    col3.markdown(
        """
        <div class="brief-card">
            <div class="brief-title">3. Run Analytics</div>
            <div class="brief-copy">After import, all executive tabs and AI charts are computed from workbook data.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_status_chips(st: Any) -> None:
    chips = ["Spreadsheet-first", "Read-only SQL", "Schema validated", "Limit enforced", "Streamlit Cloud ready"]
    st.markdown("".join(f'<span class="status-chip">{escape(chip)}</span>' for chip in chips), unsafe_allow_html=True)


def reset_runtime_source_state(st: Any) -> None:
    for key in (
        "import_summary",
        "last_response",
        "data_source_label",
        "import_notice",
        "import_warning_count",
    ):
        st.session_state.pop(key, None)


def render_import_issues(st: Any, issues: list[SpreadsheetImportIssue], *, limit: int = 8) -> None:
    for issue in issues[:limit]:
        location = issue.sheet
        if issue.row_number is not None:
            location += f" row {issue.row_number}"
        st.write(f"- `{location}`: {issue.message}")
    if len(issues) > limit:
        st.write(f"- plus {len(issues) - limit} more issue(s)")


def render_workbook_refresh(st: Any, db_path: Path) -> None:
    st.subheader("Workbook Refresh")
    st.caption("Upload workbook or CSV drift data, then select one or more sheets to refresh analytics.")

    if "data_source_label" in st.session_state:
        st.info(f"Current source: {st.session_state.data_source_label}")
    if "import_notice" in st.session_state:
        st.success(f"Workbook loaded: {st.session_state.pop('import_notice')}")
    if "import_warning_count" in st.session_state:
        st.warning(
            f"Workbook imported with {st.session_state.pop('import_warning_count')} non-blocking warning(s)."
        )

    uploaded_file = st.file_uploader(
        "Upload workbook or CSV",
        type=["xlsx", "xlsm", "xls", "csv", "tsv"],
        accept_multiple_files=False,
        help="Selected workbook sheets, CSV files, and TSV files are profiled together and merged into fresh drift analytics data.",
    )

    if uploaded_file is None:
        return

    workbook_bytes = uploaded_file.getvalue()
    try:
        sheet_names = list_excel_sheets(workbook_bytes, filename=uploaded_file.name)
    except SpreadsheetImportError as exc:
        st.error("I could not read that workbook.")
        render_import_issues(st, exc.issues)
        return

    selected_sheets = st.multiselect(
        "Sheets to ingest",
        sheet_names,
        default=sheet_names,
        help="Selected sheets are combined and replace the current application drift dataset.",
    )

    if selected_sheets:
        preview_rows = preview_excel_workbook(workbook_bytes, selected_sheets, filename=uploaded_file.name)
        with st.expander("Import preflight", expanded=True):
            st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)

    with st.expander("Expected workbook columns"):
        st.write(
            "Supported formats: one flat drift sheet, or normalized sheets joined by `app_id` and `drift_id`."
        )
        st.caption(
            "Flat sheets need application name, approved version, detected version, and RTO score. "
            "Normalized exports can put app metadata in `Applications` and version drift in `Drift Instances`; "
            "the app profiles all selected sheets together, joins app metadata by `app_id`, and merges notes/evidence "
            "by `app_id + drift_id` when those keys exist."
        )

    if st.button("Refresh analytics from workbook", type="primary", use_container_width=True):
        clear_applications_rows(db_path)
        reset_runtime_source_state(st)
        try:
            result = import_excel_workbook(
                workbook_bytes,
                selected_sheets=selected_sheets,
                filename=uploaded_file.name,
            )
            replace_applications_rows(db_path, result.rows)
        except SpreadsheetImportError as exc:
            st.error("Workbook validation failed. Fix the sheet/row issues below and upload again.")
            render_import_issues(st, exc.issues)
            return

        st.session_state.import_summary = result
        st.session_state.data_source_label = (
            f"{uploaded_file.name}: {result.row_count} rows from {len(result.sheets)} sheet(s)"
        )
        st.session_state.pop("last_response", None)
        st.session_state.import_notice = st.session_state.data_source_label
        if result.warnings:
            st.session_state.import_warning_count = len(result.warnings)
        else:
            st.session_state.pop("import_warning_count", None)
        st.rerun()


def render_chart(st: Any, response: QueryResponse, *, compact: bool = False) -> None:
    if not response.chart or response.chart.kind == "table_only" or not response.rows:
        return
    if not response.chart.x or not response.chart.y:
        return

    df = response_to_dataframe(response)
    x_field = response.chart.x
    y_field = response.chart.y
    if x_field not in df.columns or y_field not in df.columns:
        return

    height = 230 if compact else max(260, min(430, 42 * len(df) + 70))
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X(f"{y_field}:Q", title=clean_label(y_field), axis=alt.Axis(grid=False)),
            y=alt.Y(f"{x_field}:N", sort="-x", title="", axis=alt.Axis(labelLimit=180)),
            color=alt.Color(f"{x_field}:N", legend=None, scale=alt.Scale(range=CHART_COLORS)),
            tooltip=[alt.Tooltip(column, title=clean_label(column)) for column in df.columns],
        )
        .properties(height=height)
    )

    st.markdown(f"#### {response.chart.title}")
    st.altair_chart(chart, use_container_width=True)


def risk_intelligence_response(db: DriftDatabase) -> QueryResponse:
    return run_query_plan(db, analytics.ai_risk_intelligence())


def risk_intelligence_frame(response: QueryResponse) -> pd.DataFrame:
    df = response_to_dataframe(response)
    if df.empty:
        return df
    df["risk_pressure"] = df["days_open"] * (11 - df["rto_score"])
    df["rto_tier"] = pd.Categorical(df["rto_tier"], categories=RTO_ORDER, ordered=True)
    df["ai_recommended_action"] = pd.Categorical(
        df["ai_recommended_action"], categories=ACTION_ORDER, ordered=True
    )
    return df


def render_ai_signal_cards(st: Any, df: pd.DataFrame) -> None:
    if df.empty:
        return

    top = df.sort_values(["ai_priority_score", "days_open"], ascending=[False, False]).iloc[0]
    product = (
        df.groupby("product", observed=False)
        .agg(apps=("app_id", "count"), average_score=("ai_priority_score", "mean"))
        .sort_values(["average_score", "apps"], ascending=[False, False])
        .reset_index()
        .iloc[0]
    )
    data_center = (
        df.groupby("data_center", observed=False)
        .agg(apps=("app_id", "count"), average_score=("ai_priority_score", "mean"))
        .sort_values(["average_score", "apps"], ascending=[False, False])
        .reset_index()
        .iloc[0]
    )
    interventions = int((df["ai_recommended_action"] == "Executive intervention").sum())

    cards = [
        (
            "Highest Priority",
            f"{top['app_name']}",
            f"Score {top['ai_priority_score']} | {top['days_open']} days | {top['rto_tier']}",
        ),
        (
            "Product Pressure",
            f"{product['product']}",
            f"{int(product['apps'])} apps | avg score {product['average_score']:.1f}",
        ),
        (
            "Location Pressure",
            f"{data_center['data_center']}",
            f"{int(data_center['apps'])} apps | avg score {data_center['average_score']:.1f}",
        ),
        (
            "Executive Actions",
            str(interventions),
            "Items scored at direct intervention threshold.",
        ),
    ]
    html = ['<div class="signal-grid">']
    for label, main, sub in cards:
        html.append(
            f"""
            <div class="signal-card">
                <div class="signal-label">{escape(label)}</div>
                <div class="signal-main">{escape(str(main))}</div>
                <div class="signal-sub">{escape(str(sub))}</div>
            </div>
            """
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def render_ai_risk_map(st: Any, response: QueryResponse, *, compact: bool = False) -> None:
    df = risk_intelligence_frame(response)
    if df.empty:
        st.info("No open drift items available for the risk map.")
        return

    height = 360 if compact else 470
    base = alt.Chart(df).encode(
        x=alt.X(
            "days_open:Q",
            title="Days open",
            axis=alt.Axis(grid=True),
            scale=alt.Scale(domain=[0, max(140, int(df["days_open"].max()) + 15)]),
        ),
        y=alt.Y(
            "rto_score:Q",
            title="RTO score (lower is more critical)",
            scale=alt.Scale(domain=[10.5, 0.5]),
        ),
    )
    points = base.mark_circle(opacity=0.88, stroke="#ffffff", strokeWidth=1.4).encode(
        size=alt.Size(
            "ai_priority_score:Q",
            title="AI priority",
            scale=alt.Scale(range=[140, 1300]),
        ),
        color=alt.Color(
            "ai_recommended_action:N",
            title="Recommended action",
            scale=alt.Scale(domain=ACTION_ORDER, range=[ACTION_COLORS[action] for action in ACTION_ORDER]),
        ),
        tooltip=[
            alt.Tooltip("app_name:N", title="Application"),
            alt.Tooltip("product:N", title="Product"),
            alt.Tooltip("data_center:N", title="Data center"),
            alt.Tooltip("rto_tier:N", title="RTO tier"),
            alt.Tooltip("days_open:Q", title="Days open"),
            alt.Tooltip("ai_priority_score:Q", title="AI priority"),
            alt.Tooltip("ai_recommended_action:N", title="Action"),
            alt.Tooltip("exemption_result:N", title="Exemption"),
        ],
    )
    thresholds = pd.DataFrame(
        {
            "threshold": [60, 90, 120],
            "stage": ["Warning", "Senior escalation", "Executive escalation"],
        }
    )
    threshold_rules = (
        alt.Chart(thresholds)
        .mark_rule(strokeDash=[6, 4], strokeWidth=1.4, opacity=0.75)
        .encode(
            x="threshold:Q",
            color=alt.Color(
                "stage:N",
                title="Aging threshold",
                scale=alt.Scale(range=["#F2A541", "#DC6803", "#B42318"]),
            ),
            tooltip=[alt.Tooltip("stage:N"), alt.Tooltip("threshold:Q", title="Days")],
        )
    )
    chart = (threshold_rules + points).properties(
        height=height,
        title="AI Risk Map: Criticality, Aging, and Governance State",
    )
    st.altair_chart(chart, use_container_width=True)


def render_product_rto_heatmap(st: Any, response: QueryResponse) -> None:
    df = risk_intelligence_frame(response)
    if df.empty:
        return
    heatmap = (
        df.groupby(["product", "rto_tier"], observed=False)
        .agg(drift_count=("app_id", "count"), avg_priority=("ai_priority_score", "mean"))
        .reset_index()
    )
    base = alt.Chart(heatmap).encode(
        x=alt.X("rto_tier:N", title="", sort=RTO_ORDER),
        y=alt.Y("product:N", title="", sort="-x"),
        tooltip=[
            alt.Tooltip("product:N", title="Product"),
            alt.Tooltip("rto_tier:N", title="RTO tier"),
            alt.Tooltip("drift_count:Q", title="Open drift"),
            alt.Tooltip("avg_priority:Q", title="Avg AI priority", format=".1f"),
        ],
    )
    rects = base.mark_rect(cornerRadius=3).encode(
        color=alt.Color(
            "avg_priority:Q",
            title="Avg AI priority",
            scale=alt.Scale(scheme="orangered"),
        )
    )
    labels = base.mark_text(fontWeight="bold", color="#101828").encode(text="drift_count:Q")
    st.altair_chart(
        (rects + labels).properties(height=max(260, 38 * heatmap["product"].nunique() + 70), title="Product x RTO Intelligence Matrix"),
        use_container_width=True,
    )


def render_escalation_lane(st: Any, response: QueryResponse) -> None:
    df = risk_intelligence_frame(response)
    if df.empty:
        return
    top = df.sort_values("ai_priority_score", ascending=False).head(12)
    base = alt.Chart(top).encode(
        x=alt.X("days_open:Q", title="Days open"),
        y=alt.Y("app_name:N", title="", sort=alt.SortField("ai_priority_score", order="descending")),
    )
    stem = base.mark_rule(color="#CBD5E1").encode(x2=alt.value(0))
    points = base.mark_circle(stroke="#ffffff", strokeWidth=1.4).encode(
        size=alt.Size("ai_priority_score:Q", title="AI priority", scale=alt.Scale(range=[160, 900])),
        color=alt.Color(
            "ai_recommended_action:N",
            title="Action",
            scale=alt.Scale(domain=ACTION_ORDER, range=[ACTION_COLORS[action] for action in ACTION_ORDER]),
        ),
        tooltip=[
            alt.Tooltip("app_name:N", title="Application"),
            alt.Tooltip("ai_priority_score:Q", title="AI priority"),
            alt.Tooltip("days_open:Q", title="Days open"),
            alt.Tooltip("rto_tier:N", title="RTO tier"),
            alt.Tooltip("ai_recommended_action:N", title="Action"),
        ],
    )
    thresholds = pd.DataFrame({"threshold": [60, 90, 120]})
    rules = alt.Chart(thresholds).mark_rule(strokeDash=[5, 5], color="#667085").encode(x="threshold:Q")
    st.altair_chart(
        (rules + stem + points).properties(height=390, title="Escalation Lane: Top AI-Prioritized Applications"),
        use_container_width=True,
    )


def render_action_stack(st: Any, response: QueryResponse) -> None:
    df = risk_intelligence_frame(response)
    if df.empty:
        return
    stack = (
        df.groupby(["product", "ai_recommended_action"], observed=False)
        .agg(apps=("app_id", "count"), avg_priority=("ai_priority_score", "mean"))
        .reset_index()
    )
    chart = (
        alt.Chart(stack)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            x=alt.X("apps:Q", title="Open drift count"),
            y=alt.Y("product:N", title="", sort="-x"),
            color=alt.Color(
                "ai_recommended_action:N",
                title="Recommended action",
                scale=alt.Scale(domain=ACTION_ORDER, range=[ACTION_COLORS[action] for action in ACTION_ORDER]),
            ),
            tooltip=[
                alt.Tooltip("product:N", title="Product"),
                alt.Tooltip("ai_recommended_action:N", title="Action"),
                alt.Tooltip("apps:Q", title="Apps"),
                alt.Tooltip("avg_priority:Q", title="Avg priority", format=".1f"),
            ],
        )
        .properties(height=330, title="Action Stack: Where Leadership Attention Concentrates")
    )
    st.altair_chart(chart, use_container_width=True)


def render_cluster_detector(st: Any, db: DriftDatabase) -> None:
    st.markdown('<div class="section-kicker">Drift Cluster Detector</div>', unsafe_allow_html=True)
    st.write(
        "Use the original detector to answer: did a wave of drift emerge across multiple applications "
        "for the same product update inside the selected number of days?"
    )

    controls = st.columns([0.9, 0.9, 1.2])
    min_drift_count = controls[0].slider(
        "Drift threshold",
        min_value=2,
        max_value=25,
        value=3,
        step=1,
        help="Minimum number of drift findings required to flag a cluster.",
    )
    window_days = controls[1].slider(
        "Day window",
        min_value=7,
        max_value=180,
        value=30,
        step=1,
        help="Rolling calendar window used to detect drift emergence.",
    )
    grouping_label = controls[2].selectbox(
        "Cluster definition",
        list(GROUPING_OPTIONS.keys()),
        index=0,
        help="Start with Product update. The other definitions narrow or broaden what counts as the same drift wave.",
    )
    st.caption(CLUSTER_GROUPING_EXPLANATIONS[grouping_label])
    with st.expander("What the cluster definitions mean"):
        for label, explanation in CLUSTER_GROUPING_EXPLANATIONS.items():
            st.write(f"**{label}:** {explanation}")

    source_response = run_query_plan(db, analytics.drift_cluster_source(limit=1000))
    if source_response.error:
        st.error(source_response.error)
        return
    if not source_response.rows:
        st.info("No in-scope drift rows are available for cluster detection.")
        return

    clusters = detect_drift_clusters(
        source_response.rows,
        min_drift_count=min_drift_count,
        window_days=window_days,
        group_fields=GROUPING_OPTIONS[grouping_label],
    )
    period_summary = summarize_cluster_periods(
        source_response.rows,
        min_drift_count=min_drift_count,
        window_days=window_days,
        group_fields=GROUPING_OPTIONS[grouping_label],
    )
    current_period = period_summary["current"]
    prior_period = period_summary["prior"]

    if not clusters:
        st.info(
            f"No clusters found where at least {min_drift_count} drift findings emerged within "
            f"{window_days} days for {grouping_label.lower()}."
        )
        period_cols = st.columns(4)
        period_cols[0].metric(
            f"Drift In Last {window_days}d",
            int(current_period["drift_count"]),
            delta=format_period_delta(current_period["drift_count"], prior_period["drift_count"], window_days),
            delta_color="inverse",
            help=f"Current period {current_period['start']} to {current_period['end']} vs prior same-length period.",
        )
        period_cols[1].metric(
            f"Clusters In Last {window_days}d",
            int(current_period["cluster_count"]),
            delta=format_period_delta(current_period["cluster_count"], prior_period["cluster_count"], window_days),
            delta_color="inverse",
            help="Clusters use the selected drift threshold, day window, and grouping rule.",
        )
        period_cols[2].metric(
            "Critical/High Drift",
            int(current_period["critical_high_count"]),
            delta=format_period_delta(
                current_period["critical_high_count"], prior_period["critical_high_count"], window_days
            ),
            delta_color="inverse",
            help="RTO score 1-4 detected in the current selected period vs the prior period.",
        )
        period_cols[3].metric(
            "Largest Cluster",
            int(current_period["largest_cluster"]),
            delta=format_period_delta(current_period["largest_cluster"], prior_period["largest_cluster"], window_days),
            delta_color="inverse",
            help="Largest cluster inside the current selected period vs the prior period.",
        )
        with st.expander("Source rows inspected"):
            render_data_table(st, source_response, height=260)
        return

    cluster_df = pd.DataFrame(clusters)
    st.markdown(
        f"""
        <div class="answer-card">
            <div class="section-kicker">Detector Answer</div>
            <div class="brief-copy">
            Found {len(cluster_df)} cluster(s) where at least {min_drift_count} drift findings emerged within
            {window_days} days for {escape(grouping_label.lower())}.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.caption(
        f"KPI tickers compare {current_period['start']} to {current_period['end']} "
        f"against the prior {window_days}-day period."
    )
    signal_cols = st.columns(4)
    signal_cols[0].metric(
        f"Drift In Last {window_days}d",
        int(current_period["drift_count"]),
        delta=format_period_delta(current_period["drift_count"], prior_period["drift_count"], window_days),
        delta_color="inverse",
        help="All in-scope drift findings detected in the selected period vs the immediately prior period.",
    )
    signal_cols[1].metric(
        f"Clusters In Last {window_days}d",
        int(current_period["cluster_count"]),
        delta=format_period_delta(current_period["cluster_count"], prior_period["cluster_count"], window_days),
        delta_color="inverse",
        help="Clusters use the selected drift threshold, day window, and grouping rule.",
    )
    signal_cols[2].metric(
        "Critical/High Drift",
        int(current_period["critical_high_count"]),
        delta=format_period_delta(current_period["critical_high_count"], prior_period["critical_high_count"], window_days),
        delta_color="inverse",
        help="RTO score 1-4 detected in the current selected period vs the prior period.",
    )
    signal_cols[3].metric(
        "Largest Cluster",
        int(current_period["largest_cluster"]),
        delta=format_period_delta(current_period["largest_cluster"], prior_period["largest_cluster"], window_days),
        delta_color="inverse",
        help="Largest cluster inside the current selected period vs the prior period.",
    )

    ranked_clusters = cluster_df.copy()
    ranked_clusters["period"] = ranked_clusters["first_detected"] + " to " + ranked_clusters["last_detected"]
    ranked_clusters["risk_score"] = ranked_clusters["drift_count"] + ranked_clusters["critical_high_count"]
    ranked_clusters = ranked_clusters.sort_values(
        ["risk_score", "drift_count", "critical_high_count", "first_detected"],
        ascending=[False, False, False, False],
    ).head(12)

    st.markdown("#### Highest-priority drift waves")
    st.caption(
        "This ranked view shows which clusters matter most. Bar length is drift count; color intensity is the "
        "number of critical/high findings. Dates are shown as evidence, not as a trend axis."
    )
    bars = (
        alt.Chart(ranked_clusters)
        .mark_bar(cornerRadiusEnd=4)
        .encode(
            x=alt.X("drift_count:Q", title="Drift findings in cluster", axis=alt.Axis(grid=False, tickMinStep=1)),
            y=alt.Y(
                "cluster_key:N",
                title="",
                sort=alt.SortField("risk_score", order="descending"),
                axis=alt.Axis(labelLimit=300),
            ),
            color=alt.Color(
                "critical_high_count:Q",
                title="Critical/high",
                scale=alt.Scale(range=["#FEE4B8", "#F97066", "#B42318"]),
            ),
            tooltip=[
                alt.Tooltip("cluster_key:N", title="Cluster"),
                alt.Tooltip("drift_count:Q", title="Drift count"),
                alt.Tooltip("unique_app_count:Q", title="Apps"),
                alt.Tooltip("critical_high_count:Q", title="Critical/high"),
                alt.Tooltip("period:N", title="Detected period"),
                alt.Tooltip("span_days:Q", title="Span days"),
                alt.Tooltip("sample_apps:N", title="Sample apps"),
                alt.Tooltip("data_centers:N", title="Data centers"),
            ],
        )
        .properties(height=max(300, min(560, 34 * len(ranked_clusters) + 70)))
    )
    labels = (
        alt.Chart(ranked_clusters)
        .mark_text(align="left", baseline="middle", dx=6, color="#344054", fontSize=12)
        .encode(
            x=alt.X("drift_count:Q"),
            y=alt.Y("cluster_key:N", sort=alt.SortField("risk_score", order="descending")),
            text=alt.Text("period:N"),
        )
    )
    st.altair_chart(bars + labels, use_container_width=True)

    evidence_columns = [
        "cluster_key",
        "drift_count",
        "unique_app_count",
        "critical_high_count",
        "open_count",
        "first_detected",
        "last_detected",
        "span_days",
        "sample_apps",
        "data_centers",
    ]
    st.dataframe(
        ranked_clusters[evidence_columns],
        use_container_width=True,
        hide_index=True,
        height=320,
    )

    with st.expander("SQL source used by detector"):
        st.code(source_response.sql or "", language="sql")


def render_ai_graph_suite(st: Any, response: QueryResponse) -> None:
    df = risk_intelligence_frame(response)
    st.markdown('<div class="section-kicker">AI Risk Intelligence Graphs</div>', unsafe_allow_html=True)
    st.write(
        "An explainable priority score combines RTO criticality, aging thresholds, and exemption state into "
        "a decision-grade visual model."
    )
    render_ai_signal_cards(st, df)

    map_tab, matrix_tab, escalation_tab, action_tab = st.tabs(
        ["Risk Map", "Product x RTO Matrix", "Escalation Lane", "Action Stack"]
    )
    with map_tab:
        render_ai_risk_map(st, response)
    with matrix_tab:
        render_product_rto_heatmap(st, response)
    with escalation_tab:
        render_escalation_lane(st, response)
    with action_tab:
        render_action_stack(st, response)


def render_data_table(st: Any, response: QueryResponse, *, height: int | None = None) -> None:
    if not response.rows:
        st.info("No rows returned.")
        return
    st.dataframe(
        response_to_dataframe(response),
        use_container_width=True,
        hide_index=True,
        height=height,
    )


def render_response(st: Any, response: QueryResponse) -> None:
    if response.error:
        st.error(response.error)
        if response.sql:
            with st.expander("Generated SQL"):
                st.code(response.sql, language="sql")
        return

    st.markdown(
        f"""
        <div class="answer-card">
            <div class="section-kicker">Agent Answer</div>
            <div class="brief-copy">{escape(response.answer)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if response.warnings:
        for warning in response.warnings:
            st.info(warning)

    table_tab, chart_tab, sql_tab = st.tabs(["Result Table", "Chart", "SQL Evidence"])
    with table_tab:
        render_data_table(st, response, height=330)
    with chart_tab:
        render_chart(st, response)
    with sql_tab:
        st.code(response.sql or "", language="sql")


def database_row_count(db: DriftDatabase) -> int:
    value = db.scalar("SELECT COUNT(*) FROM applications")
    return int(value or 0)


def is_default_demo_database(db_path: Path) -> bool:
    return db_path.name == DEFAULT_DB_PATH.name


def ensure_recruiter_demo_data(db_path: Path, row_count: int) -> tuple[int, str | None]:
    """Keep the public demo from landing on the upload screen if the bundled DB is empty."""

    if row_count > 0 or not is_default_demo_database(db_path):
        return row_count, None

    if DEMO_ROWS_PATH.exists():
        rows = json.loads(DEMO_ROWS_PATH.read_text(encoding="utf-8"))
        replace_applications_rows(db_path, rows)
        seeded_db = DriftDatabase(db_path, max_rows=DEFAULT_ROW_LIMIT)
        return database_row_count(seeded_db), DEMO_DATA_SOURCE_LABEL

    initialize_demo_database(db_path, reset=True)
    fallback_db = DriftDatabase(db_path, max_rows=DEFAULT_ROW_LIMIT)
    return database_row_count(fallback_db), FALLBACK_DEMO_DATA_SOURCE_LABEL


def render_tableau_embed(st: Any) -> None:
    st.markdown('<div class="section-kicker">Tableau Executive Views</div>', unsafe_allow_html=True)
    if not TABLEAU_DASHBOARD_URL:
        st.markdown(
            """
            <div class="brief-card">
                <div class="brief-title">Tableau dashboard slot ready</div>
                <div class="brief-copy">
                Publish the NorthStar executive dashboard in Tableau, then set TABLEAU_DASHBOARD_URL
                to embed the live view here. Until then, this Streamlit layer carries the KPI narrative,
                agent workspace, and cluster detector.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    import streamlit.components.v1 as components

    geo_url = escape(TABLEAU_GEO_VIEW_URL or TABLEAU_DASHBOARD_URL, quote=True)
    kpi_url = escape(TABLEAU_KPI_VIEW_URL, quote=True)
    st.markdown(
        f"""
        <div class="brief-card">
            <div class="brief-title">Open Tableau views</div>
            <div class="brief-copy">
                <a href="{geo_url}" target="_blank" rel="noopener noreferrer">Geographic Risk Command Center</a>
                &nbsp;|&nbsp;
                <a href="{kpi_url}" target="_blank" rel="noopener noreferrer">Executive KPI Detail</a>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    embed_url = escape(tableau_public_view_url(TABLEAU_DASHBOARD_URL), quote=True)
    components.html(
        f"""
        <script type="module" src="https://public.tableau.com/javascripts/api/tableau.embedding.3.latest.min.js"></script>
        <div style="width:100%; overflow-x:auto; background:#fff;">
            <div style="width:{TABLEAU_EMBED_WIDTH}px; max-width:100%; margin:0 auto;">
            <tableau-viz
                id="northstar-tableau"
                src="{embed_url}"
                toolbar="hidden"
                device="desktop"
                hide-tabs
                style="width:100%; height:{TABLEAU_EMBED_HEIGHT}px;">
            </tableau-viz>
            </div>
        </div>
        """,
        width=TABLEAU_EMBED_WIDTH,
        height=TABLEAU_EMBED_HEIGHT + 20,
        scrolling=True,
    )


def render_board_brief(st: Any, db: DriftDatabase, summary: ExecutiveSummary) -> None:
    risk_response = risk_intelligence_response(db)
    open_drift = metric_by_label(summary, "Open Drift")
    mission_critical = metric_by_label(summary, "Mission Critical")
    executive = metric_by_label(summary, "Executive Escalations")
    pending = metric_by_label(summary, "Pending Exemptions")

    metric_columns = st.columns(4)
    render_metric_card(metric_columns[0], open_drift, "Open in-scope drift findings requiring ownership.", "warn")
    render_metric_card(metric_columns[1], mission_critical, "RTO score 1-2; remediation should lead the agenda.", "alert")
    render_metric_card(metric_columns[2], executive, "Aged 120+ days and ready for executive escalation.", "alert")
    render_metric_card(metric_columns[3], pending, "Awaiting governance decision before the clock worsens.", "good")
    if "import_summary" in st.session_state:
        result = st.session_state.import_summary
        st.caption(f"Workbook source active: {result.row_count} rows loaded from {len(result.sheets)} sheet(s).")

    st.write("")
    left, right = st.columns([1.1, 0.9])
    with left:
        st.markdown(
            """
            <div class="brief-card">
                <div class="brief-title">Board Narrative</div>
                <div class="brief-copy">
                The portfolio has active application-version drift concentrated in a small number of products
                and hosting locations. The right operating move is to separate immediate remediation from
                governance decisions: burn down Mission Critical and High risk first, then resolve exemptions
                before aged drift becomes a recurring executive control issue.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        st.markdown('<div class="section-kicker">Recommended Executive Moves</div>', unsafe_allow_html=True)
        for area in summary.focus_areas:
            st.write(f"- {area}")

    st.write("")
    render_ai_risk_map(st, risk_response, compact=True)
    st.write("")
    chart_columns = st.columns(2)
    for index, response in enumerate(summary.charts[:4]):
        with chart_columns[index % 2]:
            render_chart(st, response, compact=True)


def render_command_center(st: Any, db: DriftDatabase, summary: ExecutiveSummary) -> None:
    open_drift = metric_by_label(summary, "Open Drift")
    mission_critical = metric_by_label(summary, "Mission Critical")
    executive = metric_by_label(summary, "Executive Escalations")
    pending = metric_by_label(summary, "Pending Exemptions")

    metric_columns = st.columns(4)
    render_metric_card(metric_columns[0], open_drift, "Active portfolio drift requiring accountable owners.", "warn")
    render_metric_card(metric_columns[1], mission_critical, "RTO 1-2 systems where drift creates outage exposure.", "alert")
    render_metric_card(metric_columns[2], executive, "Aged 120+ days and suitable for leadership escalation.", "alert")
    render_metric_card(metric_columns[3], pending, "Governance decisions that can unblock remediation.", "good")

    st.write("")
    left, right = st.columns([1.1, 0.9])
    with left:
        st.markdown(
            """
            <div class="brief-card">
                <div class="brief-title">Executive Narrative</div>
                <div class="brief-copy">
                NorthStar Financial is losing operating leverage because version drift is aging across
                high-criticality applications. The executive job is not to inspect every row; it is to see
                where risk concentrates, force decisions on overdue exemptions, and sponsor the few
                remediation waves that remove the most outage and audit exposure.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        st.markdown('<div class="section-kicker">Board-Level Moves</div>', unsafe_allow_html=True)
        for area in summary.focus_areas[:4]:
            st.write(f"- {area}")

    st.write("")
    render_analyst_workbench(st, db)
    st.write("")
    with st.expander("Why drift matters and what the agent is reasoning over"):
        render_story_primer(st)
        st.write("")
        render_sample_data(st, db)


def render_tableau_board_view(st: Any, db: DriftDatabase) -> None:
    risk_response = risk_intelligence_response(db)
    st.markdown(
        """
        <div class="brief-card">
            <div class="brief-title">Board-ready visual layer</div>
            <div class="brief-copy">
            Tableau is embedded here as the executive reporting view. The Streamlit command center remains
            the product shell for agent questions, SQL evidence, cluster detection, and workflow guardrails.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.write("")
    render_tableau_embed(st)
    st.write("")
    with st.expander("Streamlit fallback risk map", expanded=not bool(TABLEAU_DASHBOARD_URL)):
        render_ai_risk_map(st, risk_response, compact=True)


def render_story_primer(st: Any) -> None:
    st.markdown('<div class="section-kicker">Why Drift Matters</div>', unsafe_allow_html=True)
    st.markdown(
        """
        <div class="brief-card">
            <div class="brief-title">The NorthStar problem</div>
            <div class="brief-copy">
            A financial-services portfolio can look stable while unsupported versions quietly pile up.
            Each missed update increases breach exposure, audit evidence gaps, recovery fragility, and
            the chance that a small production mismatch becomes an expensive incident.
            </div>
        </div>
        <div class="incident-card">
            <div class="incident-title">Real-world failure pattern: Knight Capital, August 1, 2012</div>
            <div class="incident-copy">
            Knight Capital deployed a software update that accidentally reactivated deprecated trading logic on
            part of its server fleet. Within 45 minutes, erroneous trades created roughly $440 million in losses.
            The lesson for NorthStar is not that application drift always causes trading losses; it is that
            undetected version mismatch can turn a routine technology gap into an executive-level event.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sample_data(st: Any, db: DriftDatabase) -> None:
    response = run_query_plan(db, analytics.top_drifting_apps(limit=6))
    st.markdown('<div class="section-kicker">Sample Rows The Agent Reasons Over</div>', unsafe_allow_html=True)
    render_data_table(st, response, height=205)


def render_cluster_lab(st: Any, db: DriftDatabase) -> None:
    render_story_primer(st)
    st.write("")
    render_sample_data(st, db)
    st.write("")
    render_cluster_detector(st, db)
    st.write("")
    with st.expander("Control evidence and SQL guardrails"):
        render_evidence_center(st, db)


def render_agent_workspace(st: Any, db: DriftDatabase) -> None:
    render_cluster_lab(st, db)


def render_decision_center(st: Any, db: DriftDatabase) -> None:
    st.markdown('<div class="section-kicker">Decision Center</div>', unsafe_allow_html=True)
    st.write("Prioritized risk queues for operating review, remediation assignment, and governance escalation.")

    critical = run_query_plan(db, analytics.critical_apps_with_open_drift(include_high=True))
    executive = run_query_plan(db, analytics.executive_escalation_candidates())

    left, right = st.columns(2)
    with left:
        render_response(st, critical)
    with right:
        render_response(st, executive)


def render_question_buttons(st: Any) -> None:
    st.markdown('<div class="section-kicker">Board Questions</div>', unsafe_allow_html=True)
    columns = st.columns(3)
    for index, question in enumerate(SUGGESTED_QUESTIONS):
        if columns[index % 3].button(question, use_container_width=True):
            st.session_state.question = question


def render_analyst_workbench(st: Any, db: DriftDatabase) -> None:
    if "question" not in st.session_state:
        st.session_state.question = SUGGESTED_QUESTIONS[0]

    render_question_buttons(st)
    st.write("")

    with st.form("analysis_form"):
        question = st.text_area(
            "Ask a drift analytics question",
            value=st.session_state.question,
            height=96,
            help="The generated SQL is still validated by the read-only safety layer before execution.",
        )
        submitted = st.form_submit_button("Run Analysis", type="primary", use_container_width=True)

    if submitted:
        st.session_state.question = question
        st.session_state.last_response = answer_question(db, question)

    if "last_response" in st.session_state:
        render_response(st, st.session_state.last_response)
    else:
        st.info("Choose a suggested question or type your own, then run the agent when you want SQL-backed evidence.")


def render_evidence_center(st: Any, db: DriftDatabase) -> None:
    st.markdown('<div class="section-kicker">Control Evidence</div>', unsafe_allow_html=True)
    render_status_chips(st)

    guardrail_left, guardrail_right = st.columns([0.95, 1.05])
    with guardrail_left:
        st.markdown("#### SQL Guardrails")
        st.write("- Only one `SELECT` statement may execute.")
        st.write("- Write and DDL keywords are blocked before SQLite execution.")
        st.write("- Table and column names are validated against the drift schema.")
        st.write("- Missing limits are added and oversized limits are reduced.")
    with guardrail_right:
        st.markdown("#### Approved Schema")
        st.code(
            """
applications(
  finding_id, app_id, drift_id, app_name,
  business_owner, technology_owner, product, data_center,
  approved_version, detected_version, rto_score, status,
  exemption_requested, exemption_result, in_scope,
  drift_detected_on, last_scanned_at, note_count,
  latest_note_date, latest_note_type, latest_note_author_role,
  latest_note_text, escalation_flag
)
""".strip(),
            language="sql",
        )

    if "last_response" in st.session_state:
        st.markdown("#### Latest Query Evidence")
        st.code(st.session_state.last_response.sql or "", language="sql")


def main() -> None:
    import streamlit as st

    st.set_page_config(
        page_title="AI Drift Analytics Agent",
        layout="wide",
        initial_sidebar_state="expanded" if SHOW_PORTFOLIO_CONTROLS else "collapsed",
    )
    if st.session_state.get("app_state_version") != APP_STATE_VERSION:
        reset_runtime_source_state(st)
        st.session_state.app_state_version = APP_STATE_VERSION

    db_path = DEFAULT_DB_PATH
    row_limit = DEFAULT_ROW_LIMIT
    initialize_empty_database(db_path)

    if not SHOW_PORTFOLIO_CONTROLS:
        st.markdown(
            """
            <style>
                [data-testid="stSidebar"],
                [data-testid="collapsedControl"] {
                    display: none;
                }
            </style>
            """,
            unsafe_allow_html=True,
        )

    if SHOW_PORTFOLIO_CONTROLS:
        with st.sidebar:
            st.header("Portfolio Controls")
            db_path = Path(st.text_input("SQLite database", value=str(DEFAULT_DB_PATH)))
            row_limit = st.slider("Max rows", min_value=25, max_value=1000, value=DEFAULT_ROW_LIMIT, step=25)
            initialize_empty_database(db_path)
            if st.button("Clear workbook data", use_container_width=True):
                clear_applications_rows(db_path)
                reset_runtime_source_state(st)
                st.success("Workbook data cleared.")
            st.divider()
            render_workbook_refresh(st, db_path)
            st.divider()
            st.caption("Operating guardrails")
            render_status_chips(st)

    db = DriftDatabase(db_path, max_rows=row_limit)
    row_count = database_row_count(db)
    row_count, fallback_label = ensure_recruiter_demo_data(db_path, row_count)
    if fallback_label:
        db = DriftDatabase(db_path, max_rows=row_limit)
        st.session_state.data_source_label = fallback_label
    if row_count > 0 and "data_source_label" not in st.session_state:
        if is_default_demo_database(db_path):
            st.session_state.data_source_label = DEMO_DATA_SOURCE_LABEL
        else:
            st.session_state.data_source_label = f"{db_path.name}: {row_count} rows loaded"

    if row_count == 0:
        render_upload_landing(st)
        return

    summary = build_executive_summary(db)
    render_shell_header(st, summary)

    command_tab, tableau_tab, cluster_tab = st.tabs(
        ["Executive Command Center", "Tableau Board View", "Cluster Detection Lab"]
    )
    with command_tab:
        render_command_center(st, db, summary)
    with tableau_tab:
        render_tableau_board_view(st, db)
    with cluster_tab:
        render_cluster_lab(st, db)


if __name__ == "__main__":
    main()
