# Dataset Design — NorthStar Financial Synthetic Enterprise Portfolio

This document explains the intentional design decisions behind the synthetic dataset powering the AI Drift Analytics Agent. The dataset was modeled after real enterprise Technology Resiliency programs used in large financial institutions.

---

## Why a Custom Dataset?

Most AI portfolio projects use off-the-shelf datasets (Kaggle, UCI, etc.). This project required a domain-specific dataset that didn't exist publicly — one that could realistically simulate the complexity of enterprise application drift management across a large organization.

The dataset was designed from scratch to support meaningful agentic reasoning, not just simple lookups.

---

## Schema Design

Three normalized tables with a pre-joined analytics view:

### applications (1,000 rows)
The full enterprise application portfolio — the denominator for all drift metrics.

Key design decisions:
- 78% of apps marked `in_scope = Y` — only these are subject to drift compliance monitoring
- RTO scores 1–10 map to recovery time windows (0.5hrs → 120hrs), enabling true criticality-based prioritization
- 10 realistic datacenters across US financial hub cities (Chandler, Charlotte, Eagan, Dallas, Ashburn, etc.)
- Secondary datacenter intentionally null for 28% of apps — creates geographic concentration risk stories
- App names use realistic prefix/suffix combinations (e.g. "Granite Gateway", "Pinnacle Recovery")

### drift_instances (~475 rows)
One row per drift event. An application can have multiple concurrent drift instances.

Key design decisions:
- ~28% drift penetration among in-scope apps — realistic for a mature resiliency program
- Multiple drift instances per app (1: 58%, 2: 24%, 3: 11%, 4+: 7%)
- Year-aware drift IDs (`DR-2025-000001`) support historical analysis and trend reporting
- Instance names encode LOB + product + environment (`PAYORAPRD01`, `RSKMQPRD02`) for realistic enterprise feel
- Realistic software version strings by product type (Oracle 19.18.0, RHEL 8.8, WebLogic 12.2.1.4)
- Aging distribution weighted toward shorter durations with a realistic long tail
- Exemption logic: 28% requested, with Approved/Pending/Denied breakdown among those

### drift_notes (~800 rows)
Analyst documentation tied to drift instances — escalations, exemption requests, remediation updates.

Key design decisions:
- Note volume weighted by drift status and exemption state
- 7 note types with realistic probability distributions per context
- Note text uses realistic enterprise language (CAB approvals, regression risk, vendor certification, quarter-end blackouts)
- Escalation flag enables agent to surface high-priority items quickly

### drift_full (view)
Pre-joined view of drift_instances + applications — the primary query target for the agent. Eliminates ambiguous column references and simplifies SQL generation.

---

## Realistic Distributions

| Attribute | Distribution |
|-----------|-------------|
| Apps in scope | 78% Y / 22% N |
| Drift penetration | ~28% of in-scope apps |
| Drift status | 38% Open / 62% Closed |
| Exemptions requested | 28% of drift instances |
| Exemption outcomes | 44% Approved / 34% Pending / 22% Denied |
| Production drift | 63% of instances in Production environment |
| RTO score | Bell curve weighted toward mid-range (scores 5-7) |

---

## Product Catalog

7 product categories, 28 products, with vendor-accurate version strings:

| Category | Products |
|----------|---------|
| Operating System | RHEL, Windows Server, Ubuntu Server, SUSE Linux |
| Database | Oracle Database, SQL Server, PostgreSQL, MongoDB Enterprise |
| Middleware | WebLogic, WebSphere, Tomcat, JBoss EAP |
| Web Server / API | Apache HTTP Server, NGINX, Apigee Gateway, IIS |
| Security / Identity | Okta Agent, CyberArk, PingFederate, SailPoint IQService |
| Messaging / Integration | IBM MQ, Kafka, MuleSoft Runtime, TIBCO EMS |
| Monitoring / Agent | Splunk UF, Dynatrace OneAgent, AppDynamics Agent, Tanium Client |

---

## RTO Score Mapping

| Score | Max Recovery Time | Criticality |
|-------|------------------|-------------|
| 1 | 0.5 hours | Mission Critical |
| 2 | 1 hour | Mission Critical |
| 3 | 2 hours | High |
| 4 | 4 hours | High |
| 5 | 8 hours | Medium |
| 6 | 12 hours | Medium |
| 7 | 24 hours | Low |
| 8 | 48 hours | Low |
| 9 | 72 hours | Very Low |
| 10 | 120 hours | Very Low |

---

## Generation Stack

- **Python + Pandas** — dataframe construction and export
- **Faker** — realistic names, emails, dates
- **NumPy** — weighted random distributions
- **SQLite** — relational database with view layer
- **Jupyter Notebook** — iterative generation and validation

Full generation pipeline: `notebooks/generate_drift_dataset.ipynb`

---

*All company names, application names, employee names, and data are entirely fictional.*