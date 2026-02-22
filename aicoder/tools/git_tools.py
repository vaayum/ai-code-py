"""Git tools — branch, commit, diff, status."""
from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

try:
    import git
    HAS_GIT = True
except ImportError:
    HAS_GIT = False


class GitTools:
    """Git operations via GitPython."""

    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()

    def get_tools(self) -> list:
        root = self.root

        @tool
        def get_git_status() -> str:
            """Show current git status (branch, staged/unstaged changes)."""
            if not HAS_GIT:
                return "GitPython not installed"
            try:
                repo = git.Repo(root)
                branch = repo.active_branch.name
                status = repo.git.status("--short")
                return f"Branch: {branch}\n{status or '(clean)'}"
            except Exception as e:
                return f"Git error: {e}"

        @tool
        def checkout_new_branch(branch_name: str) -> str:
            """Create and switch to a new git branch."""
            if not HAS_GIT:
                return "GitPython not installed"
            try:
                repo = git.Repo(root)
                new = repo.create_head(branch_name)
                new.checkout()
                return f"✅ Switched to new branch: {branch_name}"
            except Exception as e:
                return f"❌ Git error: {e}"

        @tool
        def commit_changes(message: str) -> str:
            """Stage all changes and commit with the given message."""
            if not HAS_GIT:
                return "GitPython not installed"
            try:
                repo = git.Repo(root)
                repo.git.add("-A")
                if not repo.index.diff("HEAD") and not repo.untracked_files:
                    return "Nothing to commit (working tree clean)"
                repo.index.commit(message)
                return f"✅ Committed: {message}"
            except Exception as e:
                return f"❌ Git error: {e}"

        @tool
        def get_diff(file_path: str = "") -> str:
            """Get the git diff for a specific file or the whole repo."""
            if not HAS_GIT:
                return "GitPython not installed"
            try:
                repo = git.Repo(root)
                diff = repo.git.diff("HEAD", file_path) if file_path else repo.git.diff("HEAD")
                return diff[:4000] if diff else "(no changes)"
            except Exception as e:
                return f"❌ Git error: {e}"

        @tool
        def get_diff_since(ref: str) -> str:
            """Get a summary of all changes since a branch or commit (e.g. 'main')."""
            if not HAS_GIT:
                return "GitPython not installed"
            try:
                repo = git.Repo(root)
                summary = repo.git.diff("--stat", ref)
                return summary[:4000] if summary else "(no changes)"
            except Exception as e:
                return f"❌ Git error: {e}"

        return [get_git_status, checkout_new_branch, commit_changes, get_diff, get_diff_since]
