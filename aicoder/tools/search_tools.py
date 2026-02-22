"""Semantic codebase search using ChromaDB + sentence-transformers."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from langchain_core.tools import tool

if TYPE_CHECKING:
    from aicoder.ingestor import CodebaseIngestor


class SearchTools:
    """Semantic search tools backed by the CodebaseIngestor."""

    def __init__(self, ingestor: "CodebaseIngestor") -> None:
        self._ingestor = ingestor

    def get_tools(self) -> list:
        ingestor = self._ingestor

        @tool
        def search_codebase(query: str, max_results: int = 5) -> str:
            """Search the codebase using natural language. Returns relevant code snippets."""
            return ingestor.search(query, max_results)

        return [search_codebase]
