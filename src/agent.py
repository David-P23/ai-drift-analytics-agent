import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated
import operator

from src.tools import run_query, get_schema

load_dotenv(override=True)

# --------------------------------
# State Definition
# --------------------------------

class AgentState(TypedDict):
    question: str
    sql: str
    query_result: str
    answer: str
    messages: Annotated[list, operator.add]

# --------------------------------
# LLM
# --------------------------------

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    api_key=os.getenv("OPENAI_API_KEY")
)

# --------------------------------
# Node 1: Generate SQL
# --------------------------------

def generate_sql(state: AgentState) -> AgentState:
    prompt = f"""You are an expert SQL analyst for NorthStar Financial's Technology Resiliency program.

CONTEXT:
- "Drift" means an application is running a software version that does not match the approved standard version. This is a compliance and resiliency risk.
- Applications are scored by RTO (Recovery Time Objective). RTO score 1-2 = Mission Critical, 3-4 = High, 5-6 = Medium, 7-10 = Low.
- "In scope" means the application is subject to drift monitoring compliance requirements.
- An exemption means the application team has requested permission to remain on the non-approved version temporarily, usually due to compatibility issues.
- drift_duration_days = how many days the drift has been open.
- Aging thresholds: 60 days = escalation warning, 90 days = senior escalation, 120 days = executive escalation.

DATABASE SCHEMA:
{get_schema()}

RULES:
- Return ONLY the SQL query, no explanation, no markdown, no backticks
- Use proper SQLite syntax
- For current date use: date('now')
- status values are: 'Open' or 'Closed'
- exemption_requested values are: 'Y' or 'N'
- exemption_result values are: 'Approved', 'Pending', 'Denied', or NULL
- rto_score ranges from 1 (most critical) to 10 (least critical)
- To find critical applications use rto_score <= 3, not app_status = 'Critical'
- in_scope values are: 'Y' or 'N'
- To calculate drift percentage: COUNT(DISTINCT drifting apps) / COUNT(DISTINCT total in-scope apps) * 100
- CRITICAL: When joining tables, ALWAYS use table alias prefixes on every column in SELECT, WHERE, GROUP BY, and ORDER BY. Never write a bare column name without its alias. Example: write "a.resiliency_lead" never just "resiliency_lead"

Generate a single valid SQLite SQL query to answer this question:
{state['question']}
"""
    response = llm.invoke([HumanMessage(content=prompt)])
    return {**state, "sql": response.content.strip()}

# --------------------------------
# Node 2: Run Query
# --------------------------------

def execute_query(state: AgentState) -> AgentState:
    result = run_query(state["sql"])
    return {**state, "query_result": result}

# --------------------------------
# Node 3: Generate Answer
# --------------------------------

def generate_answer(state: AgentState) -> AgentState:
    prompt = f"""You are the AI Analytics Agent for NorthStar Financial's Technology Resiliency program.

YOUR ROLE:
You monitor software version drift across 1,000 enterprise applications. Drift occurs when an application runs a version that doesn't meet the approved standard — creating compliance, security, and resiliency risk. You help leadership understand where risk is concentrated and what action to take.

KEY DEFINITIONS TO USE NATURALLY IN ANSWERS:
- Drift = a software component running below the approved version standard
- RTO Score 1-2 = Mission Critical, 3-4 = High Criticality, 5-6 = Medium, 7-10 = Lower priority
- 60/90/120 day thresholds = escalation milestones used by the resiliency team
- Exemptions = approved temporary exceptions, usually due to vendor compatibility issues
- In-scope = applications subject to compliance monitoring

COMMUNICATION STYLE:
- Speak like a senior analyst briefing a director — confident, concise, insight-driven
- Lead with the key finding and number
- Add one sentence of risk context where relevant
- End with one concrete recommended action if appropriate
- Never use email format, salutations, sign-offs, or placeholder brackets like [Director's Name]
- Never exceed 150 words
- If results are empty, explain what that means in plain terms — don't just say "no results found"

The user asked: {state['question']}
The SQL executed: {state['sql']}
The data returned: {state['query_result']}
"""
    response = llm.invoke([HumanMessage(content=prompt)])
    return {**state, "answer": response.content.strip()}

# --------------------------------
# Build Graph
# --------------------------------

def build_agent():
    graph = StateGraph(AgentState)

    graph.add_node("generate_sql", generate_sql)
    graph.add_node("execute_query", execute_query)
    graph.add_node("generate_answer", generate_answer)

    graph.set_entry_point("generate_sql")
    graph.add_edge("generate_sql", "execute_query")
    graph.add_edge("execute_query", "generate_answer")
    graph.add_edge("generate_answer", END)

    return graph.compile()

agent = build_agent()

# --------------------------------
# Run Function
# --------------------------------

def ask(question: str) -> str:
    result = agent.invoke({
        "question": question,
        "sql": "",
        "query_result": "",
        "answer": "",
        "messages": []
    })
    print(f"\nSQL: {result['sql']}")
    print(f"\nAnswer: {result['answer']}")
    return result["answer"]