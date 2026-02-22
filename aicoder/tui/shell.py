"""Rich interactive REPL — Claude Code-like terminal experience."""
from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from aicoder.config import AiCoderConfig, create_llm, load_config
from aicoder.ingestor import CodebaseIngestor
from aicoder.memory import AgentMemory
from aicoder.tools import BuildTools, FileTools, GitTools, SearchTools
from aicoder.agents.single_agent import build_agent

console = Console()

BANNER = """
[bold cyan]╔══════════════════════════════════════════╗[/bold cyan]
[bold cyan]║[/bold cyan]  [bold white]🤖  A I C O D E R[/bold white]  [dim]Python Edition[/dim]  [bold cyan]║[/bold cyan]
[bold cyan]╚══════════════════════════════════════════╝[/bold cyan]
[dim]Type your request naturally. /help for commands. Ctrl+D to exit.[/dim]
"""


class InteractiveShell:
    """Rich-powered interactive REPL with live streaming output."""

    def __init__(self, project_root: Path, model_provider: str,
                 config: AiCoderConfig | None = None) -> None:
        self.root = project_root.resolve()
        self.provider = model_provider
        self.config = config or load_config()

    def run(self) -> None:
        console.print(BANNER)

        # Load memory
        memory = AgentMemory(self.root)
        n_actions = len(memory.recent_actions)
        if n_actions:
            console.print(f"[dim]💾 Memory loaded ({n_actions} past actions)[/dim]")

        console.print(f"[dim]📁 Project: {self.root}[/dim]")

        # Index codebase
        with console.status("[cyan]Indexing codebase...[/cyan]", spinner="dots"):
            ingestor = CodebaseIngestor(self.root)
            new_chunks = ingestor.ingest(quiet=True)

        console.print(
            f"[green]✓[/green] Indexed [bold]{ingestor.total_chunks}[/bold] chunks"
            + (f" ({new_chunks} new)" if new_chunks else " (no changes)")
        )

        # Build tools
        file_tools  = FileTools(self.root).get_tools()
        build_tools = BuildTools(self.root).get_tools()
        git_tools   = GitTools(self.root).get_tools()
        search_tools = SearchTools(ingestor).get_tools()
        all_tools   = file_tools + build_tools + git_tools + search_tools

        # Create LLM and agent
        with console.status(f"[cyan]Connecting to {self.provider}...[/cyan]", spinner="dots"):
            llm = create_llm(self.provider, self.config)

        executor = build_agent(llm, all_tools)
        console.print(f"[green]✓[/green] Model: [bold]{self.provider}[/bold] (streaming)\n")

        memory_context = memory.to_prompt_context()

        # ── REPL loop ─────────────────────────────────────────────────────
        while True:
            try:
                user_input = Prompt.ask("[bold green]>[/bold green]")
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input.strip():
                continue

            # Slash commands
            if user_input.startswith("/"):
                if self._handle_command(user_input.strip(), memory):
                    continue
                else:
                    break

            # Build prompt with memory context
            prompt = user_input
            if memory_context:
                prompt = f"{user_input}\n\n{memory_context}"

            # Stream the response with Rich Live
            console.print()
            console.rule("[dim]Agent Response[/dim]", style="dim")
            console.print()

            full_response = ""
            try:
                with Live(console=console, refresh_per_second=15) as live:
                    for event in executor.stream({"input": prompt}):
                        if isinstance(event, dict) and "output" in event:
                            full_response = event["output"]
                            live.update(Markdown(full_response))

                console.print()
                memory.add_action(user_input)
                memory.save()
                memory_context = memory.to_prompt_context()

            except Exception as e:
                console.print(f"[red]❌ Agent error: {e}[/red]")

        memory.save()
        console.print("\n[green]Session ended. Memory saved. Goodbye! 👋[/green]\n")

    def _handle_command(self, cmd: str, memory: AgentMemory) -> bool:
        """Handle slash commands. Returns False to exit the REPL."""
        match cmd.lower():
            case "/help":
                console.print(Panel(
                    "[bold]/help[/bold]    — Show this help\n"
                    "[bold]/memory[/bold]  — Show stored memory\n"
                    "[bold]/clear[/bold]   — Clear screen\n"
                    "[bold]/quit[/bold]    — Exit AICoder\n\n"
                    "[dim]Examples:[/dim]\n"
                    "  look at main.py\n"
                    "  fix the NPE in UserService.py\n"
                    "  refactor the auth module to use async/await",
                    title="Commands", border_style="cyan"
                ))
            case "/memory":
                ctx = memory.to_prompt_context()
                if ctx:
                    console.print(Markdown(ctx))
                else:
                    console.print("[dim]No memory stored yet.[/dim]")
            case "/clear":
                console.clear()
            case "/quit" | "/exit" | "/q":
                return False
            case _:
                console.print(f"[red]Unknown command: {cmd}[/red]")
        return True
