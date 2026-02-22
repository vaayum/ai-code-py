"""File system tools exposed to the LLM agent."""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool


class FileTools:
    """Stateful file tools bound to a project root directory."""

    def __init__(self, project_root: Path, dry_run: bool = False) -> None:
        self.root = project_root.resolve()
        self.dry_run = dry_run

    def get_tools(self) -> list:
        """Return LangChain tool objects bound to this instance."""
        root = self.root
        dry_run = self.dry_run

        @tool
        def read_file(path: str) -> str:
            """Read a file and return its contents with line numbers."""
            target = _resolve(root, path)
            if not target.exists():
                return f"❌ File not found: {path}"
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
            target = _resolve(root, path)
            if target.exists():
                return f"❌ File already exists: {path}. Use update_file to modify it."
            if dry_run:
                return f"[DRY RUN] Would create: {path} ({len(content)} chars)"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            return f"✅ Created: {path}"

        @tool
        def update_file(path: str, old_content: str, new_content: str) -> str:
            """Replace old_content with new_content in a file (exact match required)."""
            target = _resolve(root, path)
            if not target.exists():
                return f"❌ File not found: {path}"
            current = target.read_text()
            if old_content not in current:
                return (
                    f"❌ Exact match not found in {path}.\n"
                    "Read the file first, then use the exact text you see."
                )
            if dry_run:
                return f"[DRY RUN] Would update {path}: replace {len(old_content)} chars"
            target.write_text(current.replace(old_content, new_content, 1))
            return f"✅ Updated: {path}"

        @tool
        def delete_file(path: str) -> str:
            """Delete a file (use with caution)."""
            target = _resolve(root, path)
            if not target.exists():
                return f"❌ File not found: {path}"
            if dry_run:
                return f"[DRY RUN] Would delete: {path}"
            target.unlink()
            return f"✅ Deleted: {path}"

        return [read_file, list_files, create_file, update_file, delete_file]


def _resolve(root: Path, path: str) -> Path:
    """Safely resolve a path relative to root (prevents path traversal)."""
    target = (root / path).resolve()
    # Security: don't allow escaping the project root
    if not str(target).startswith(str(root)):
        raise ValueError(f"Path traversal detected: {path}")
    return target
