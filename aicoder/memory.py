"""Agent memory — persisted to .aicoder/memory.json between sessions."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class AgentMemory:
    """
    Cross-session memory that persists to ``.aicoder/memory.json``.

    Stores:
    - ``recent_actions``   – last N user requests with timestamps
    - ``key_files``        – files the agent has flagged as important (path → description)
    - ``file_read_counts`` – how many times each file has been read this project (auto key-file)
    - ``conventions``      – project conventions discovered by the agent
    - ``project_summary``  – free-form project description (auto-generated or agent-written)
    """

    MAX_ACTIONS      = 20
    KEY_FILE_THRESHOLD = 3   # reads before a file is auto-promoted to key_files

    def __init__(self, project_root: Path) -> None:
        self.root  = project_root
        self._path = project_root / ".aicoder" / "memory.json"
        self._data: dict[str, Any] = self._load()

    # ── Public properties ──────────────────────────────────────────────────

    @property
    def recent_actions(self) -> list[dict]:
        return self._data.setdefault("recentActions", [])

    @property
    def key_files(self) -> dict[str, str]:
        return self._data.setdefault("keyFiles", {})

    @property
    def conventions(self) -> list[str]:
        return self._data.setdefault("conventions", [])

    @property
    def project_summary(self) -> str:
        return self._data.get("projectSummary", "")

    @property
    def file_read_counts(self) -> dict[str, int]:
        return self._data.setdefault("fileReadCounts", {})

    # ── Write API ──────────────────────────────────────────────────────────

    def add_action(self, action: str) -> None:
        """Record a user action. Keeps last MAX_ACTIONS entries, newest first."""
        self.recent_actions.insert(0, {
            "timestamp": datetime.now().isoformat(),
            "action": action,
        })
        self._data["recentActions"] = self.recent_actions[: self.MAX_ACTIONS]

    def save_entry(self, key: str, value: str) -> None:
        """
        Save an arbitrary key-value entry.

        Key prefixes:
          file:path       → saved to keyFiles
          convention:name → appended to conventions list (deduped)
          project:summary → saved as projectSummary
          anything else   → saved directly in _data
        """
        if key.startswith("file:"):
            self.key_files[key[5:]] = value
        elif key.startswith("convention:"):
            if value not in self.conventions:
                self.conventions.append(value)
        elif key.startswith("project:"):
            sub = key[8:]   # e.g. "summary", "name"
            field = "projectSummary" if sub == "summary" else sub
            self._data[field] = value
        else:
            self._data[key] = value

    def record_file_read(self, path: str) -> None:
        """
        Track how often a file is read.
        Auto-promotes frequently-read files to keyFiles after KEY_FILE_THRESHOLD reads.
        """
        counts = self.file_read_counts
        counts[path] = counts.get(path, 0) + 1
        if (
            counts[path] >= self.KEY_FILE_THRESHOLD
            and path not in self.key_files
        ):
            self.key_files[path] = f"Frequently accessed ({counts[path]} reads)"

    def set_project_summary(self, summary: str) -> None:
        """Set the project summary (called after first index or by the agent)."""
        self._data["projectSummary"] = summary

    def save(self) -> None:
        """Persist memory to disk."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))

    # ── Prompt context ─────────────────────────────────────────────────────

    def to_prompt_context(self) -> str:
        """Render memory as a compact context block injected into every agent prompt."""
        parts: list[str] = []

        if self.project_summary:
            parts.append(f"**Project:** {self.project_summary}")

        if self.conventions:
            conv_lines = "\n".join(f"  - {c}" for c in self.conventions[:8])
            parts.append(f"**Conventions:**\n{conv_lines}")

        if self.key_files:
            top = list(self.key_files.items())[:8]
            kf_lines = "\n".join(f"  - `{p}` — {d}" for p, d in top)
            parts.append(f"**Key files:**\n{kf_lines}")

        if self.recent_actions:
            recent = self.recent_actions[:5]
            action_lines = "\n".join(f"  - {a['action']}" for a in recent)
            parts.append(f"**Recent actions:**\n{action_lines}")

        if not parts:
            return ""

        return "## 🧠 Project Memory\n\n" + "\n\n".join(parts)

    def stats(self) -> str:
        """One-line summary for display."""
        n_actions = len(self.recent_actions)
        n_files   = len(self.key_files)
        n_conv    = len(self.conventions)
        summary   = f"  {self.project_summary[:60]}..." if self.project_summary else ""
        return (
            f"💾 {n_actions} action(s) · {n_files} key file(s) · {n_conv} convention(s)"
            + (f"\n   📋 {summary}" if summary else "")
        )

    # ── Private ────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {}
