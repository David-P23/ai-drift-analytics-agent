import streamlit as st
from src.agent import ask

st.set_page_config(
    page_title="NorthStar AI Drift Analytics Agent",
    page_icon="🔍",
    layout="wide"
)

st.title("🔍 NorthStar Financial")
st.subheader("AI Drift Analytics Agent")
st.markdown("*Powered by LangGraph + GPT-4o-mini*")
st.divider()

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

if "active_question" not in st.session_state:
    st.session_state.active_question = ""
if "answer" not in st.session_state:
    st.session_state.answer = ""
if "sql" not in st.session_state:
    st.session_state.sql = ""
if "run_query" not in st.session_state:
    st.session_state.run_query = False

st.markdown("### 💡 Suggested Questions")
cols = st.columns(2)
for i, question in enumerate(SUGGESTED_QUESTIONS):
    col = cols[i % 2]
    if col.button(question, key=f"q{i}", use_container_width=True):
        st.session_state.active_question = question
        st.session_state.run_query = True

st.divider()
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

if st.session_state.answer:
    st.divider()
    st.markdown("### 📊 Answer")
    st.success(st.session_state.answer)
    if st.session_state.sql:
        with st.expander("🔎 View SQL Query"):
            st.code(st.session_state.sql, language="sql")

st.divider()
st.caption("NorthStar Financial — Technology Resiliency Program | AI Analytics Agent v1.0")