"""AICoder CLI — the main entry point (registered as `aicoder` by pyproject.toml)."""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from aicoder import __version__
from aicoder.config import AiCoderConfig, create_llm, load_config
from aicoder.ingestor import CodebaseIngestor
from aicoder.memory import AgentMemory
from aicoder.mcp_loader import McpServerLoader
from aicoder.tools import AstTools, BuildTools, FileTools, GitTools, SearchTools

app = typer.Typer(
    name="aicoder",
    help="🤖 Autonomous AI coding agent — Python edition",
    no_args_is_help=False,
    rich_markup_mode="rich",
)
console = Console()


class Provider(str, Enum):
    openai    = "openai"
    anthropic = "anthropic"
    deepseek  = "deepseek"
    ollama    = "ollama"


# ── Shared options ────────────────────────────────────────────────────────────

def _common(
    model: Provider = typer.Option(Provider.deepseek, "--model", "-m", help="LLM provider"),
    directory: Path = typer.Option(Path("."), "--dir", "-d", help="Target project directory"),
    config: Optional[Path] = typer.Option(None, "--config", help="Path to .aicoder.yml"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview changes without writing"),
):
    pass  # used for type hints only


def _setup(
    model: str,
    directory: Path,
    config_path: Path | None,
    dry_run: bool,
    mcp_config: Path | None = None,
    interactive: bool = False,
):
    """Load config, build tools, create LLM. Returns (llm, all_tools, ro_tools, build_tools, memory)."""
    root = directory.resolve()
    if not root.is_dir():
        console.print(f"[red]❌ Directory not found: {root}[/red]")
        raise typer.Exit(1)

    cfg = load_config(config_path)

    with console.status("[cyan]Indexing codebase...[/cyan]", spinner="dots"):
        ingestor = CodebaseIngestor(root)
        ingestor.ingest(quiet=True)

    ft   = FileTools(root, dry_run=dry_run, interactive=interactive)
    bt   = BuildTools(root)
    gt   = GitTools(root)
    st   = SearchTools(ingestor)
    at   = AstTools(root)
    mem  = AgentMemory(root)

    file_tools_list   = ft.get_tools()
    build_tools_list  = bt.get_tools()
    git_tools_list    = gt.get_tools()
    search_tools_list = st.get_tools()
    ast_tools_list    = at.get_tools()

    all_tools      = file_tools_list + build_tools_list + git_tools_list + search_tools_list + ast_tools_list
    ro_tools       = search_tools_list + [t for t in file_tools_list if t.name in ("read_file", "list_files")]
    tester_tools   = build_tools_list + search_tools_list + [t for t in file_tools_list if t.name == "read_file"]

    # ── MCP servers ────────────────────────────────────────────────────────
    mcp_loader = McpServerLoader(root, mcp_config)
    if mcp_loader.config_exists():
        mcp_tools = mcp_loader.load_tools_sync()
        if mcp_tools:
            all_tools = all_tools + mcp_tools
            console.print(f"[green]✓[/green] {len(mcp_tools)} MCP tool(s) loaded")
    elif mcp_config:  # user explicitly passed --mcp but file not found
        console.print(f"[yellow]⚠️  MCP config not found: {mcp_config}[/yellow]")

    llm = create_llm(model, cfg)
    return llm, all_tools, ro_tools, tester_tools, mem


# ── Commands ──────────────────────────────────────────────────────────────────

@app.callback(invoke_without_command=True)
def default(
    ctx: typer.Context,
    model: Provider = typer.Option(Provider.deepseek, "--model", "-m"),
    directory: Path = typer.Option(Path("."), "--dir", "-d"),
    config: Optional[Path] = typer.Option(None, "--config"),
    version: bool = typer.Option(False, "--version", "-v", is_eager=True),
):
    """Launch interactive REPL when called with no subcommand."""
    if version:
        console.print(f"aicoder {__version__}")
        raise typer.Exit()

    if ctx.invoked_subcommand is None:
        # Interactive mode
        from aicoder.tui.shell import InteractiveShell
        cfg = load_config(config)
        InteractiveShell(directory.resolve(), model.value, cfg).run()


@app.command()
def fix(
    instruction: str = typer.Argument(..., help="What to fix"),
    model: Provider = typer.Option(Provider.deepseek, "--model", "-m"),
    directory: Path = typer.Option(Path("."), "--dir", "-d"),
    config: Optional[Path] = typer.Option(None, "--config"),
    mcp: Optional[Path] = typer.Option(None, "--mcp", help="Path to mcp.json (default: .aicoder/mcp.json)"),
    spec: Optional[Path] = typer.Option(None, "--spec", "-s", help="Design doc / spec MD file"),
    interactive: bool = typer.Option(False, "--interactive", "-i", help="Review each change before applying"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Fix a bug or implement a feature."""
    _run_batch("fix", instruction, model.value, directory, config, dry_run, mcp, interactive, spec)


@app.command()
def refactor(
    instruction: str = typer.Argument(..., help="What to refactor"),
    model: Provider = typer.Option(Provider.deepseek, "--model", "-m"),
    directory: Path = typer.Option(Path("."), "--dir", "-d"),
    config: Optional[Path] = typer.Option(None, "--config"),
    mcp: Optional[Path] = typer.Option(None, "--mcp"),
    spec: Optional[Path] = typer.Option(None, "--spec", "-s"),
    interactive: bool = typer.Option(False, "--interactive", "-i"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Refactor code without changing behavior."""
    _run_batch("refactor", instruction, model.value, directory, config, dry_run, mcp, interactive, spec)


@app.command()
def audit(
    instruction: str = typer.Argument(..., help="What to review"),
    model: Provider = typer.Option(Provider.deepseek, "--model", "-m"),
    directory: Path = typer.Option(Path("."), "--dir", "-d"),
    since: Optional[str] = typer.Option(None, "--since", help="Git ref to diff from (e.g. main)"),
    config: Optional[Path] = typer.Option(None, "--config"),
    mcp: Optional[Path] = typer.Option(None, "--mcp"),
    spec: Optional[Path] = typer.Option(None, "--spec", "-s"),
):
    """Review code for issues."""
    full_instruction = instruction
    if since:
        full_instruction = f"{instruction}\n\nFocus on changes since: {since} (use get_diff_since tool)"
    _run_batch("audit", full_instruction, model.value, directory, config, dry_run=True, mcp_config=mcp,
               interactive=False, spec=spec)


@app.command()
def run(
    instruction: str = typer.Argument(..., help="Task description"),
    model: Provider = typer.Option(Provider.deepseek, "--model", "-m"),
    directory: Path = typer.Option(Path("."), "--dir", "-d"),
    agents: bool = typer.Option(False, "--agents", help="Enable multi-agent mode"),
    config: Optional[Path] = typer.Option(None, "--config"),
    mcp: Optional[Path] = typer.Option(None, "--mcp"),
    spec: Optional[Path] = typer.Option(None, "--spec", "-s"),
    interactive: bool = typer.Option(False, "--interactive", "-i"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Run a task (single-agent or multi-agent with --agents)."""
    if agents:
        _run_multi_agent(instruction, model.value, directory, config, dry_run, mcp, interactive, spec)
    else:
        _run_batch("run", instruction, model.value, directory, config, dry_run, mcp, interactive, spec)


@app.command(name="test-gen")
def test_gen(
    target: str = typer.Argument(..., help="File or class to generate tests for"),
    model: Provider = typer.Option(Provider.deepseek, "--model", "-m"),
    directory: Path = typer.Option(Path("."), "--dir", "-d"),
    config: Optional[Path] = typer.Option(None, "--config"),
    mcp: Optional[Path] = typer.Option(None, "--mcp"),
    spec: Optional[Path] = typer.Option(None, "--spec", "-s"),
    interactive: bool = typer.Option(False, "--interactive", "-i"),
    dry_run: bool = typer.Option(False, "--dry-run"),
):
    """Generate a comprehensive test suite for a file or class."""
    instruction = (
        f"Generate a comprehensive test suite for: {target}\n"
        "1. Read the file to understand all public functions/methods\n"
        "2. Check existing test files to match conventions\n"
        "3. Write tests covering happy paths, edge cases, and error cases\n"
        "4. Compile and run the tests\n"
        "5. Fix any failures (max 3 retries)"
    )
    _run_batch("test-gen", instruction, model.value, directory, config, dry_run, mcp, interactive, spec)


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


# ── Internal helpers ──────────────────────────────────────────────────────────

def _load_spec(spec: Path | None) -> str:
    """Read a design doc / spec MD file. Returns formatted context block or empty string."""
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


def _run_batch(mode: str, instruction: str, model: str,
               directory: Path, config: Path | None, dry_run: bool,
               mcp_config: Path | None = None,
               interactive: bool = False,
               spec: Path | None = None) -> None:
    from aicoder.agents.single_agent import run_single_agent

    _print_banner(mode, model, directory)
    if interactive:
        console.print("[bold yellow]🔍 Interactive mode — you will approve each file change[/bold yellow]")

    llm, all_tools, _, _, memory = _setup(model, directory, config, dry_run, mcp_config, interactive)
    memory_ctx = memory.to_prompt_context()
    spec_ctx   = _load_spec(spec)

    full_instruction = instruction + spec_ctx
    if memory_ctx:
        full_instruction = full_instruction + "\n\n" + memory_ctx

    with console.status("[cyan]Agent is working...[/cyan]", spinner="dots"):
        result = run_single_agent(full_instruction, llm, all_tools)

    console.print()
    console.rule("[dim]Result[/dim]", style="dim")
    from rich.markdown import Markdown
    console.print(Markdown(result))
    memory.add_action(f"{mode.upper()}: {instruction[:80]}")
    memory.save()


def _run_multi_agent(instruction: str, model: str,
                     directory: Path, config: Path | None, dry_run: bool,
                     mcp_config: Path | None = None,
                     interactive: bool = False,
                     spec: Path | None = None) -> None:
    from aicoder.agents.multi_agent import run_multi_agent

    _print_banner("multi-agent", model, directory)
    if interactive:
        console.print("[bold yellow]🔍 Interactive mode — you will approve each file change[/bold yellow]")

    llm, all_tools, ro_tools, build_tools_list, memory = _setup(
        model, directory, config, dry_run, mcp_config, interactive
    )
    spec_ctx = _load_spec(spec)
    full_instruction = instruction + spec_ctx

    console.print("[bold cyan]🚀 Launching: Planner → Coder + Reviewer + Tester → Synthesis[/bold cyan]\n")
    result = run_multi_agent(full_instruction, llm, all_tools, ro_tools, build_tools_list)

    console.print()
    console.rule("[dim]Final Summary[/dim]", style="dim")
    from rich.markdown import Markdown
    console.print(Markdown(result))
    memory.add_action(f"MULTI-AGENT: {instruction[:80]}")
    memory.save()


def _print_banner(mode: str, model: str, directory: Path) -> None:
    console.print(Panel(
        f"[bold]Mode:[/bold]  {mode}\n"
        f"[bold]Model:[/bold] {model}\n"
        f"[bold]Dir:[/bold]   {directory.resolve()}",
        title="[bold cyan]🤖 AICoder[/bold cyan]",
        border_style="cyan",
    ))
    console.print()


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
    writes a ready-to-use .aicoder.yml.  Works for any enterprise that follows
    the standard pattern: acquire token → send to hosted LLM endpoint.
    """
    from aicoder.enterprise_wizard import run_wizard

    dest = output or (directory.resolve() / ".aicoder.yml")

    if dest.exists():
        overwrite = typer.confirm(f"⚠️  {dest} already exists. Overwrite?", default=False)
        if not overwrite:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit()

    run_wizard(dest)


if __name__ == "__main__":
    app()
