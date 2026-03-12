"""Single ReAct agent — LangGraph 0.3+ streaming with tool-call events."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.prebuilt import create_react_agent


SYSTEM_PROMPT = """\
You are AICoder, an autonomous coding agent. You help developers by reading,
analyzing, and modifying codebases based on their natural language requests.

## Your Tools

### Code tools
- **read_file**            – Read file contents with line numbers
- **list_files**           – List a directory
- **create_file**          – Create a new file
- **patch_file**           – Replace specific lines in a file using 1-indexed start/end line numbers
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

### Memory tools (persist knowledge across sessions)
- **recall_memory**        – Read everything remembered about this project (call first!)
- **save_memory**          – Save conventions, key files, or summaries for future sessions
- **list_key_files**       – List files previously flagged as important

## Memory Guidelines

**Always call `recall_memory()` at the start of a session** to load previously saved context.

**Call `save_memory()` when you discover:**
- A coding convention: save_memory("convention:tests", "Use pytest with conftest.py fixtures")  
- A key file role:     save_memory("file:src/auth.py", "JWT auth — core security module")
- Project summary:    save_memory("project:summary", "FastAPI e-commerce backend + PostgreSQL")
- A useful pattern:   save_memory("pattern:errors", "All errors use the AppError base class")

## How to Decide What to Do

**UNDERSTAND requests** (explain, review, analyze, look at, describe):
→ Call recall_memory() first → ONLY read the mentioned files. Report findings. Do NOT modify anything.

**CHANGE requests** (fix, create, add, refactor, implement, update, delete):
→ Call recall_memory() → checkout_new_branch → read files → make changes → compile → commit.
→ After finishing, save any conventions or patterns you noticed with save_memory().

## Rules
1. Always **read** a file before modifying it.
2. For CHANGE requests: `checkout_new_branch` → edit → `compile_project` → `commit_changes`.
3. After changes, run `compile_project`. If it fails, read errors, fix, recompile (max 3 retries).
4. Keep responses concise and action-focused.
5. Only read files the user mentioned. Ask before exploring others.
"""


@dataclass
class ToolCallEvent:
    """Emitted when the agent calls a tool."""
    tool_name: str
    tool_input: dict
    tool_result: str = ""


@dataclass
class TokenEvent:
    """Emitted as the agent streams its final text response."""
    text: str
    is_final: bool = False


@dataclass
class ThinkingEvent:
    """Emitted when agent is reasoning (no visible text yet)."""
    pass


AgentEvent = ToolCallEvent | TokenEvent | ThinkingEvent


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
    chat_history: list = None,
) -> str:
    """Run a single blocking agent call. Returns the final response string."""
    agent = build_agent(llm, tools)
    prompt = instruction
    if memory_context:
        prompt = f"{instruction}\n\n{memory_context}"

    messages = list(chat_history) if chat_history else []
    messages.append(HumanMessage(content=prompt))

    result = agent.invoke({"messages": messages})
    messages = result.get("messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "content") and isinstance(msg.content, str) and msg.content.strip():
            return msg.content
    return ""


def stream_agent_events(
    instruction: str,
    llm,
    tools: list,
    memory_context: str = "",
    chat_history: list = None,
) -> Iterator[AgentEvent]:
    """
    Stream rich agent events in real time using LangGraph stream_mode='updates'.

    Yields:
        ThinkingEvent  — agent is processing (no output yet)
        ToolCallEvent  — a tool was invoked (name + input + result)
        TokenEvent     — final text chunks as they stream
    """
    agent = build_agent(llm, tools)
    prompt = instruction
    if memory_context:
        prompt = f"{instruction}\n\n{memory_context}"

    # Track pending tool calls (name+input arrive before result)
    pending_tool_calls: dict[str, dict] = {}  # call_id -> {name, input}

    yield ThinkingEvent()

    messages = list(chat_history) if chat_history else []
    messages.append(HumanMessage(content=prompt))

    for update in agent.stream(
        {"messages": messages},
        stream_mode="updates",
    ):
        for node_name, node_output in update.items():
            messages = node_output.get("messages", [])

            for msg in messages:
                # ── AI message: reasoning or tool call request ────────────
                if isinstance(msg, AIMessage):
                    # Surface any tool_calls embedded in this message
                    for tc in getattr(msg, "tool_calls", []) or []:
                        call_id = tc.get("id", "")
                        pending_tool_calls[call_id] = {
                            "name": tc.get("name", "?"),
                            "input": tc.get("args", {}),
                        }
                        yield ToolCallEvent(
                            tool_name=tc.get("name", "?"),
                            tool_input=tc.get("args", {}),
                        )

                    # If it has plain text and no tool_calls → it's the final answer
                    if (
                        isinstance(msg.content, str)
                        and msg.content.strip()
                        and not getattr(msg, "tool_calls", None)
                    ):
                        yield TokenEvent(text=msg.content, is_final=True)

                # ── ToolMessage: result of a tool call ────────────────────
                elif isinstance(msg, ToolMessage):
                    call_id = msg.tool_call_id
                    pending = pending_tool_calls.pop(call_id, {})
                    yield ToolCallEvent(
                        tool_name=pending.get("name", msg.name or "?"),
                        tool_input=pending.get("input", {}),
                        tool_result=str(msg.content)[:500],  # truncate long results
                    )


def stream_single_agent(
    instruction: str,
    llm,
    tools: list,
    memory_context: str = "",
    chat_history: list = None,
) -> Iterator[str]:
    """Backward-compat: stream only the final text tokens."""
    for event in stream_agent_events(instruction, llm, tools, memory_context, chat_history):
        if isinstance(event, TokenEvent) and event.is_final:
            yield event.text
