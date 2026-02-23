"""
Live streaming renderer for AICoder's interactive shell.

Renders agent events as they arrive:
  - ThinkingEvent    → animated spinner
  - ToolCallEvent    → colored tool call line with file name
  - TokenEvent       → streamed markdown response

Also provides colored unified diff display used by the interactive
approval flow in FileTools.
"""
from __future__ import annotations

import difflib
import re
import time
from pathlib import Path
from typing import Iterator

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from aicoder.agents.single_agent import (
    AgentEvent,
    ThinkingEvent,
    TokenEvent,
    ToolCallEvent,
)

console = Console()

# ── Tool call icon mapping ─────────────────────────────────────────────────────

_TOOL_ICONS: dict[str, tuple[str, str]] = {
    "read_file":          ("🔍", "cyan"),
    "list_files":         ("📂", "cyan"),
    "create_file":        ("✨", "green"),
    "update_file":        ("✏️ ", "yellow"),
    "delete_file":        ("🗑️ ", "red"),
    "search_codebase":    ("🔎", "blue"),
    "get_build_system":   ("🔧", "magenta"),
    "compile_project":    ("🏗️ ", "magenta"),
    "run_tests":          ("🧪", "magenta"),
    "execute_shell":      ("💻", "white"),
    "get_git_status":     ("🌿", "green"),
    "checkout_new_branch":("🌿", "green"),
    "commit_changes":     ("💾", "green"),
    "get_diff":           ("📝", "yellow"),
    "get_diff_since":     ("📝", "yellow"),
    # AST tools
    "add_function":       ("➕", "green"),
    "replace_function_body":("♻️ ", "yellow"),
    "add_import":         ("📦", "cyan"),
    "add_decorator":      ("🎀", "cyan"),
    "rename_symbol":      ("🏷️ ", "yellow"),
}


def _tool_label(name: str, tool_input: dict) -> str:
    """Human-readable label for a tool call."""
    # Extract the most meaningful argument (path / query / command)
    arg = (
        tool_input.get("path")
        or tool_input.get("directory")
        or tool_input.get("query")
        or tool_input.get("command")
        or tool_input.get("branch_name")
        or tool_input.get("message")
        or ""
    )
    label = name.replace("_", " ")
    if arg:
        label = f"{label}  [dim]{arg}[/dim]"
    return label


def _tool_result_summary(tool_name: str, result: str) -> str | None:
    """One-line summary of a tool result to show in the UI."""
    if not result:
        return None
    lines = result.splitlines()
    n = len(lines)
    if tool_name in ("read_file", "list_files") and n > 1:
        return f"[dim]({n} lines)[/dim]"
    if tool_name == "run_tests":
        # Try to find pass/fail summary
        for line in reversed(lines):
            if "passed" in line or "failed" in line or "error" in line:
                status = "✅" if "failed" not in line and "error" not in line else "❌"
                return f"[dim]{status} {line.strip()}[/dim]"
    if tool_name == "compile_project":
        ok = not any(w in result.lower() for w in ("error", "fail"))
        return "[dim]✅ OK[/dim]" if ok else "[dim]❌ Error[/dim]"
    if tool_name in ("create_file", "update_file", "delete_file"):
        return "[dim]✅ Done[/dim]"
    if tool_name == "commit_changes":
        for line in lines:
            if line.strip().startswith("["):
                return f"[dim]{line.strip()}[/dim]"
    return None


# ── Main streaming renderer ───────────────────────────────────────────────────

class LiveRenderer:
    """
    Renders agent events to the terminal in real time, Claude-Code style:
      - Each tool call shows on its own line with icon and file name
      - Final response streams as live Markdown
      - Session stats printed at the end
    """

    def __init__(self) -> None:
        self._start = time.time()
        self._tool_calls: list[tuple[str, dict, str]] = []  # (name, input, result)

    def render(self, events: Iterator[AgentEvent]) -> str:
        """Consume the event stream, render everything, return final text."""
        final_text = ""
        thinking_shown = False

        for event in events:

            if isinstance(event, ThinkingEvent):
                if not thinking_shown:
                    console.print()
                    thinking_shown = True

            elif isinstance(event, ToolCallEvent):
                if event.tool_result:
                    # Complete event (both call + result arrived)
                    self._render_tool_complete(event)
                    self._tool_calls.append((event.tool_name, event.tool_input, event.tool_result))
                else:
                    # Just the call — result will come in next event
                    self._render_tool_calling(event)

            elif isinstance(event, TokenEvent) and event.is_final:
                final_text = event.text
                # Stream the final markdown response
                self._render_final_response(final_text)

        return final_text

    def _render_tool_calling(self, event: ToolCallEvent) -> None:
        icon, color = _TOOL_ICONS.get(event.tool_name, ("⚙️ ", "white"))
        label = _tool_label(event.tool_name, event.tool_input)
        # Will be overwritten when result arrives (printed on same conceptual step)
        console.print(f"  {icon} [bold {color}]{label}[/bold {color}]  [dim]…[/dim]")

    def _render_tool_complete(self, event: ToolCallEvent) -> None:
        icon, color = _TOOL_ICONS.get(event.tool_name, ("⚙️ ", "white"))
        label = _tool_label(event.tool_name, event.tool_input)
        summary = _tool_result_summary(event.tool_name, event.tool_result) or ""
        console.print(f"  {icon} [bold {color}]{label}[/bold {color}]  {summary}")

    def _render_final_response(self, text: str) -> None:
        console.print()
        console.rule("[dim]Response[/dim]", style="dim")
        console.print()
        console.print(Markdown(text))

    def print_session_stats(self) -> None:
        """Print a compact session summary bar."""
        elapsed = time.time() - self._start
        reads   = sum(1 for n, _, _ in self._tool_calls if n in ("read_file", "list_files", "search_codebase"))
        writes  = sum(1 for n, _, _ in self._tool_calls if n in ("create_file", "update_file", "delete_file"))
        ran_tests = any(n == "run_tests" for n, _, _ in self._tool_calls)

        parts = []
        if reads:
            parts.append(f"[cyan]{reads} read[/cyan]")
        if writes:
            parts.append(f"[green]{writes} write{'s' if writes > 1 else ''}[/green]")
        if ran_tests:
            parts.append("[magenta]tests ran[/magenta]")
        parts.append(f"[dim]{elapsed:.1f}s[/dim]")

        console.print()
        console.print("  📊 " + " · ".join(parts))


# ── Colored diff utility ──────────────────────────────────────────────────────

def render_diff(path: str, old_content: str, new_content: str) -> None:
    """Print a colored unified diff, Claude-Code style."""
    diff_lines = list(difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    ))

    if not diff_lines:
        console.print("[dim](no changes)[/dim]")
        return

    text = Text()
    for line in diff_lines:
        if line.startswith("+++") or line.startswith("---"):
            text.append(line + "\n", style="bold")
        elif line.startswith("@@"):
            text.append(line + "\n", style="cyan")
        elif line.startswith("+"):
            text.append(line + "\n", style="green")
        elif line.startswith("-"):
            text.append(line + "\n", style="red")
        else:
            text.append(line + "\n", style="dim")

    console.print(Panel(text, title=f"[bold]diff — {path}[/bold]", border_style="yellow"))
