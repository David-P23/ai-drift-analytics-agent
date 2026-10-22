# AI Drift Analytics Agent

Executive dashboard and analytics-agent demo for enterprise application drift risk.

This v2 version opens directly on a bundled NorthStar Financial demo portfolio, so recruiters and reviewers can see the story immediately without uploading a workbook or waking a sleeping free-tier app. The app keeps the original natural-language analytics agent, but reframes the project as an Executive Command Center with Tableau as a board-ready visual layer and cluster detection as a separate analyst lab.

The original v1 Streamlit agent is preserved in the `v1-streamlit-agent` branch/tag. The prior Streamlit Cloud deployment has been retired; v1 is now shown as an archived milestone through source history and screenshots.

## Live Demo

Recruiter-facing app: https://ai-drift-analytics-agent.onrender.com

The live demo runs on Render Starter using the included Docker/Render files. After the Tableau dashboard is published, set `TABLEAU_DASHBOARD_URL` in Render environment variables to embed the live Tableau view inside the Tableau Board View page.

![v2 Executive Dashboard](docs/assets/v2-executive-dashboard.png)

## Project Evolution

### v1: Agent Concept

V1 proved the core idea: an analytics agent could answer natural-language governance questions over a synthetic enterprise application-drift portfolio. It introduced the company story, sample data, suggested prompts, and the risk framing around unremediated software drift.

V1 archive:

- Branch/tag: `v1-streamlit-agent`
- GitHub view: https://github.com/David-P23/ai-drift-analytics-agent/tree/v1-streamlit-agent

![v1 drift story](docs/assets/v1-drift-story.png)

![v1 agent questions](docs/assets/v1-agent-questions.png)

### v2: Executive Command Center

V2 reframes the same concept for a recruiter and executive audience. It opens directly on seeded demo data, moves the first impression to executive KPIs, board-level narrative, and an agent input, then provides Tableau and cluster detection as supporting pages.

### v3: Databricks Lakehouse

V3 moves the agent's data onto Databricks as a bronze → silver → gold pipeline and simulates a
post-acquisition integration: the portfolio arrives from three simulated acquired companies
with different IDs, column names, codes and date formats, plus seeded data-quality defects. Silver puts
them on shared definitions through crosswalk and code-map tables; gold rebuilds the agent's
table as a Delta table behind a view. The agent
runs on either backend (`DRIFT_BACKEND=sqlite` or `databricks`) with the same validated query
templates, translated to Databricks SQL.

Built and run on Databricks Free Edition: source generation, bronze (`COPY INTO`), the silver
conformance layer (crosswalks, code maps, quarantine with reasons, `MERGE` upserts) and gold.
`05_checks` passes all 21 checks, including row accounting from bronze through silver and gold
reproducing the SQLite demo row for row. Local Spark tests in CI show every agent query returns
identical results on both engines. The agent also runs against the workspace's SQL warehouse:
`scripts/check_databricks.py` passes all 16 checks, including the nine suggested questions
answered identically on both backends. Next: LLM tagging of note themes from a fixed label list,
and a scheduled job. See [`lakehouse/README.md`](lakehouse/README.md).

## What It Does

- Opens with bundled NorthStar demo data from `data/northstar_demo.sqlite`.
- Presents three primary pages:
  - `Executive Command Center`: KPI cards, board narrative, recommended moves, suggested questions, agent answer, SQL evidence, and a compact data/story primer.
  - `Tableau Board View`: embedded Tableau executive dashboard plus a Streamlit fallback risk map.
  - `Cluster Detection Lab`: drift story primer, sample rows, cluster detector, cluster evidence table, and guardrails.
- Detects drift waves when a configurable number of findings emerge within a configurable rolling window for the same product update.
- Supports alternate cluster definitions with plain-English explanations.
- Uses an optional structured LLM planner to interpret natural-language follow-ups into approved analytics intents and filters.
- Answers "what does X mean" questions from a glossary of business definitions (built from the same rules as the SQL), with the term's current figure, and politely declines questions outside the drift portfolio.
- Runs analytics through validated read-only SQLite `SELECT` queries; the LLM never generates executable SQL.
- Retrieves relevant passages from a labeled synthetic NorthStar policy corpus with embeddings, then grounds policy guidance in the retrieved sources.
- Supports optional workbook/CSV refresh from normalized or flat drift exports.

## Domain Rules

- Drift means `detected_version <> approved_version`.
- `rto_score` 1-2 is Mission Critical.
- `rto_score` 3-4 is High.
- `rto_score` 5-6 is Medium.
- `rto_score` 7-10 is Low.
- Aging thresholds are 60, 90, and 120 days.
- Aging is point-in-time: days open are counted from `drift_detected_on` to the finding's `last_scanned_at` date, not to today, so the KPIs describe the portfolio as of its latest scan (the bundled demo is as of April 3, 2026).
- Drift cluster detection defaults to `product + approved_version`, the original "same product update" definition.

## Project Structure

```text
app.py                    Streamlit UI
data/northstar_demo.sqlite Bundled recruiter-demo database
src/agent.py              Agent orchestration and executive summary
src/analytics.py          Reusable drift analytics SQL plans
src/cluster_detection.py  Rolling-window drift cluster detector
src/database.py           SQLite setup and read-only execution
src/domain.py             Domain rules and schema metadata
src/models.py             Pydantic structured response objects
src/prompting.py          Prompt builder and deterministic question routing
src/spreadsheet_import.py Excel workbook normalization and validation
src/sql_safety.py         SQL validation and LIMIT enforcement
src/sql_dialect.py        SQLite -> Databricks SQL translation for the query templates
src/warehouse.py          Databricks SQL warehouse backend (DRIFT_BACKEND=databricks)
lakehouse/                Databricks pipeline: source generator, notebooks, gold SQL, contracts
tests/                    Safety, import, database, cluster, smoke, lakehouse, and Spark parity tests
```

## Local Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
streamlit run app.py
```

The app defaults to:

```text
DRIFT_DB_PATH=data/northstar_demo.sqlite
SQL_ROW_LIMIT=1000
TABLEAU_DASHBOARD_URL=
OPENAI_MODEL=gpt-5-mini
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

To enable the optional LLM planner, configure `OPENAI_API_KEY` as a local environment variable or Render secret. If the key is absent or the provider is unavailable, the deterministic question router continues to run the demo.

## Policy RAG

The `docs/policies/` corpus is a fictional NorthStar demo corpus, not legal or regulatory advice. Policy-oriented questions retrieve the most relevant document sections using OpenAI embeddings when available, fall back to lexical matching when they are not, and show the source sections used in the answer.

## Tableau Embed

Publish the Tableau executive dashboard, then configure:

```text
TABLEAU_DASHBOARD_URL=https://public.tableau.com/views/...
```

Until that variable is set, the app shows a professional placeholder plus a fallback Streamlit risk map.

## Workbook Refresh

The sidebar upload flow supports `.xlsx`, `.xlsm`, `.xls`, `.csv`, and `.tsv` files. It can ingest either:

- Flat drift sheets with application name, approved version, detected version, and RTO score.
- Normalized exports with `Applications`, `Drift Instances`, and `Drift Notes`-style sheets joined by `app_id` and `drift_id`.

## Deployment

Recommended for the public portfolio link:

```text
Render Starter
```

The repo includes:

```text
.streamlit/config.toml
.dockerignore
Dockerfile
render.yaml
Procfile
runtime.txt
DEPLOYMENT.md
```

See `DEPLOYMENT.md` for details.

## Tests

```powershell
pytest
```

The suite covers cluster detection, spreadsheet import, SQL safety, prompt routing, database analytics, and Streamlit import smoke behavior.
With `pyspark` installed it also runs the lakehouse tests on local Spark (gold reconciliation and SQLite/Spark SQL parity); CI runs both on every push.

## Notes

NorthStar Financial is a fictional company and the data is synthetic. This is a portfolio-grade analytics demo, not a production control plane. For a real enterprise deployment, pair the same pattern with service authentication, centralized audit logging, row-level authorization, and governed read-replica access.
