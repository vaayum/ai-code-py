"""Multi-agent orchestration using LangGraph — Planner → [Coder|Reviewer|Tester]."""
from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages


# ── State ────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    """Shared state flowing through the LangGraph nodes."""
    task: str
    messages: Annotated[list, add_messages]
    coder_result: str
    reviewer_result: str
    tester_result: str
    final_summary: str


# ── Node prompts ─────────────────────────────────────────────────────────────

PLANNER_PROMPT = """\
You are the Planner in a multi-agent coding system. Break the user's task into
focused sub-tasks for specialized agents.

Respond ONLY with a JSON object like:
{
  "coder":    "<instruction for the Coder agent, or null>",
  "reviewer": "<instruction for the Reviewer agent, or null>",
  "tester":   "<instruction for the Tester agent, or null>"
}
"""

CODER_PROMPT = """\
You are the Coder agent. IMPLEMENT the given task.
- Always read files before modifying them
- Create a feature branch before making changes
- Use compile_project to verify changes
- Commit when done
"""

REVIEWER_PROMPT = """\
You are the Reviewer agent. ANALYZE code — do NOT modify anything.
- Be specific: quote file + line number for each finding
- Categorize: [CRITICAL] [WARNING] [SUGGESTION] [POSITIVE]
- Report security, quality, perf, and architectural issues
"""

TESTER_PROMPT = """\
You are the Tester agent. COMPILE, RUN TESTS, and DIAGNOSE failures.
- Start with compile_project, then run_tests
- Report structured results: Build ✅/❌, Tests X/Y passed
- Suggest fixes but do not implement them
"""

SYNTHESIZER_PROMPT = """\
You are the Planner synthesizing results from specialist agents.
Combine their outputs into a concise, actionable summary for the developer.
Group by agent, highlight key outcomes, note any failures or follow-ups.
"""


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_multi_agent_graph(llm, all_tools: list, read_only_tools: list, build_tools: list):
    """
    Build a LangGraph StateGraph with 5 nodes:
      plan → [coder, reviewer, tester] (parallel) → synthesize
    """
    from langchain.agents import AgentExecutor, create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

    def _make_executor(system_prompt: str, tools: list) -> AgentExecutor:
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        agent = create_tool_calling_agent(llm, tools, prompt)
        return AgentExecutor(agent=agent, tools=tools, verbose=False,
                             max_iterations=15, handle_parsing_errors=True)

    coder_executor   = _make_executor(CODER_PROMPT,    all_tools)
    reviewer_executor = _make_executor(REVIEWER_PROMPT, read_only_tools)
    tester_executor  = _make_executor(TESTER_PROMPT,   build_tools)

    # ── Nodes ──────────────────────────────────────────────────────────────

    def plan_node(state: AgentState) -> AgentState:
        """Planner decomposes the task into sub-agent instructions."""
        import json, re
        response = llm.invoke([
            SystemMessage(content=PLANNER_PROMPT),
            HumanMessage(content=state["task"]),
        ])
        raw = response.content.strip()
        # Strip markdown fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            plan = json.loads(raw)
        except Exception:
            plan = {"coder": state["task"]}  # fallback: treat as coder task

        print(f"\n🗂️  Work plan: {list(k for k, v in plan.items() if v)}")
        return {**state, "messages": [HumanMessage(content=f"Plan: {plan}")],
                "_plan": plan}

    def _run_if(executor, instruction, state: AgentState, key: str) -> AgentState:
        if instruction:
            print(f"  → {key.upper()} agent starting...")
            result = executor.invoke({"input": instruction})
            output = result.get("output", "")
            print(f"  ✓ {key.upper()} done.")
            return {**state, f"{key}_result": output}
        return state

    def coder_node(state: AgentState) -> AgentState:
        plan = state.get("_plan", {})
        return _run_if(coder_executor, plan.get("coder"), state, "coder")

    def reviewer_node(state: AgentState) -> AgentState:
        plan = state.get("_plan", {})
        return _run_if(reviewer_executor, plan.get("reviewer"), state, "reviewer")

    def tester_node(state: AgentState) -> AgentState:
        plan = state.get("_plan", {})
        return _run_if(tester_executor, plan.get("tester"), state, "tester")

    def synthesize_node(state: AgentState) -> AgentState:
        """Planner synthesizes all sub-agent results into a final summary."""
        results_text = "\n\n".join([
            f"## Coder\n{state.get('coder_result', '(not run)')}" if state.get('coder_result') else "",
            f"## Reviewer\n{state.get('reviewer_result', '(not run)')}" if state.get('reviewer_result') else "",
            f"## Tester\n{state.get('tester_result', '(not run)')}" if state.get('tester_result') else "",
        ]).strip()

        response = llm.invoke([
            SystemMessage(content=SYNTHESIZER_PROMPT),
            HumanMessage(content=f"Task: {state['task']}\n\n{results_text}"),
        ])
        return {**state, "final_summary": response.content}

    # ── Graph wiring ───────────────────────────────────────────────────────

    graph = StateGraph(AgentState)
    graph.add_node("plan",      plan_node)
    graph.add_node("coder",     coder_node)
    graph.add_node("reviewer",  reviewer_node)
    graph.add_node("tester",    tester_node)
    graph.add_node("synthesize", synthesize_node)

    graph.set_entry_point("plan")

    # After planning, run all agents in sequence
    # (LangGraph parallel fan-out requires async; sequential is fine for v1)
    graph.add_edge("plan",      "coder")
    graph.add_edge("coder",     "reviewer")
    graph.add_edge("reviewer",  "tester")
    graph.add_edge("tester",    "synthesize")
    graph.add_edge("synthesize", END)

    return graph.compile()


def run_multi_agent(task: str, llm, all_tools: list,
                    read_only_tools: list, build_tools: list) -> str:
    """Run the full multi-agent graph and return the synthesized summary."""
    app = build_multi_agent_graph(llm, all_tools, read_only_tools, build_tools)
    final_state = app.invoke({
        "task": task,
        "messages": [],
        "coder_result": "",
        "reviewer_result": "",
        "tester_result": "",
        "final_summary": "",
    })
    return final_state.get("final_summary", "No summary produced.")
