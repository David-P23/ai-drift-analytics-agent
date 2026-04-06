from src.agent import ask

SUGGESTED_QUESTIONS = """
Suggested questions to get started:
  1. What percentage of in-scope applications are currently drifting?
  2. Which datacenter has the highest concentration of open drift?
  3. Which resiliency lead should we escalate to first?
  4. Show me critical applications with approved exemptions that are still open
  5. Which product category has the most open drift instances over 90 days?
  6. How many open drift instances have pending exemption requests?
  7. Which line of business has the most unresolved drift?
  8. Show me all applications drifting over 120 days with no exemption
"""

print("\n" + "=" * 60)
print("  NorthStar Financial — AI Drift Analytics Agent")
print("  Powered by LangGraph + GPT-4o-mini")
print("=" * 60)
print(SUGGESTED_QUESTIONS)
print("Type your question and press Enter. Type 'exit' to quit.\n")

while True:
    question = input("You: ").strip()
    if question.lower() in ["exit", "quit", "q"]:
        print("\nGoodbye.")
        break
    if not question:
        continue
    print("\nAgent thinking...\n")
    ask(question)
    print("\n" + "─" * 60 + "\n")