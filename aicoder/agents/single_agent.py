"""Single ReAct agent — uses langgraph.prebuilt.create_react_agent (LangChain 0.3+)."""
from __future__ import annotations

from typing import Iterator

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent


SYSTEM_PROMPT = """\
You are AICoder, an autonomous coding agent. You help developers by reading,
analyzing, and modifying codebases based on their natural language requests.

## Your Tools
- **read_file**            – Read file contents with line numbers
- **list_files**           – List a directory
- **create_file**          – Create a new file
- **update_file**          – Replace code (exact match required)
- **delete_file**          – Delete a file
- **search_codebase**      – Semantic search across the project
- **get_build_system**     – Detect Maven/Gradle/npm/Cargo/Go/Python build system
- **compile_project**      – Quick compile check
- **run_tests**            – Run the test suite
- **execute_shell**        – Run any shell command
- **get_git_status**       – Show current branch and changes
- **checkout_new_branch**  – Create a feature branch
- **commit_changes**       – Commit staged changes
- **get_diff**             – Show file or repo diff
- **get_diff_since**       – Diff since a branch/commit (e.g. 'main')

## How to Decide What to Do

**UNDERSTAND requests** (explain, review, analyze, look at, describe):
→ ONLY read the mentioned files. Report findings. Do NOT modify anything.

**CHANGE requests** (fix, create, add, refactor, implement, update, delete):
→ Create a branch first → read files → make changes → compile → commit.

## Rules
1. Always **read** a file before modifying it.
2. For CHANGE requests: `checkout_new_branch` → edit → `compile_project` → `commit_changes`.
3. After changes, run `compile_project`. If it fails, read errors, fix, recompile (max 3 retries).
4. Keep responses concise and action-focused.
5. Only read files the user mentioned. Ask before exploring others.
"""


def build_agent(llm, tools: list):
    """Build a LangGraph ReAct agent — LangChain 0.3+ compatible."""
    return create_react_agent(
        model=llm,
        tools=tools,
        prompt=SystemMessage(content=SYSTEM_PROMPT),
    )


def run_single_agent(
    instruction: str,
    llm,
    tools: list,
    memory_context: str = "",
) -> str:
    """Run a single blocking agent call. Returns the final response string."""
    agent = build_agent(llm, tools)
    prompt = instruction
    if memory_context:
        prompt = f"{instruction}\n\n{memory_context}"

    result = agent.invoke({"messages": [HumanMessage(content=prompt)]})
    # LangGraph returns {"messages": [...]} — last message is the AI response
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "content") and isinstance(msg.content, str) and msg.content.strip():
            return msg.content
    return ""


def stream_single_agent(
    instruction: str,
    llm,
    tools: list,
    memory_context: str = "",
) -> Iterator[str]:
    """Stream agent tokens. Yields final text chunks as they arrive."""
    agent = build_agent(llm, tools)
    prompt = instruction
    if memory_context:
        prompt = f"{instruction}\n\n{memory_context}"

    for event in agent.stream(
        {"messages": [HumanMessage(content=prompt)]},
        stream_mode="values",
    ):
        messages = event.get("messages", [])
        if messages:
            last = messages[-1]
            if hasattr(last, "content") and isinstance(last.content, str):
                yield last.content
