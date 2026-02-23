"""Pretty-prints the model catalog — used by `aicoder models` command."""
from __future__ import annotations


def print_catalog() -> None:
    """Print a formatted table of recommended models for BYOK and local use."""
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel

        console = Console()

        # ── BYOK table ────────────────────────────────────────────────────────
        byok_table = Table(show_header=True, header_style="bold cyan",
                           box=None, padding=(0, 1))
        byok_table.add_column("Model ID",        style="bold white", no_wrap=True)
        byok_table.add_column("Provider",         style="cyan",       no_wrap=True)
        byok_table.add_column("Env Var",          style="dim",        no_wrap=True)
        byok_table.add_column("Notes",            style="white")

        from aicoder.config import MODEL_CATALOG
        for model_id, provider, env_var, display, note in MODEL_CATALOG["byok"]:
            byok_table.add_row(model_id, provider, env_var, note)

        console.print(Panel(byok_table, title="[bold yellow]☁️  Cloud / BYOK Models[/bold yellow]",
                            border_style="yellow", expand=False))

        # ── Local table ───────────────────────────────────────────────────────
        local_table = Table(show_header=True, header_style="bold green",
                            box=None, padding=(0, 1))
        local_table.add_column("Model ID (ollama pull ...)", style="bold white", no_wrap=True)
        local_table.add_column("Min VRAM",                   style="cyan",       no_wrap=True)
        local_table.add_column("Notes",                      style="white")

        for model_id, vram, display, note in MODEL_CATALOG["local"]:
            local_table.add_row(model_id, f"{vram}GB", note)

        console.print(Panel(local_table, title="[bold green]🏠  Local Models (Ollama)[/bold green]",
                            border_style="green", expand=False))

        # ── Quick start ───────────────────────────────────────────────────────
        console.print("\n[bold]Quick start:[/bold]")
        console.print("  [dim]# BYOK (anthropic)[/dim]")
        console.print("  export ANTHROPIC_API_KEY=your-key")
        console.print("  aicoder fix 'add rate limiting' --model anthropic")
        console.print()
        console.print("  [dim]# Local (Ollama)[/dim]")
        console.print("  ollama pull qwen2.5-coder:7b")
        console.print("  aicoder fix 'add rate limiting' --model ollama")
        console.print()
        console.print("  [dim]# Set a specific model in .aicoder.yml[/dim]")
        console.print("  echo 'provider: anthropic\\nmodel: claude-3-5-sonnet-20241022' > .aicoder.yml")
        console.print()

    except ImportError:
        # Fallback plain text
        from aicoder.config import MODEL_CATALOG
        print("\n=== Cloud / BYOK Models ===")
        for model_id, provider, env_var, display, note in MODEL_CATALOG["byok"]:
            print(f"  {model_id:<40} [{provider}]  {note}")
        print("\n=== Local Models (Ollama) ===")
        for model_id, vram, display, note in MODEL_CATALOG["local"]:
            print(f"  {model_id:<35} {vram}GB VRAM  {note}")


def suggest_model() -> None:
    """Print a short hint when an unknown provider is used."""
    try:
        from rich.console import Console
        Console().print(
            "[dim]Run [bold]aicoder models[/bold] to see all supported providers and recommended models.[/dim]"
        )
    except ImportError:
        print("Run 'aicoder models' to see supported providers and recommended models.")
