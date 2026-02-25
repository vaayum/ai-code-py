"""File system tools exposed to the LLM agent.

Supports three write modes:
- Normal: writes immediately
- Dry-run (--dry-run): shows what would happen, no writes
- Interactive (-i / --interactive): shows a Rich colored diff and asks
  [y]es / [n]o / [e]dit / [a]ll / [q]uit before every write
"""
from __future__ import annotations

import difflib
import time
from collections import deque
from pathlib import Path
from typing import Literal

from langchain_core.tools import tool
from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

from aicoder.memory import AgentMemory
from aicoder.ingestor import CodebaseIngestor

_console = Console()


# ── write-mode state ──────────────────────────────────────────────────────────

class _ApprovalState:
    """Shared mutable state: tracks whether the user said 'accept all'."""
    accept_all: bool = False


# ── Pretty diff ────────────────────────────────────────────────────────────────

def _render_diff(path: str, old: str, new: str) -> None:
    """Print a unified diff with Rich colors."""
    diff = list(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    ))
    if not diff:
        _console.print("[dim](no textual change)[/dim]")
        return

    for line in diff:
        line = line.rstrip("\n")
        if line.startswith("+++") or line.startswith("---"):
            _console.print(f"[bold]{line}[/bold]")
        elif line.startswith("@@"):
            _console.print(f"[cyan]{line}[/cyan]")
        elif line.startswith("+"):
            _console.print(f"[green]{line}[/green]")
        elif line.startswith("-"):
            _console.print(f"[red]{line}[/red]")
        else:
            _console.print(f"[dim]{line}[/dim]")


def _ask_approve(
    action: str,
    path: str,
    old: str,
    new: str,
    state: _ApprovalState,
) -> Literal["yes", "no", "quit"]:
    """Show a diff and ask the user to approve. Returns 'yes', 'no', or 'quit'."""
    if state.accept_all:
        return "yes"

    _console.print()
    _console.rule(f"[bold yellow]⚡ Agent wants to {action}: {path}[/bold yellow]")
    _render_diff(path, old, new)
    _console.print()

    while True:
        choice = _console.input(
            "[bold]Apply change?[/bold] "
            "\\[[green]y[/green]]es  "
            "\\[[red]n[/red]]o  "
            "\\[[cyan]a[/cyan]]ll  "
            "\\[[red]q[/red]]uit  > "
        ).strip().lower()

        if choice in ("y", "yes"):
            return "yes"
        elif choice in ("n", "no"):
            return "no"
        elif choice in ("a", "all"):
            state.accept_all = True
            return "yes"
        elif choice in ("q", "quit"):
            return "quit"
        else:
            _console.print("[dim]Enter y, n, a, or q[/dim]")


# ── FileTools ─────────────────────────────────────────────────────────────────

class FileTools:
    """Stateful file tools bound to a project root directory."""

    def __init__(
        self,
        project_root: Path,
        dry_run: bool = False,
        interactive: bool = False,
        max_reads_per_minute: int = 50,
        max_writes_per_minute: int = 10,
        memory: AgentMemory | None = None,
        ingestor: CodebaseIngestor | None = None,
    ) -> None:
        self.root = project_root.resolve()
        self.dry_run = dry_run
        self.interactive = interactive
        self._state = _ApprovalState()
        self.memory = memory
        self.ingestor = ingestor
        
        # Rate limiting configuration
        self.max_reads_per_minute = max_reads_per_minute
        self.max_writes_per_minute = max_writes_per_minute
        
        # Rate limiting state
        self._read_timestamps: deque[float] = deque(maxlen=max_reads_per_minute)
        self._write_timestamps: deque[float] = deque(maxlen=max_writes_per_minute)

    def _check_rate_limit(self, timestamps: deque[float], limit: int, operation: str) -> str | None:
        """Check if rate limit is exceeded for given operation.
        
        Returns error message if limit exceeded, None otherwise.
        """
        now = time.time()
        window_start = now - 60  # 60 seconds ago
        
        # Remove timestamps older than 60 seconds
        while timestamps and timestamps[0] < window_start:
            timestamps.popleft()
        
        if len(timestamps) >= limit:
            # Calculate wait time
            oldest = timestamps[0]
            wait_seconds = int(60 - (now - oldest)) + 1  # +1 for safety
            return f"❌ Rate limit exceeded: max {limit} {operation} per minute. Wait {wait_seconds} seconds."
        
        # Add current timestamp
        timestamps.append(now)
        return None

    def reset_rate_limits(self) -> None:
        """Reset all rate limit counters for testing purposes."""
        self._read_timestamps.clear()
        self._write_timestamps.clear()

    def get_tools(self) -> list:
        """Return LangChain tool objects bound to this instance."""
        root = self.root
        dry_run = self.dry_run
        interactive = self.interactive
        state = self._state

        @tool
        def read_file(path: str) -> str:
            """Read a file and return its contents with line numbers."""
            # Check read rate limit
            error = self._check_rate_limit(self._read_timestamps, self.max_reads_per_minute, "reads")
            if error:
                return error
            
            target = _resolve(root, path)
            if not target.exists():
                return f"❌ File not found: {path}"
            
            if self.memory:
                try:
                    rel_path = str(target.relative_to(root))
                    self.memory.record_file_read(rel_path)
                    self.memory.save()
                except ValueError:
                    pass

            lines = target.read_text(errors="replace").splitlines()
            return "\n".join(f"{i+1:4}: {line}" for i, line in enumerate(lines))

        @tool
        def list_files(directory: str = ".") -> str:
            """List files in a directory (relative to project root)."""
            target = _resolve(root, directory)
            if not target.is_dir():
                return f"❌ Not a directory: {directory}"
            entries = sorted(target.iterdir())
            lines = []
            for e in entries:
                rel = e.relative_to(root)
                indicator = "/" if e.is_dir() else ""
                size = f"  ({e.stat().st_size:,}B)" if e.is_file() else ""
                lines.append(f"  {rel}{indicator}{size}")
            return "\n".join(lines) or "(empty directory)"

        @tool
        def create_file(path: str, content: str) -> str:
            """Create a new file with the given content."""
            # Check write rate limit
            error = self._check_rate_limit(self._write_timestamps, self.max_writes_per_minute, "writes")
            if error:
                return error
            
            target = _resolve(root, path)
            if target.exists():
                return f"❌ File already exists: {path}. Use update_file to modify it."

            if dry_run:
                _console.print(f"\n[dim]📋 DRY RUN — new file: {path}[/dim]")
                _render_diff(path, "", content)
                return f"[DRY RUN] Would create: {path} ({len(content)} chars)"

            if interactive:
                decision = _ask_approve("CREATE", path, "", content, state)
                if decision == "no":
                    return f"⏭️  Skipped (user rejected): {path}"
                if decision == "quit":
                    raise SystemExit("User quit the session.")

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            
            if self.ingestor:
                try:
                    self.ingestor.ingest_file(target)
                except Exception as e:
                    _console.print(f"[yellow]⚠️ Could not re-index {path}: {e}[/yellow]")
                    
            return f"✅ Created: {path}"

        @tool
        def update_file(path: str, old_content: str, new_content: str) -> str:
            """Replace old_content with new_content in a file (exact match required)."""
            # Check write rate limit
            error = self._check_rate_limit(self._write_timestamps, self.max_writes_per_minute, "writes")
            if error:
                return error
            
            target = _resolve(root, path)
            if not target.exists():
                return f"❌ File not found: {path}"
            current = target.read_text()
            if old_content not in current:
                return (
                    f"❌ Exact match not found in {path}.\n"
                    "Read the file first, then use the exact text you see."
                )
            updated = current.replace(old_content, new_content, 1)

            if dry_run:
                _console.print(f"\n[dim]📋 DRY RUN — update: {path}[/dim]")
                _render_diff(path, current, updated)
                return f"[DRY RUN] Would update {path}: replace {len(old_content)} chars"

            if interactive:
                decision = _ask_approve("UPDATE", path, current, updated, state)
                if decision == "no":
                    return f"⏭️  Skipped (user rejected): {path}"
                if decision == "quit":
                    raise SystemExit("User quit the session.")

            target.write_text(updated)
            
            if self.ingestor:
                try:
                    self.ingestor.ingest_file(target)
                except Exception as e:
                    _console.print(f"[yellow]⚠️ Could not re-index {path}: {e}[/yellow]")
                    
            return f"✅ Updated: {path}"

        @tool
        def delete_file(path: str) -> str:
            """Delete a file (use with caution)."""
            # Check write rate limit
            error = self._check_rate_limit(self._write_timestamps, self.max_writes_per_minute, "writes")
            if error:
                return error
            
            target = _resolve(root, path)
            if not target.exists():
                return f"❌ File not found: {path}"
            current = target.read_text(errors="replace") if target.is_file() else ""

            if dry_run:
                _console.print(f"\n[dim]📋 DRY RUN — delete: {path}[/dim]")
                _render_diff(path, current, "")
                return f"[DRY RUN] Would delete: {path}"

            if interactive:
                decision = _ask_approve("DELETE", path, current, "", state)
                if decision == "no":
                    return f"⏭️  Skipped (user rejected): {path}"
                if decision == "quit":
                    raise SystemExit("User quit the session.")

            target.unlink()
            
            if self.ingestor:
                try:
                    self.ingestor.remove_file(target)
                except Exception as e:
                    pass
                    
            return f"✅ Deleted: {path}"

        return [read_file, list_files, create_file, update_file, delete_file]


def _resolve(root: Path, path: str) -> Path:
    """Safely resolve a path relative to root (prevents path traversal)."""
    target = (root / path).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError(f"Path traversal detected: {path}")
    return target
