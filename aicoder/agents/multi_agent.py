"""Multi-agent orchestration using LangGraph — Planner → [Coder|Reviewer|Tester]."""
from __future__ import annotations

import json
import re
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import create_react_agent
from rich.console import Console

console = Console()


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

def build_multi_agent_graph(llm, all_tools: list, read_only_tools: list, build_tool_list: list, interactive: bool = False):
    """Build a LangGraph StateGraph: plan → coder → reviewer → tester → synthesize."""

    coder_agent    = create_react_agent(llm, all_tools,       prompt=SystemMessage(CODER_PROMPT))
    reviewer_agent = create_react_agent(llm, read_only_tools, prompt=SystemMessage(REVIEWER_PROMPT))
    tester_agent   = create_react_agent(llm, build_tool_list, prompt=SystemMessage(TESTER_PROMPT))

    # ── Nodes ──────────────────────────────────────────────────────────────

    def plan_node(state: AgentState) -> dict:
        messages = [
            SystemMessage(content=PLANNER_PROMPT),
            HumanMessage(content=state["task"]),
        ]
        
        while True:
            response = llm.invoke(messages)
            raw = response.content.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            try:
                plan = json.loads(raw)
            except Exception:
                plan = {"coder": state["task"]}

            console.print(f"\\n[bold cyan]🗂️  Work plan:[/bold cyan] {[k for k, v in plan.items() if v]}")
            
            if not interactive:
                break
                
            console.print("\\n[bold yellow]✋ Interactive Plan Review[/bold yellow]")
            console.print("Type [bold green]'y'[/bold green] to approve, [bold red]'n'[/bold red] to abort, or [bold cyan]type feedback[/bold cyan] to refine the plan.")
            console.print("Prompt > ")
            
            import sys
            sys.stdout.flush()
            resp = sys.stdin.readline().strip()
            
            if resp.lower() in ('y', 'yes', ''):
                break
            elif resp.lower() in ('n', 'no', 'cancel', 'exit', 'quit'):
                console.print("[bold red]Plan rejected. Aborting.[/bold red]")
                raise KeyboardInterrupt("User aborted during interactive planning.")
            else:
                console.print(f"[dim]Refining plan based on feedback: {resp}[/dim]")
                messages.append(AIMessage(content=response.content))
                messages.append(HumanMessage(content=f"Feedback: {resp}\\nUpdate the JSON plan accordingly."))

        return {"messages": [HumanMessage(content=f"Plan: {plan}")], "_plan": plan}

    def _invoke_if(agent, instruction: str | None, state: AgentState, role: str) -> dict:
        if not instruction:
            return {}
            
        console.print(f"\\n[bold blue]▶️  {role.upper()} agent starting...[/bold blue]")
        
        messages = [HumanMessage(content=instruction)]
        final_output = ""
        
        from langchain_core.messages import AIMessage
        
        for update in agent.stream({"messages": messages}, stream_mode="updates"):
            for node_name, node_output in update.items():
                node_msgs = node_output.get("messages", [])
                for msg in node_msgs:
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        for tc in msg.tool_calls:
                            name = tc.get("name", "tool")
                            args = dict(tc.get("args", {}))
                            arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
                            if len(arg_str) > 60:
                                arg_str = arg_str[:57] + "..."
                            # Print in format that index.html matches ('Tool: ')
                            console.print(f"  → Tool: [bold cyan]{name}[/bold cyan]({arg_str})")
                    
                    if hasattr(msg, "content") and isinstance(msg.content, str) and msg.content.strip() and not getattr(msg, "tool_calls", None):
                        final_output = msg.content
        
        console.print(f"  [bold green]✓ {role.upper()} done.[/bold green]")
        return {f"{role}_result": final_output}

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
                    read_only_tools: list, build_tools: list, interactive: bool = False) -> str:
    app = build_multi_agent_graph(llm, all_tools, read_only_tools, build_tools, interactive)
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
