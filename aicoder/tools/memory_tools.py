"""
MemoryTools — LangChain tools that let the agent read and write project memory.

The agent can call these tools during a session to persist things it discovers:
  save_memory("convention:tests", "Use pytest with conftest.py fixtures")
  save_memory("file:src/auth.py", "Main auth module — uses JWT with refresh tokens")
  save_memory("project:summary", "FastAPI e-commerce backend with PostgreSQL")
  recall_memory()  → returns full memory context
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from langchain_core.tools import tool

from aicoder.memory import AgentMemory


def make_memory_tools(memory: AgentMemory) -> list:
    """Return LangChain tools bound to the given AgentMemory instance."""

    @tool
    def save_memory(
        key: Annotated[str, "Memory key. Prefixes: 'convention:', 'file:', 'project:'"],
        value: Annotated[str, "Value to remember (1-2 sentences max)"],
    ) -> str:
        """
        Persist something important about this project for future sessions.

        Use this when you discover:
        - A coding convention: save_memory("convention:tests", "Use pytest fixtures")
        - A key file:          save_memory("file:src/auth.py", "JWT auth logic")
        - Project summary:     save_memory("project:summary", "FastAPI backend")
        - Anything useful:     save_memory("pattern:error-handling", "Always use Result<T>")
        """
        memory.save_entry(key, value)
        memory.save()
        return f"✅ Remembered: {key} = {value[:60]}"

    @tool
    def recall_memory() -> str:
        """
        Recall everything remembered about this project.
        Call this at the start of a session to get context from previous runs.
        """
        ctx = memory.to_prompt_context()
        return ctx if ctx else "No project memory yet."

    @tool
    def list_key_files() -> str:
        """
        List files previously flagged as important in this project.
        Returns file paths with their descriptions.
        """
        kf = memory.key_files
        if not kf:
            return "No key files recorded yet."
        lines = [f"  {path}: {desc}" for path, desc in kf.items()]
        return "Key files:\n" + "\n".join(lines)

    return [save_memory, recall_memory, list_key_files]
