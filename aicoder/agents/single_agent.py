"""Single ReAct agent — the default mode for fix/refactor/audit/test."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder


SYSTEM_PROMPT = """\
You are AICoder, an autonomous coding agent. You help developers by reading,
analyzing, and modifying codebases based on their natural language requests.

## Your Tools
- **read_file**         – Read file contents with line numbers
- **list_files**        – List a directory
- **create_file**       – Create a new file
- **update_file**       – Replace code (exact match required)
- **delete_file**       – Delete a file
- **search_codebase**   – Semantic search across the project
- **get_build_system**  – Detect Maven/Gradle/npm/Cargo/Go/Python build system
- **compile_project**   – Quick compile check
- **run_tests**         – Run the test suite
- **execute_shell**     – Run any shell command
- **get_git_status**    – Show current branch and changes
- **checkout_new_branch** – Create a feature branch
- **commit_changes**    – Commit staged changes
- **get_diff**          – Show file or repo diff
- **get_diff_since**    – Diff since a branch/commit (e.g. 'main')

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


def build_agent(llm, tools: list) -> AgentExecutor:
    """Build a LangChain ReAct agent with the given LLM and tools."""
    prompt = ChatPromptTemplate.from_messages([
        ("system", SYSTEM_PROMPT),
        MessagesPlaceholder("chat_history", optional=True),
        ("human", "{input}"),
        MessagesPlaceholder("agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=False,
        max_iterations=20,
        handle_parsing_errors=True,
    )


def run_single_agent(
    instruction: str,
    llm,
    tools: list,
    memory_context: str = "",
) -> str:
    """Run a single blocking agent call. Returns the final response string."""
    executor = build_agent(llm, tools)
    prompt = instruction
    if memory_context:
        prompt = f"{instruction}\n\n{memory_context}"
    result = executor.invoke({"input": prompt})
    return result.get("output", "")


def stream_single_agent(
    instruction: str,
    llm,
    tools: list,
    memory_context: str = "",
) -> Iterator[str]:
    """Stream agent events. Yields text tokens as they arrive."""
    executor = build_agent(llm, tools)
    prompt = instruction
    if memory_context:
        prompt = f"{instruction}\n\n{memory_context}"

    for event in executor.stream({"input": prompt}):
        # AgentExecutor stream yields dicts; extract final output tokens
        if "output" in event:
            yield event["output"]
        elif "messages" in event:
            for msg in event["messages"]:
                if hasattr(msg, "content") and isinstance(msg.content, str):
                    yield msg.content
