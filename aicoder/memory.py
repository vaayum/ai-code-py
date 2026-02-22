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
    - ``recent_actions`` – last N user requests
    - ``key_files``       – files the agent has flagged as important
    - ``conventions``     – project conventions discovered by the agent
    - ``project_summary`` – free-form project description
    """

    MAX_ACTIONS = 20

    def __init__(self, project_root: Path) -> None:
        self.root = project_root
        self._path = project_root / ".aicoder" / "memory.json"
        self._data: dict[str, Any] = self._load()

    # ── Public API ────────────────────────────────────────────────────────

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

    def add_action(self, action: str) -> None:
        self.recent_actions.insert(0, {
            "timestamp": datetime.now().isoformat(),
            "action": action,
        })
        # Keep only the most recent N actions
        self._data["recentActions"] = self.recent_actions[: self.MAX_ACTIONS]

    def save_entry(self, key: str, value: str) -> None:
        """Save an arbitrary key-value entry (conventions, key files, etc.)."""
        if key.startswith("file:"):
            self.key_files[key[5:]] = value
        elif key.startswith("convention:"):
            self.conventions.append(value)
        else:
            self._data[key] = value

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2))

    def to_prompt_context(self) -> str:
        """Render memory as a compact context block for the agent's prompt."""
        parts: list[str] = []
        if self.project_summary:
            parts.append(f"Project: {self.project_summary}")
        if self.conventions:
            parts.append("Conventions:\n" + "\n".join(f"  - {c}" for c in self.conventions[:5]))
        if self.recent_actions:
            recent = self.recent_actions[:5]
            parts.append(
                "Recent actions:\n"
                + "\n".join(f"  - {a['action']}" for a in recent)
            )
        if not parts:
            return ""
        return "## Project Memory\n" + "\n".join(parts)

    # ── Private ───────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {}
