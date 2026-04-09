import streamlit as st
from src.agent import ask

st.set_page_config(
    page_title="NorthStar AI Drift Analytics Agent",
    page_icon="🔍",
    layout="wide"
)

# ── Header ──────────────────────────────────────────────────────────────────
st.title("🔍 NorthStar Financial")
st.subheader("AI Drift Analytics Agent")
st.markdown(
    "*Asking natural-language questions about enterprise application drift risk — "
    "powered by LangGraph + GPT-4o-mini*"
)


# ── What Is Application Drift? ───────────────────────────────────────────────
with st.expander("📖 What is Application Drift? (Start here if you're new)", expanded=False):
    st.markdown("""
**Application drift** occurs when software running in a production environment falls out of alignment
with approved, current standards — typically because a component (an operating system, middleware,
database engine, or third-party library) reaches end-of-life or falls behind a required patch level
without being remediated.

In regulated industries like financial services, unresolved drift creates:

- 🔓 **Security exposure** — outdated components are a primary ransomware and breach attack surface
- 📋 **Compliance violations** — regulators require firms to operate on supported, patched stacks
- 💥 **Operational fragility** — unsupported components cannot receive critical patches
- 🔍 **Audit risk** — resiliency controls require documented evidence that drift is actively monitored

The longer drift goes unaddressed — especially on high-criticality applications — the more dangerous it becomes.
""")

    st.error("""
⚠️ **When drift goes unaddressed: Knight Capital Group, August 1, 2012**

Knight Capital deployed a software update that accidentally reactivated a deprecated trading algorithm
on 8 of its 32 servers. The remaining servers ran new code. The version mismatch went undetected.

Within **45 minutes**, erroneous trades had accumulated **$440 million in losses** — effectively
wiping out the firm. Knight Capital was acquired shortly after.

**Root cause: software version drift across a production server fleet, undetected until it was catastrophic.**
""")


# ── Sample Data ──────────────────────────────────────────────────────────────
with st.expander("🗂️ What data is the agent reasoning over?", expanded=False):
    st.markdown("Each application in the NorthStar Financial portfolio is tracked with the following fields:")

    import pandas as pd
    sample = pd.DataFrame([
        {
            "APP_ID": "NSF-0042",
            "App Name": "Claims Processing Engine",
            "Product": "Red Hat Linux",
            "Detected Version": "7.9",
            "Approved Version": "8.6",
            "Days Drifting": 94,
            "RTO Tier": "RTO 0–1",
            "Data Center": "Minneapolis-DC1",
            "Status": "Open"
        },
        {
            "APP_ID": "NSF-0107",
            "App Name": "Payment Gateway Core",
            "Product": "Oracle DB",
            "Detected Version": "12.1",
            "Approved Version": "19c",
            "Days Drifting": 61,
            "RTO Tier": "RTO 0–1",
            "Data Center": "Dallas-DC2",
            "Status": "Open"
        },
        {
            "APP_ID": "NSF-0233",
            "App Name": "Internal HR Portal",
            "Product": "Windows Server",
            "Detected Version": "2016",
            "Approved Version": "2022",
            "Days Drifting": 38,
            "RTO Tier": "RTO 4",
            "Data Center": "Minneapolis-DC1",
            "Status": "In Remediation"
        },
    ])
    st.dataframe(sample, use_container_width=True, hide_index=True)

    st.markdown("""
**Key fields the agent uses:**
- **Days Drifting** — drives escalation logic at 60, 90, and 120-day thresholds
- **RTO Tier** — RTO 0–1 applications must be restored within 1 hour; drift here is highest priority
- **Detected vs. Approved Version** — the gap between these defines the drift
- **Status** — Open, In Remediation, or Closed
""")


# ── Session State ────────────────────────────────────────────────────────────
if "active_question" not in st.session_state:
    st.session_state.active_question = ""
if "answer" not in st.session_state:
    st.session_state.answer = ""
if "sql" not in st.session_state:
    st.session_state.sql = ""
if "run_query" not in st.session_state:
    st.session_state.run_query = False


# ── Suggested Questions ──────────────────────────────────────────────────────
SUGGESTED_QUESTIONS = [
    "What percentage of in-scope applications are currently drifting?",
    "Which datacenter has the highest concentration of open drift?",
    "Which resiliency lead should we escalate to first?",
    "Show me critical applications with approved exemptions that are still open",
    "Which product category has the most open drift instances over 90 days?",
    "How many open drift instances have pending exemption requests?",
    "Which line of business has the most unresolved drift?",
    "Show me all applications drifting over 120 days with no exemption",
]

st.markdown("### 💡 Suggested Questions")
cols = st.columns(2)
for i, question in enumerate(SUGGESTED_QUESTIONS):
    col = cols[i % 2]
    if col.button(question, key=f"q{i}", use_container_width=True):
        st.session_state.active_question = question
        st.session_state.run_query = True

st.divider()

# ── Ask the Agent ────────────────────────────────────────────────────────────
st.markdown("### 🧠 Ask the Agent")
user_input = st.text_input(
    "Or type your own question:",
    placeholder="e.g. Which resiliency lead should we escalate to first?",
    key="user_input"
)

if st.button("Ask", type="primary"):
    if user_input.strip():
        st.session_state.active_question = user_input.strip()
        st.session_state.run_query = True

# ── Run Agent ────────────────────────────────────────────────────────────────
if st.session_state.run_query and st.session_state.active_question:
    st.session_state.run_query = False
    with st.spinner("Agent thinking..."):
        import io, sys
        old_stdout = sys.stdout
        sys.stdout = buffer = io.StringIO()
        answer = ask(st.session_state.active_question)
        sys.stdout = old_stdout
        output = buffer.getvalue()

    sql_line = ""
    for line in output.split("\n"):
        if line.startswith("SQL:"):
            sql_line = line.replace("SQL:", "").strip()

    st.session_state.answer = answer
    st.session_state.sql = sql_line

# ── Answer ───────────────────────────────────────────────────────────────────
if st.session_state.answer:
    st.divider()
    st.markdown("### 📊 Answer")
    st.success(st.session_state.answer)
    if st.session_state.sql:
        with st.expander("🔎 View SQL Query"):
            st.code(st.session_state.sql, language="sql")

# ── Footer ───────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "NorthStar Financial is a fictional company created for portfolio demonstration purposes. "
    "All data is synthetically generated and does not represent any real organization or individuals. | "
    "AI Analytics Agent v1.0 · Built by [David Pearcill](https://www.linkedin.com/in/david-pearcill/)"
)