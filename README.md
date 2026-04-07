# AI Drift Analytics Agent

An agentic AI system that monitors software version drift across an enterprise application portfolio, identifies concentration risk, prioritizes remediation, and delivers director-level insights through natural language.

Built on **LangGraph + GPT-4o-mini + SQLite**, this project demonstrates production-style agentic AI applied to a real enterprise resiliency use case.

## 🚀 Live Demo

**Try it now:** [https://ai-drift-analytics-agent-hrhgjperokzzvgytvv8yxq.streamlit.app](https://ai-drift-analytics-agent-hrhgjperokzzvgytvv8yxq.streamlit.app)

Click any suggested question or type your own — no setup required.

---

## What is Drift?

In enterprise technology, **drift** occurs when a software component running on a production system falls behind the organization's approved version standard. Unresolved drift creates compliance violations, security vulnerabilities, and resiliency risk — especially for mission-critical applications.

This agent automates the work of a resiliency analyst: detecting where drift is concentrated, which applications are most at risk, and who should be contacted first.

---

## What the Agent Can Do

Ask it anything about the portfolio in plain English:

1. What percentage of in-scope applications are currently drifting?
2. Which datacenter has the highest concentration of open drift?
3. Which resiliency lead should we escalate to first?
4. Show me critical applications with approved exemptions that are still open
5. Which product category has the most drift instances over 90 days?
6. Which line of business has the most unresolved drift?
7. Show me all applications drifting over 120 days with no exemption

The agent generates SQL, queries the database, and returns a concise analyst-style briefing — no hardcoded queries, no dashboards to navigate.

---

## How It Works

User Question
│
▼
[Node 1] Generate SQL
LLM reads schema + domain context
Produces valid SQLite query
│
▼
[Node 2] Execute Query
Runs SQL against northstar_drift.db
Returns raw result set
│
▼
[Node 3] Generate Answer
LLM interprets results
Returns director-level briefing
│
▼
Answer

Built with **LangGraph** — each step is a discrete node in a stateful graph, making the reasoning pipeline transparent, testable, and extensible.

---

## Dataset

→ [Full dataset design documentation](DATASET.md)

The agent runs against a fully synthetic enterprise dataset modeled after real financial institution resiliency programs:

| Table | Records | Description |
|-------|---------|-------------|
| `applications` | 1,000 | Full enterprise app portfolio with RTO scores, datacenters, owners |
| `drift_instances` | ~475 | Software drift events with aging, exemption status, root cause |
| `drift_notes` | ~800 | Analyst notes, escalation flags, exemption documentation |
| `drift_full` | view | Pre-joined view for efficient agent querying |

**Key design decisions:**
- ~78% of apps marked in-scope, ~28% drift penetration — modeled after real program distributions
- Year-aware drift IDs (`DR-2025-000001`) support historical and geographic concentration analysis
- RTO scores 1–10 map to recovery time windows (0.5hrs → 120hrs), enabling true criticality-based prioritization
- Instance names encode LOB + product + environment (`PAYORAPRD01`, `RSKMQPRD02`) for realistic demo fidelity
- Exemption logic includes Approved / Pending / Denied states with realistic documentation notes
- 12 months of drift history enables trend and aging analysis

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Framework | LangGraph |
| LLM | GPT-4o-mini (OpenAI) |
| LLM Orchestration | LangChain |
| Data Layer | SQLite |
| Data Processing | Pandas, NumPy |
| Synthetic Data | Faker |
| Output Validation | Pydantic |
| Interface | CLI (FastAPI planned) |

---

## Project Structure

ai-drift-analytics-agent/
│
├── notebooks/
│   └── generate_drift_dataset.ipynb   # Full dataset generation pipeline
│
├── data/
│   └── processed/
│       ├── northstar_drift.db         # SQLite database
│       ├── applications.csv
│       ├── drift_instances.csv
│       └── drift_notes.csv
│
├── src/
│   ├── agent.py       # LangGraph agent — SQL generation + answer synthesis
│   ├── tools.py       # Database query tool + schema definition
│   ├── cli.py         # Interactive CLI interface
│   └── config.py      # Environment config, constants, lookup tables
│
├── sql/
│   └── validation_queries.sql
│
├── requirements.txt
└── .env.example

---

## Quickstart
```bash
# Clone the repo
git clone https://github.com/David-P23/ai-drift-analytics-agent.git
cd ai-drift-analytics-agent

# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate  # Windows
source venv/bin/activate  # Mac/Linux

# Install dependencies
pip install -r requirements.txt

# Add your OpenAI API key
cp .env.example .env
# Edit .env and add: OPENAI_API_KEY=your-key-here

# Run the agent
python -m src.cli
```

---

## Domain Context

This project is modeled on enterprise Technology Resiliency controls used in large financial institutions. Key concepts:

- **RTO (Recovery Time Objective)** — maximum tolerable downtime per application. Scores 1–2 = Mission Critical, 3–4 = High, 5–6 = Medium, 7–10 = Lower priority
- **Escalation thresholds** — 60 / 90 / 120 days of open drift trigger progressive escalation to application owners, resiliency leads, and executive stakeholders
- **Exemptions** — some applications cannot be upgraded immediately due to vendor compatibility issues; exemptions are formally requested, reviewed, and tracked
- **Geographic concentration risk** — drift clustering within a single datacenter can amplify the impact of an infrastructure failure event

---

## Background

This agent was designed to replace manual resiliency reporting workflows — the kind that required analysts to maintain escalation trackers, generate aging reports, and identify bulk remediation opportunities across hundreds of applications every month.

The domain expertise behind the data model and agent logic comes from hands-on experience managing these controls in a production enterprise environment.

---

*Built as a portfolio project demonstrating agentic AI applied to enterprise analytics. Company name and data are fictional.*