"""Multi-agent orchestration using LangGraph — Planner → [Coder|Reviewer|Tester]."""
from __future__ import annotations

import json
import re
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent


# ── State ────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    task: str
    messages: Annotated[list, add_messages]
    coder_result: str
    reviewer_result: str
    tester_result: str
    final_summary: str
    _plan: dict


# ── Prompts ───────────────────────────────────────────────────────────────────

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
- Use compile check tools to verify changes
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
- Start with compile check, then run tests
- Report structured results: Build ✅/❌, Tests X/Y passed
- Suggest fixes but do not implement them
"""

SYNTHESIZER_PROMPT = """\
You are the Planner synthesizing results from specialist agents.
Combine their outputs into a concise, actionable summary for the developer.
Group by agent, highlight key outcomes, note any failures or follow-ups.
"""


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_multi_agent_graph(llm, all_tools: list, read_only_tools: list, build_tool_list: list):
    """Build a LangGraph StateGraph: plan → coder → reviewer → tester → synthesize."""

    coder_agent    = create_react_agent(llm, all_tools,       prompt=SystemMessage(CODER_PROMPT))
    reviewer_agent = create_react_agent(llm, read_only_tools, prompt=SystemMessage(REVIEWER_PROMPT))
    tester_agent   = create_react_agent(llm, build_tool_list, prompt=SystemMessage(TESTER_PROMPT))

    # ── Nodes ──────────────────────────────────────────────────────────────

    def plan_node(state: AgentState) -> dict:
        response = llm.invoke([
            SystemMessage(content=PLANNER_PROMPT),
            HumanMessage(content=state["task"]),
        ])
        raw = response.content.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            plan = json.loads(raw)
        except Exception:
            plan = {"coder": state["task"]}

        print(f"\n🗂️  Work plan: {[k for k, v in plan.items() if v]}")
        return {"messages": [HumanMessage(content=f"Plan: {plan}")], "_plan": plan}

    def _invoke_if(agent, instruction: str | None, state: AgentState, role: str) -> dict:
        if not instruction:
            return {}
        print(f"  → {role.upper()} agent starting...")
        result = agent.invoke({"messages": [HumanMessage(content=instruction)]})
        msgs = result.get("messages", [])
        output = ""
        for m in reversed(msgs):
            if hasattr(m, "content") and isinstance(m.content, str) and m.content.strip():
                output = m.content
                break
        print(f"  ✓ {role.upper()} done.")
        return {f"{role}_result": output}

    def coder_node(state: AgentState) -> dict:
        return _invoke_if(coder_agent,    state.get("_plan", {}).get("coder"),    state, "coder")

    def reviewer_node(state: AgentState) -> dict:
        return _invoke_if(reviewer_agent, state.get("_plan", {}).get("reviewer"), state, "reviewer")

    def tester_node(state: AgentState) -> dict:
        return _invoke_if(tester_agent,   state.get("_plan", {}).get("tester"),   state, "tester")

    def synthesize_node(state: AgentState) -> dict:
        parts = []
        if state.get("coder_result"):
            parts.append(f"## Coder\n{state['coder_result']}")
        if state.get("reviewer_result"):
            parts.append(f"## Reviewer\n{state['reviewer_result']}")
        if state.get("tester_result"):
            parts.append(f"## Tester\n{state['tester_result']}")

        response = llm.invoke([
            SystemMessage(content=SYNTHESIZER_PROMPT),
            HumanMessage(content=f"Task: {state['task']}\n\n" + "\n\n".join(parts)),
        ])
        return {"final_summary": response.content}

    # ── Graph wiring ───────────────────────────────────────────────────────

    graph = StateGraph(AgentState)
    graph.add_node("plan",       plan_node)
    graph.add_node("coder",      coder_node)
    graph.add_node("reviewer",   reviewer_node)
    graph.add_node("tester",     tester_node)
    graph.add_node("synthesize", synthesize_node)

    graph.set_entry_point("plan")
    graph.add_edge("plan",       "coder")
    graph.add_edge("coder",      "reviewer")
    graph.add_edge("reviewer",   "tester")
    graph.add_edge("tester",     "synthesize")
    graph.add_edge("synthesize", END)

    return graph.compile()


def run_multi_agent(task: str, llm, all_tools: list,
                    read_only_tools: list, build_tools: list) -> str:
    app = build_multi_agent_graph(llm, all_tools, read_only_tools, build_tools)
    final = app.invoke({
        "task": task,
        "messages": [],
        "coder_result": "",
        "reviewer_result": "",
        "tester_result": "",
        "final_summary": "",
        "_plan": {},
    })
    return final.get("final_summary", "No summary produced.")
