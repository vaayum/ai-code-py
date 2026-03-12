
"""AICoder CLI — the main entry point (registered as `aicoder` by pyproject.toml).

Usage (smart single command):
    aicoder "fix the null-check bug in UserService"
    aicoder "audit for SQL injection and hardcoded secrets"
    aicoder "refactor payment module to use repository pattern"
    aicoder "add tests for the auth module"
    aicoder "add rate limiting to all API endpoints" --agents
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from aicoder import __version__
from aicoder.config import AiCoderConfig, create_llm, load_config
from aicoder.tools.memory_tools import make_memory_tools
from aicoder.ingestor import CodebaseIngestor
from aicoder.memory import AgentMemory
from aicoder.mcp_loader import McpServerLoader
from aicoder.tools import AstTools, BuildTools, FileTools, GitTools, SearchTools

app = typer.Typer(
    name="aicoder",
    help="🤖 Autonomous AI coding agent — just describe what you want done.",
    no_args_is_help=False,
    rich_markup_mode="rich",
    context_settings={"allow_interspersed_args": True, "max_content_width": 120},
)
console = Console()

# ── Intent classifier ─────────────────────────────────────────────────────────
# Detects the type of task from natural language — no LLM call needed.

_AUDIT_WORDS = re.compile(
    r"\b(audit|review|check|scan|security|vulnerabilit|secret|leak|sql.?inject"
    r"|xss|csrf|pentest|lint|smell|compliance|pii|gdpr)\b", re.I
)
_REFACTOR_WORDS = re.compile(
    r"\b(refactor|restructure|reorganize|extract|rename|clean.?up|decouple"
    r"|simplif|modularize|solid|dry|pattern|architecture|redesign)\b", re.I
)
_TEST_WORDS = re.compile(
    r"\b(tests?|spec|unittest|pytest|coverage|assert|mock|fixture|tdd)\b", re.I
)


def _classify(instruction: str) -> str:
    """Return 'audit', 'refactor', 'test-gen', or 'fix' based on the instruction text."""
    if _AUDIT_WORDS.search(instruction):
        return "audit"
    if _TEST_WORDS.search(instruction):
        return "test-gen"
    if _REFACTOR_WORDS.search(instruction):
        return "refactor"
    return "fix"


# ── Setup ─────────────────────────────────────────────────────────────────────

def _setup(
    model: str,
    directory: Path,
    config_path: Path | None,
    dry_run: bool,
    mcp_config: Path | None = None,
    interactive: bool = False,
    reindex: bool = False,
):
    """Load config, build tools, create LLM. Returns (llm, all_tools, ro_tools, build_tools, memory)."""
    root = directory.resolve()
    if not root.is_dir():
        console.print(f"[red]❌ Directory not found: {root}[/red]")
        raise typer.Exit(1)

    cfg = load_config(config_path)
    ingestor = CodebaseIngestor(root)

    new_chunks = 0
    if not ingestor.is_indexed() or reindex:
        with console.status("[cyan]Indexing codebase (first run or --reindex)...[/cyan]", spinner="dots"):
            new_chunks = ingestor.ingest(quiet=True)
    else:
        console.print("[dim]✓ Base index loaded (lazy)[/dim]")

    mem = AgentMemory(root)
    ft  = FileTools(root, dry_run=dry_run, interactive=interactive, memory=mem, ingestor=ingestor)
    bt  = BuildTools(root)
    gt  = GitTools(root)
    st  = SearchTools(ingestor)
    at  = AstTools(root)

    file_tools_list   = ft.get_tools()
    build_tools_list  = bt.get_tools()
    git_tools_list    = gt.get_tools()
    search_tools_list = st.get_tools()
    ast_tools_list    = at.get_tools()
    memory_tools_list = make_memory_tools(mem)

    all_tools    = file_tools_list + build_tools_list + git_tools_list + search_tools_list + ast_tools_list + memory_tools_list
    ro_tools     = search_tools_list + memory_tools_list + [t for t in file_tools_list if t.name in ("read_file", "list_files")]
    tester_tools = build_tools_list + search_tools_list + [t for t in file_tools_list if t.name == "read_file"]

    # ── MCP servers ────────────────────────────────────────────────────────────
    mcp_loader = McpServerLoader(root, mcp_config)
    if mcp_loader.config_exists():
        mcp_tools = mcp_loader.load_tools_sync()
        if mcp_tools:
            all_tools = all_tools + mcp_tools
            console.print(f"[green]✓[/green] {len(mcp_tools)} MCP tool(s) loaded")
    elif mcp_config:
        console.print(f"[yellow]⚠️  MCP config not found: {mcp_config}[/yellow]")

    llm = create_llm(model, cfg)

    if not mem.project_summary and new_chunks > 0:
        with console.status("[cyan]Generating project summary...[/cyan]", spinner="dots"):
            from aicoder.summarizer import generate_project_summary
            summary = generate_project_summary(llm, root)
            if summary:
                mem.set_project_summary(summary)
                mem.save()
                console.print(f"[green]✓[/green] [dim]{summary}[/dim]\n")

    return llm, all_tools, ro_tools, tester_tools, mem


# ── Main smart command ────────────────────────────────────────────────────────

@app.callback(invoke_without_command=True)
def default(
    ctx: typer.Context,
    instruction: Optional[str] = typer.Argument(None, help="What to do — describe in plain English"),
    model: str = typer.Option("deepseek", "--model", "-m", help="Provider: anthropic|openai|deepseek|gemini|ollama|enterprise"),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Target project directory"),
    config: Optional[Path] = typer.Option(None, "--config", help="Path to .aicoder.yml"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Review each change before applying"),
    reindex: bool = typer.Option(False, "--reindex", help="Force a full rebuild of the codebase vector index"),
    auto_route: bool = typer.Option(True, "--auto-route/--no-auto-route", help="Dynamically choose single vs multi-agent based on task complexity"),
    agents: bool = typer.Option(False, "--agents", help="Force multi-agent pipeline (Planner→Coder→Reviewer→Tester)"),
    since: Optional[str] = typer.Option(None, "--since", help="Git ref — only consider changes since this branch/commit"),
    spec: Optional[Path] = typer.Option(None, "--spec", "-s", help="Design doc / spec file to follow"),
    mcp: Optional[Path] = typer.Option(None, "--mcp", help="Path to mcp.json"),
    version: bool = typer.Option(False, "--version", "-v", is_eager=True, help="Show version"),
):
    """
    🤖 Describe what you want — AICoder figures out the rest.

    Examples:

      aicoder "fix the NPE in UserService"
      aicoder "audit for SQL injection and hardcoded secrets"
      aicoder "refactor payment module to use repository pattern"
      aicoder "add tests for the auth service"
      aicoder "add rate limiting to all endpoints" --agents
      aicoder "implement auth" --spec design/auth-spec.md
      aicoder "review security issues" --since main
    """
    if version:
        console.print(f"aicoder {__version__}")
        raise typer.Exit()

    # Sub-command was explicitly invoked (models, enterprise-init, etc.)
    if ctx.invoked_subcommand is not None:
        return

    # No instruction → launch interactive REPL
    if not instruction:
        from aicoder.tui.shell import InteractiveShell
        cfg = load_config(config)
        InteractiveShell(directory.resolve(), model, cfg, reindex=reindex, interactive=interactive).run()
        return

    # ── Auto-route intent ────────────────────────────────────────────────────
    mode = _classify(instruction)

    # Augment instruction based on detected mode
    if mode == "audit":
        dry_run = True   # audits never write files
        if since:
            instruction = f"{instruction}\n\nFocus on changes since git ref: {since} (use get_diff_since tool)"
    elif mode == "test-gen":
        instruction = (
            f"{instruction}\n\n"
            "Steps: 1) Read the target file(s), 2) Check existing test conventions, "
            "3) Write tests covering happy paths + edge cases + errors, 4) Run tests, "
            "5) Fix failures (max 3 retries)."
        )

    use_agents = agents
    if auto_route and not agents:
        cfg = load_config(config)
        llm = create_llm(model, cfg)
        from aicoder.agents.router_agent import route_instruction
        with console.status("[cyan]Analyzing task complexity...[/cyan]", spinner="dots"):
            route = route_instruction(llm, instruction)
            
        console.print(f"[bold magenta]⚡ Smart Router:[/bold magenta] [yellow]{route['mode']}[/yellow] — [italic dim]{route['reasoning']}[/italic dim]")
        use_agents = route["mode"] == "MULTI_AGENT"

    if use_agents:
        from aicoder.cli import _run_multi_agent # Need to import locally if not already available in this scope, wait it's just below
        # Wait, the original code called _run_multi_agent directly, meaning it's in cli.py or imported.
        # Looking at original code:
        pass # removed this, just keeping the call
        
    if use_agents:
        _run_multi_agent(instruction, mode, model, directory, config, dry_run, mcp, interactive, spec, reindex)
    else:
        _run_batch(instruction, mode, model, directory, config, dry_run, mcp, interactive, spec, reindex)


# ── Execution helpers ─────────────────────────────────────────────────────────

def _load_spec(spec: Path | None) -> str:
    if spec is None:
        return ""
    path = Path(spec).resolve()
    if not path.exists():
        console.print(f"[yellow]⚠️  Spec file not found: {spec}[/yellow]")
        return ""
    content = path.read_text(errors="replace")
    console.print(f"[green]📄 Loaded spec:[/green] {path.name} ({len(content):,} chars)")
    return (
        f"\n\n---\n## 📄 Design Specification: {path.name}\n\n"
        f"{content}\n\n"
        "**Important**: Implement exactly according to the above spec. "
        "Read the spec carefully before starting any work."
        "\n---\n"
    )


def _run_batch(
    instruction: str,
    mode: str,
    model: str,
    directory: Path,
    config: Path | None,
    dry_run: bool,
    mcp_config: Path | None = None,
    interactive: bool = False,
    spec: Path | None = None,
    reindex: bool = False,
) -> None:
    from aicoder.agents.single_agent import stream_agent_events
    from aicoder.tui.renderer import LiveRenderer

    _print_banner(mode, model, directory)
    if interactive:
        console.print("[bold yellow]🔍 Interactive mode — you will approve each file change[/bold yellow]")
    if dry_run:
        console.print("[bold dim]👁  Dry-run — no files will be written[/bold dim]")

    llm, all_tools, _, _, memory = _setup(
        model, directory, config, dry_run, mcp_config, interactive, reindex
    )
    spec_ctx   = _load_spec(spec)
    memory_ctx = memory.to_prompt_context()

    full_instruction = instruction + spec_ctx
    if memory_ctx:
        full_instruction += "\n\n" + memory_ctx

    renderer = LiveRenderer()
    try:
        events = stream_agent_events(full_instruction, llm, all_tools)
        result = renderer.render(events)
    except KeyboardInterrupt:
        console.print("\n[yellow]⚡ Cancelled.[/yellow]")
        return

    renderer.print_session_stats()

    if result:
        memory.add_action(f"{mode.upper()}: {instruction[:80]}")
        memory.save()


def _run_multi_agent(
    instruction: str,
    mode: str,
    model: str,
    directory: Path,
    config: Path | None,
    dry_run: bool,
    mcp_config: Path | None = None,
    interactive: bool = False,
    spec: Path | None = None,
    reindex: bool = False,
) -> None:
    from aicoder.agents.multi_agent import run_multi_agent

    _print_banner(f"multi-agent / {mode}", model, directory)
    if interactive:
        console.print("[bold yellow]🔍 Interactive mode — you will approve each file change[/bold yellow]")

    llm, all_tools, ro_tools, build_tools_list, memory = _setup(
        model, directory, config, dry_run, mcp_config, interactive, reindex
    )
    spec_ctx = _load_spec(spec)
    full_instruction = instruction + spec_ctx

    console.print("[bold cyan]🚀 Launching: Planner → Coder + Reviewer + Tester → Synthesis[/bold cyan]\\n")
    result = run_multi_agent(full_instruction, llm, all_tools, ro_tools, build_tools_list, interactive)

    console.print()
    console.rule("[dim]Final Summary[/dim]", style="dim")
    from rich.markdown import Markdown
    console.print(Markdown(result))
    memory.add_action(f"MULTI-AGENT/{mode.upper()}: {instruction[:80]}")
    memory.save()


def _print_banner(mode: str, model: str, directory: Path) -> None:
    console.print(Panel(
        f"[bold]Task:[/bold]  {mode}\n"
        f"[bold]Model:[/bold] {model}\n"
        f"[bold]Dir:[/bold]   {directory.resolve()}",
        title="[bold cyan]🤖 AICoder[/bold cyan]",
        border_style="cyan",
    ))
    console.print()


# ── Utility commands ──────────────────────────────────────────────────────────

@app.command(name="models")
def list_models():
    """Show recommended models for BYOK (cloud) and local (Ollama) deployment."""
    from aicoder.config_printer import print_catalog
    print_catalog()


@app.command(name="enterprise-init")
def enterprise_init(
    directory: Path = typer.Option(Path("."), "--dir", "-d",
                                   help="Project directory to write .aicoder.yml into"),
    output: Optional[Path] = typer.Option(None, "--output", "-o",
                                          help="Custom output path (default: <dir>/.aicoder.yml)"),
):
    """
    Interactive wizard — configure AICoder for an on-premise corporate LLM.

    Walks through 5 steps (endpoint, auth, TLS, proxy, agent settings) and
    writes a ready-to-use .aicoder.yml.
    """
    from aicoder.enterprise_wizard import run_wizard

    dest = output or (directory.resolve() / ".aicoder.yml")

    if dest.exists():
        overwrite = typer.confirm(f"⚠️  {dest} already exists. Overwrite?", default=False)
        if not overwrite:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit()

    run_wizard(dest)


@app.command(name="mcp-init")
def mcp_init(
    directory: Path = typer.Option(Path("."), "--dir", "-d"),
):
    """Create a starter .aicoder/mcp.json in your project."""
    from aicoder.mcp_loader import McpServerLoader
    loader = McpServerLoader(directory.resolve())
    target = directory.resolve() / ".aicoder" / "mcp.json"
    if target.exists():
        console.print(f"[yellow]Already exists: {target}[/yellow]")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(loader.example_config())
    console.print(f"[green]✅ Created: {target}[/green]")
    console.print("[dim]Edit it to add your MCP servers, then re-run any aicoder command.[/dim]")


if __name__ == "__main__":
    app()
