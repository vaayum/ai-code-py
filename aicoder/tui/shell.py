"""
AICoder interactive shell — Claude Code-style terminal experience.

Features:
  ✅ prompt_toolkit input  — multi-line, history (↑↓), auto-suggest
  ✅ Live tool-call display — "🔍 read_file  auth.py" as it happens
  ✅ Streaming response     — token-by-token with Rich Live
  ✅ Session stats bar      — "3 reads · 2 writes · 4.1s"
  ✅ Slash commands         — /help /memory /files /diff /branch /undo /clear /quit
  ✅ Intent auto-detect     — detects fix/audit/refactor/test from your words
  ✅ Keyboard shortcuts     — Ctrl+C cancel turn, Ctrl+D exit
"""
from __future__ import annotations

import os
import time
from pathlib import Path

# ── Rich ──────────────────────────────────────────────────────────────────────
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from langchain_core.messages import AIMessage, HumanMessage

# ── prompt_toolkit ────────────────────────────────────────────────────────────
from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from prompt_toolkit.completion import Completer, Completion

# ── AICoder internals ─────────────────────────────────────────────────────────
from aicoder.agents.single_agent import (
    AgentEvent,
    ThinkingEvent,
    TokenEvent,
    ToolCallEvent,
    build_agent,
    stream_agent_events,
)
from aicoder.config import AiCoderConfig, create_llm, load_config
from aicoder.ingestor import CodebaseIngestor
from aicoder.memory import AgentMemory
from aicoder.tui.renderer import LiveRenderer, render_diff
from aicoder.tools import AstTools, BuildTools, FileTools, GitTools, SearchTools
from aicoder.tools.memory_tools import make_memory_tools

console = Console()

# ── prompt_toolkit styling ────────────────────────────────────────────────────

_STYLE = Style.from_dict({
    "prompt":        "#00ff87 bold",
    "prompt.arrow":  "#00ff87",
    "bottom-toolbar": "bg:#1a1a2e fg:#888888",
    "completion-menu.completion": "bg:#1a1a2e fg:#aaaaaa",
    "completion-menu.completion.current": "bg:#00ff87 fg:#000000",
})

_BINDINGS = KeyBindings()

class SlashCommandCompleter(Completer):
    COMMANDS = [
        ('/help', 'Show all available slash commands'),
        ('/clear', 'Clear the screen'),
        ('/quit', 'Exit AICoder'),
        ('/exit', 'Exit AICoder'),
        ('/memory', 'View the agent\'s memory'),
        ('/files', 'List files modified this session'),
        ('/diff', 'Show git diff of modified files'),
        ('/branch', 'Show current git branch'),
        ('/tests', 'Run the test suite'),
        ('/undo', 'Revert the last file update'),
    ]

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith('/'):
            for cmd, desc in self.COMMANDS:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text), display_meta=desc)


@_BINDINGS.add("c-c")
def _cancel(event):
    """Ctrl+C clears the current input buffer without exiting."""
    event.current_buffer.reset()


# ── Banner ────────────────────────────────────────────────────────────────────

_BANNER = """
[bold cyan]╔══════════════════════════════════════════════╗[/bold cyan]
[bold cyan]║[/bold cyan]  [bold white]🤖  A I C O D E R[/bold white]  [dim]Python Edition[/dim]     [bold cyan]║[/bold cyan]
[bold cyan]║[/bold cyan]  [dim]Type anything. /help for commands. ^D exit.[/dim]  [bold cyan]║[/bold cyan]
[bold cyan]╚══════════════════════════════════════════════╝[/bold cyan]
"""

# ── Tool-call icon map (for the live display) ─────────────────────────────────

_TOOL_ICONS: dict[str, tuple[str, str]] = {
    "read_file":           ("🔍", "cyan"),
    "list_files":          ("📂", "cyan"),
    "create_file":         ("✨", "green"),
    "update_file":         ("✏️ ", "yellow"),
    "delete_file":         ("🗑️ ", "red"),
    "search_codebase":     ("🔎", "blue"),
    "compile_project":     ("🏗️ ", "magenta"),
    "run_tests":           ("🧪", "magenta"),
    "execute_shell":       ("💻", "white"),
    "get_git_status":      ("🌿", "green"),
    "checkout_new_branch": ("🌿", "green"),
    "commit_changes":      ("💾", "green"),
    "get_diff":            ("📝", "yellow"),
    "get_diff_since":      ("📝", "yellow"),
    "add_function":        ("➕", "green"),
    "replace_function_body":("♻️ ", "yellow"),
    "add_import":          ("📦", "cyan"),
    "rename_symbol":       ("🏷️ ", "yellow"),
}


class InteractiveShell:
    """Rich + prompt_toolkit interactive REPL."""

    def __init__(
        self,
        project_root: Path,
        model_provider: str,
        config: AiCoderConfig | None = None,
        reindex: bool = False,
        interactive: bool = False,
    ) -> None:
        self.root     = project_root.resolve()
        self.provider = model_provider
        self.config   = config or load_config()
        self.reindex  = reindex
        # CLI flag takes precedence, otherwise fallback to config file
        self.interactive = interactive or self.config.agent.interactive
        self._written_files: list[tuple[str, str, str]] = []   # (path, old, new)
        self.chat_history: list = []  # Maintain conversation context

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self) -> None:
        console.print(_BANNER)

        # Load memory and show stats
        memory = AgentMemory(self.root)
        if memory.recent_actions or memory.key_files or memory.conventions:
            console.print(f"[dim]{memory.stats()}[/dim]")

        console.print(f"[dim]📁 {self.root}[/dim]")
        if self.interactive:
            console.print("[dim]🔍 Interactive mode enabled (diff approval)[/dim]")

        # Index codebase
        ingestor = CodebaseIngestor(self.root)
        new_chunks = 0
        if not ingestor.is_indexed() or self.reindex:
            with console.status("[cyan]Indexing codebase (first run or --reindex)...[/cyan]", spinner="dots"):
                new_chunks = ingestor.ingest(quiet=True)
            console.print(
                f"[green]✓[/green] [bold]{ingestor.total_chunks}[/bold] chunks indexed"
                + (f"  [dim]({new_chunks} new)[/dim]" if new_chunks else "")
            )
        else:
            console.print("[dim]✓ Base index loaded (lazy)[/dim]")

        # Build tools (including memory tools bound to this session's memory)
        file_tools    = FileTools(self.root, memory=memory, ingestor=ingestor, interactive=self.interactive).get_tools()
        build_tools   = BuildTools(self.root).get_tools()
        git_tools     = GitTools(self.root).get_tools()
        search_tools  = SearchTools(ingestor).get_tools()
        ast_tools     = AstTools(self.root).get_tools()
        memory_tools  = make_memory_tools(memory)
        all_tools     = file_tools + build_tools + git_tools + search_tools + ast_tools + memory_tools

        # LLM
        with console.status(f"[cyan]Connecting to {self.provider}...[/cyan]", spinner="dots"):
            llm = create_llm(self.provider, self.config)

        if not memory.project_summary and new_chunks > 0:
            with console.status("[cyan]Generating project summary...[/cyan]", spinner="dots"):
                from aicoder.summarizer import generate_project_summary
                summary = generate_project_summary(llm, self.root)
                if summary:
                    memory.set_project_summary(summary)
                    memory.save()
                    console.print(f"[green]✓[/green] [dim]{summary}[/dim]\n")

        agent = build_agent(llm, all_tools)
        console.print(f"[green]✓[/green] [bold]{self.provider}[/bold] ready\n")

        # prompt_toolkit session with persistent history
        history_file = self.root / ".aicoder" / "history"
        history_file.parent.mkdir(parents=True, exist_ok=True)
        session: PromptSession = PromptSession(
            history=FileHistory(str(history_file)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=SlashCommandCompleter(),
            key_bindings=_BINDINGS,
            style=_STYLE,
            enable_history_search=True,
        )

        def _toolbar():
            try:
                import subprocess
                branch = subprocess.check_output(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=self.root, stderr=subprocess.DEVNULL, text=True
                ).strip()
                return HTML(f'  <b>{self.provider}</b>  ·  🌿 {branch}  ·  {self.root.name}')
            except Exception:
                return HTML(f'  <b>{self.provider}</b>  ·  {self.root.name}')

        # ── REPL loop ──────────────────────────────────────────────────────
        while True:
            try:
                user_input = session.prompt(
                    HTML("<prompt>❯ </prompt>"),
                    bottom_toolbar=_toolbar,
                    style=_STYLE,
                    multiline=False,
                )
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input.strip():
                continue

            # Slash commands
            if user_input.startswith("/"):
                if not self._handle_slash(user_input.strip(), memory, llm, all_tools):
                    break
                continue

            # Run the agent with live streaming
            t0 = time.time()
            renderer = LiveRenderer()
            console.print()

            try:
                events = stream_agent_events(
                    instruction=user_input,
                    llm=llm,
                    tools=all_tools,
                    memory_context=memory.to_prompt_context(),
                    chat_history=self.chat_history,
                )
                final_text = renderer.render(events)
                renderer.print_session_stats()

                if final_text:
                    memory.add_action(user_input[:80])
                    memory.save()
                    self.chat_history.append(HumanMessage(content=user_input))
                    self.chat_history.append(AIMessage(content=final_text))

            except KeyboardInterrupt:
                console.print("\n[yellow]⚡ Cancelled.[/yellow]")
            except Exception as exc:
                console.print(f"\n[red]❌ {exc}[/red]")

            console.print()

        memory.save()
        console.print("\n[green]👋 Session ended. Memory saved.[/green]\n")

    # ── Slash commands ────────────────────────────────────────────────────────

    def _handle_slash(self, cmd: str, memory: AgentMemory, llm, tools: list) -> bool:
        """Handle slash commands. Returns False to exit."""
        parts = cmd.split(None, 1)
        name  = parts[0].lower()
        arg   = parts[1] if len(parts) > 1 else ""

        match name:

            case "/help":
                console.print(Panel(
                    "[bold]Navigation[/bold]\n"
                    "  [cyan]/help[/cyan]          Show this help\n"
                    "  [cyan]/clear[/cyan]         Clear the screen\n"
                    "  [cyan]/quit[/cyan]          Exit AICoder\n\n"
                    "[bold]Project info[/bold]\n"
                    "  [cyan]/files[/cyan]         List files changed this session\n"
                    "  [cyan]/diff \\[file\\][/cyan]   Show git diff (optional: specific file)\n"
                    "  [cyan]/branch[/cyan]        Show current git branch\n"
                    "  [cyan]/tests[/cyan]         Run the test suite now\n\n"
                    "[bold]Memory[/bold]\n"
                    "  [cyan]/memory[/cyan]        Show stored agent memory\n"
                    "  [cyan]/undo[/cyan]          Revert the last file write\n\n"
                    "[bold]Examples[/bold]\n"
                    "  [dim]fix the null pointer in UserService[/dim]\n"
                    "  [dim]audit for SQL injection across all routes[/dim]\n"
                    "  [dim]refactor auth module to use dependency injection[/dim]\n"
                    "  [dim]add tests for the payment service[/dim]\n"
                    "  [dim]explain how the rate limiter works[/dim]",
                    title="[bold cyan]AICoder Commands[/bold cyan]",
                    border_style="cyan",
                ))

            case "/clear":
                console.clear()
                console.print(_BANNER)
                self.chat_history.clear()

            case "/quit" | "/exit" | "/q":
                return False

            case "/memory":
                ctx = memory.to_prompt_context()
                console.print(Markdown(ctx) if ctx else Text("No memory yet.", style="dim"))

            case "/files":
                if not self._written_files:
                    console.print("[dim]No files written this session.[/dim]")
                else:
                    for path, _, _ in self._written_files:
                        console.print(f"  [green]✏️[/green]  {path}")

            case "/diff":
                self._run_git_diff(arg.strip() or "")

            case "/branch":
                self._run_git_branch()

            case "/tests":
                from aicoder.tools.build_tools import BuildTools
                bt = {t.name: t for t in BuildTools(self.root).get_tools()}
                result = bt["run_tests"].invoke({})
                console.print(Markdown(f"```\n{result}\n```"))

            case "/undo":
                self._undo_last_write()

            case _:
                console.print(f"[red]Unknown command: {name}[/red]  (try [cyan]/help[/cyan])")

        return True

    # ── Slash command helpers ──────────────────────────────────────────────────

    def _run_git_diff(self, path: str) -> None:
        try:
            import subprocess
            args = ["git", "diff"]
            if path:
                args.append(path)
            out = subprocess.check_output(args, cwd=self.root, text=True, stderr=subprocess.DEVNULL)
            if not out.strip():
                console.print("[dim](no changes)[/dim]")
                return
            text = Text()
            for line in out.splitlines():
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
            console.print(Panel(text, title="git diff", border_style="yellow"))
        except Exception as e:
            console.print(f"[red]git diff failed: {e}[/red]")

    def _run_git_branch(self) -> None:
        try:
            import subprocess
            branch = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.root, text=True, stderr=subprocess.DEVNULL
            ).strip()
            log = subprocess.check_output(
                ["git", "log", "--oneline", "-5"],
                cwd=self.root, text=True, stderr=subprocess.DEVNULL
            ).strip()
            console.print(f"\n[bold green]🌿 {branch}[/bold green]\n")
            console.print(Markdown(f"```\n{log}\n```"))
        except Exception as e:
            console.print(f"[red]{e}[/red]")

    def _undo_last_write(self) -> None:
        if not self._written_files:
            console.print("[yellow]Nothing to undo.[/yellow]")
            return
        path, old, _ = self._written_files.pop()
        try:
            (self.root / path).write_text(old)
            console.print(f"[green]✅ Reverted:[/green] {path}")
        except Exception as e:
            console.print(f"[red]Failed to revert {path}: {e}[/red]")
