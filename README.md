# 🤖 AI Drift Analytics Agent
### NorthStar Financial | Technology Resiliency Intelligence Platform

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python&logoColor=white)
![LangChain](https://img.shields.io/badge/LangChain-0.2+-1C3C3C?style=flat)
![LangGraph](https://img.shields.io/badge/LangGraph-Latest-4B8BBE?style=flat)
![OpenAI](https://img.shields.io/badge/OpenAI-GPT--4o-412991?style=flat&logo=openai&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.111+-009688?style=flat&logo=fastapi&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-3-003B57?style=flat&logo=sqlite&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-v2-E92063?style=flat)

> A production-style agentic AI system that automates the analysis of application drift risk across an enterprise technology portfolio — enabling natural-language querying, intelligent escalation detection, and AI-generated insights for technology resiliency teams.

---

## 📌 What Is Application Drift — And Why Does It Matter?

**Application drift** occurs when software running in a production environment falls out of alignment with approved, current standards — typically because a component (an operating system, middleware, database engine, or third-party library) reaches end-of-life or falls behind a required patch level without being remediated.

In regulated industries like financial services, drift is not just a technical inconvenience. Unresolved drift means:

- **Security exposure** — outdated components are a primary attack surface for ransomware and data breaches
- **Compliance violations** — regulators expect firms to operate on supported, patched software stacks
- **Operational fragility** — unsupported components cannot receive critical patches, leaving systems vulnerable to outages
- **Audit risk** — technology resiliency controls require documented evidence that drift is actively monitored and remediated

The longer drift goes unaddressed, the more dangerous it becomes — especially for **high-criticality applications** with strict Recovery Time Objectives (RTOs) that must be restored within hours of an outage.

### ⚠️ When Drift Goes Unaddressed: A Real-World Example

On August 1, 2012, **Knight Capital Group** — one of the largest market makers on the U.S. stock exchange — deployed a software update that unintentionally reactivated a deprecated trading algorithm on 8 of its 32 servers. The remaining servers ran the new code. The version mismatch went undetected.

Within **45 minutes**, Knight Capital's systems executed millions of erroneous trades. By the time engineers identified and halted the issue, the firm had accumulated **$440 million in losses** — effectively wiping out the company. Knight Capital was acquired shortly after.

The root cause: **software version drift across a production server fleet**, left undetected until it was catastrophic.

This is the problem the AI Drift Analytics Agent is built to prevent.

---

## 🧠 What This Agent Does

The AI Drift Analytics Agent is a conversational AI system that connects directly to NorthStar Financial's application drift database. It allows technology resiliency analysts, operations leads, and executives to:

- Ask natural-language questions about drift risk across the application portfolio
- Instantly surface high-risk applications, escalation candidates, and concentration patterns
- Receive AI-generated narratives explaining drift trends and recommended actions
- Explore drill-downs by RTO tier, product category, data center, and days outstanding
- Trigger visual summaries of drift distribution and risk concentration

No SQL. No dashboards to navigate. Just ask.

---

## 🗂️ Sample Data

Below is a representative row from the NorthStar Financial drift dataset, illustrating the key variables the agent reasons over:

| APP_ID | App Name | Product | Detected Version | Approved Version | Days Drifting | RTO Tier | Data Center | Status |
|--------|----------|---------|-----------------|-----------------|--------------|----------|-------------|--------|
| NSF-0042 | Claims Processing Engine | Red Hat Linux | 7.9 | 8.6 | 94 | RTO 0-1 | Minneapolis-DC1 | Open |

**Key fields the agent uses:**
- **Days Drifting** — drives escalation logic at 60, 90, and 120-day thresholds
- **RTO Tier** — RTO 0–1 applications must be restored within 1 hour; drift here is highest priority
- **Detected vs. Approved Version** — the gap between these defines the drift
- **Status** — Open, In Remediation, or Closed

---

## 💬 Example Interactions

### Natural Language → Structured Insight

**You:** `Which RTO 0-1 applications have been drifting for more than 90 days?`

**Agent:** 
> 3 critical applications have exceeded the 90-day escalation threshold with RTO 0–1 designations. The longest outstanding is the Claims Processing Engine (NSF-0042) at 94 days on Red Hat Linux 7.9, followed by Payment Gateway Core (NSF-0107) at 91 days. These applications require immediate director-level escalation per resiliency policy. Recommend initiating formal remediation plans within 48 hours.

---

**You:** `Show me a breakdown of drift by product category`

**Agent:** *(returns chart + narrative)*
> The highest concentration of drift is in the Linux/OS category, accounting for 41% of all open drift instances. This pattern is consistent with a fleet-wide upgrade lag following a major Red Hat version transition. Recommend a bulk remediation initiative targeting this product group.

---

**You:** `What would happen if the top 5 drifting apps went down simultaneously?`

**Agent:**
> Of the top 5 drifting applications, 3 carry RTO 0–1 designations, meaning they must be restored within 1 hour of an outage. Simultaneous failure would trigger a Severity 1 incident. Combined, these applications support payment processing, claims adjudication, and core authentication services. Without current patch support, emergency restoration would be severely complicated — vendor assistance for end-of-life components is not guaranteed.

---

## 🏗️ Architecture

```
User (CLI)
    │
    ▼
LangGraph Agent Orchestrator
    │
    ├── Tool: SQL Query Engine (SQLite → northstar_drift.db)
    ├── Tool: Chart Generator (Matplotlib)
    ├── Tool: Escalation Detector (60/90/120-day threshold logic)
    └── Tool: Narrative Summarizer (OpenAI GPT-4o)
    │
    ▼
Pydantic Response Models → FastAPI (API layer)
    │
    ▼
Structured Output + Optional Visualization
```

**Agent flow:**
1. User submits a natural-language question via CLI
2. LangGraph orchestrates tool selection based on intent
3. Relevant tools query the SQLite database, run threshold logic, or generate visuals
4. OpenAI synthesizes a plain-English response with context-aware escalation guidance
5. Output is returned with optional chart and structured data

---

## 🗃️ Project Structure

```
ai-drift-analytics-agent/
│
├── agent/
│   ├── graph.py              # LangGraph agent definition & tool binding
│   ├── tools.py              # SQL, chart, escalation, and narrative tools
│   ├── prompts.py            # Domain-specific system prompt
│   └── models.py             # Pydantic response schemas
│
├── api/
│   └── main.py               # FastAPI app & endpoints
│
├── data/
│   ├── northstar_drift.db    # SQLite database
│   └── seed.py               # Synthetic dataset generator
│
├── visuals/                  # Generated charts saved here
├── cli.py                    # Interactive CLI entry point
├── requirements.txt
└── README.md
```

---

## 🚀 Getting Started

### Prerequisites
- Python 3.11+
- OpenAI API key

### Installation

```bash
git clone https://github.com/David-P23/ai-drift-analytics-agent.git
cd ai-drift-analytics-agent
pip install -r requirements.txt
```

### Configuration

```bash
cp .env.example .env
# Add your OpenAI API key to .env:
# OPENAI_API_KEY=sk-...
```

### Run the CLI Agent

```bash
python cli.py
```

### Run the API

```bash
uvicorn api.main:app --reload
```

---

## 💡 Suggested Questions to Try

| Category | Question |
|----------|----------|
| Escalation | `Which apps have been drifting for more than 90 days?` |
| Risk prioritization | `Show me all RTO 0-1 applications with open drift` |
| Concentration analysis | `What product categories have the most drift?` |
| Geographic | `Which data centers have the highest drift concentration?` |
| What-if | `What's the business impact if the top 3 drifting apps go down?` |
| Trend | `How many apps crossed the 60-day threshold this month?` |
| Bulk remediation | `Are there clusters of apps with the same drift issue I could address together?` |

---

## 🔧 Tech Stack

| Layer | Technology |
|-------|-----------|
| Agent Orchestration | LangGraph |
| LLM | OpenAI GPT-4o via LangChain |
| Tools & Logic | Python, custom LangChain tools |
| Data Validation | Pydantic v2 |
| Database | SQLite |
| API Layer | FastAPI |
| Visualization | Matplotlib |
| CLI | Python (rich) |

---

## 💼 Domain Context

This project is grounded in real enterprise experience. The drift monitoring workflows, escalation thresholds (60/90/120-day), RTO tier prioritization, and resiliency control frameworks modeled here reflect production-grade Technology Resiliency practices used at large financial institutions — adapted into a portfolio simulation context.

The agent is designed to mirror the kind of AI-augmented operations tooling that resiliency, risk, and technology teams at regulated firms are actively building toward.

---

## 📈 Roadmap

- [ ] Web UI (Streamlit or React frontend)
- [ ] Email escalation trigger for 120-day threshold breaches
- [ ] Multi-turn conversation memory across sessions
- [ ] Integration with mock CMDB for real-time app inventory
- [ ] Deployment to cloud (AWS / Azure)

---

*NorthStar Financial is a fictional company created for portfolio demonstration purposes. All data is synthetically generated and does not represent any real organization or individuals.*

---

**David Pearcill** · [LinkedIn](https://www.linkedin.com/in/david-pearcill/) · [GitHub](https://github.com/David-P23)
