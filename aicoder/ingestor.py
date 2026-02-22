"""Codebase ingestion — ChromaDB vector store with incremental indexing."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

EXCLUDED_DIRS = {".git", "target", "build", ".idea", ".vscode",
                 "node_modules", ".gradle", "__pycache__", ".venv", "venv", "dist"}
SUPPORTED_EXTS = {".py", ".java", ".ts", ".js", ".go", ".rs", ".kt",
                  ".xml", ".yaml", ".yml", ".json", ".md", ".txt", ".toml"}
MAX_FILE_SIZE = 500_000  # 500 KB


class CodebaseIngestor:
    """
    Indexes a codebase into ChromaDB for semantic search.

    Key features vs Java edition:
    - Uses sentence-transformers (no ONNX drama, pure Python)
    - ChromaDB persists to .aicoder/embeddings/ (no re-index on restart)
    - SHA-256 fingerprinting skips unchanged files
    - tree-sitter for multi-language AST chunking (Python, JS, Go, Rust, Java, TS)
    """

    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()
        self._cache_dir = self.root / ".aicoder" / "embeddings"
        self._fp_file = self.root / ".aicoder" / "fingerprints.json"
        self._collection = None
        self._fingerprints: dict[str, str] = {}
        self._total_chunks = 0

    # ── Public API ────────────────────────────────────────────────────────

    def ingest(self, quiet: bool = False) -> int:
        """Index new/changed files. Returns number of new chunks added."""
        import json
        import chromadb
        from chromadb.utils import embedding_functions

        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # Load fingerprints
        if self._fp_file.exists():
            self._fingerprints = json.loads(self._fp_file.read_text())

        # ChromaDB with sentence-transformers embedding
        client = chromadb.PersistentClient(path=str(self._cache_dir))
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        self._collection = client.get_or_create_collection(
            name="codebase", embedding_function=ef
        )

        # Collect and filter dirty files
        all_files = self._collect_files()
        dirty, skipped = [], 0
        for f in all_files:
            rel = str(f.relative_to(self.root))
            sha = _sha256(f)
            if sha == self._fingerprints.get(rel):
                skipped += 1
            else:
                dirty.append(f)
                self._fingerprints[rel] = sha

        if not quiet:
            print(f"   📂 {len(all_files)} files | {skipped} unchanged | {len(dirty)} to index")

        # Index dirty files
        new_chunks = 0
        for f in dirty:
            chunks = self._chunk_file(f)
            if not chunks:
                continue
            ids = [_chunk_id(f, i) for i in range(len(chunks))]
            texts = [c["text"] for c in chunks]
            metas = [c["meta"] for c in chunks]
            # Upsert so re-indexing a changed file replaces old chunks
            self._collection.upsert(documents=texts, metadatas=metas, ids=ids)
            new_chunks += len(chunks)

        self._total_chunks = self._collection.count()

        # Persist fingerprints
        self._fp_file.write_text(json.dumps(self._fingerprints, indent=2))
        return new_chunks

    def search(self, query: str, max_results: int = 5) -> str:
        """Semantic search — returns formatted snippets with file + score."""
        if self._collection is None:
            return "⚠️  Index not built yet. Call ingest() first."
        results = self._collection.query(
            query_texts=[query],
            n_results=min(max_results, self._total_chunks or 1),
        )
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        if not docs:
            return f"No results for: {query}"

        parts = [f"Found {len(docs)} result(s) for: **{query}**\n"]
        for doc, meta, dist in zip(docs, metas, distances):
            score = 1 - dist  # ChromaDB returns L2 distance
            parts.append(
                f"--- {meta.get('file', '?')} | {meta.get('type', '?')} | score: {score:.2f} ---\n"
                f"{doc[:500]}\n"
            )
        return "\n".join(parts)

    @property
    def total_chunks(self) -> int:
        return self._total_chunks

    # ── Private ───────────────────────────────────────────────────────────

    def _collect_files(self) -> list[Path]:
        files = []
        for f in self.root.rglob("*"):
            if f.is_file() and f.suffix in SUPPORTED_EXTS:
                if any(ex in f.parts for ex in EXCLUDED_DIRS):
                    continue
                if f.stat().st_size > MAX_FILE_SIZE:
                    continue
                files.append(f)
        return files

    def _chunk_file(self, f: Path) -> list[dict]:
        try:
            content = f.read_text(errors="replace")
        except Exception:
            return []
        rel = str(f.relative_to(self.root))
        ext = f.suffix.lstrip(".")

        # Try tree-sitter AST chunking for supported languages
        if ext in ("py", "js", "ts", "go", "rs", "java"):
            chunks = _chunk_with_treesitter(content, rel, ext)
            if chunks:
                return chunks

        # Fallback: whole file as one chunk (truncated)
        return [{"text": content[:4000], "meta": {"file": rel, "type": "file"}}]


# ── Tree-sitter chunking ──────────────────────────────────────────────────────

def _chunk_with_treesitter(content: str, rel_path: str, ext: str) -> list[dict]:
    """Chunk source code by top-level definitions using tree-sitter."""
    try:
        from tree_sitter_languages import get_language, get_parser

        lang_map = {
            "py": "python", "js": "javascript", "ts": "typescript",
            "go": "go", "rs": "rust", "java": "java",
        }
        lang_name = lang_map.get(ext)
        if not lang_name:
            return []

        lang = get_language(lang_name)
        parser = get_parser(lang_name)
        tree = parser.parse(content.encode())

        # Node types that represent top-level definitions
        target_types = {
            "python": {"function_definition", "class_definition"},
            "javascript": {"function_declaration", "class_declaration", "arrow_function"},
            "typescript": {"function_declaration", "class_declaration", "method_definition"},
            "go": {"function_declaration", "method_declaration", "type_declaration"},
            "rust": {"function_item", "impl_item", "struct_item", "enum_item"},
            "java": {"class_declaration", "method_declaration", "interface_declaration"},
        }.get(lang_name, set())

        chunks = []
        for node in tree.root_node.children:
            if node.type in target_types:
                text = content[node.start_byte:node.end_byte]
                name = _extract_name(node, content)
                chunks.append({
                    "text": f"// {rel_path} — {node.type}: {name}\n{text[:2000]}",
                    "meta": {"file": rel_path, "type": node.type, "name": name},
                })

        return chunks
    except Exception:
        return []


def _extract_name(node, content: str) -> str:
    for child in node.children:
        if child.type in ("identifier", "name"):
            return content[child.start_byte:child.end_byte]
    return "unknown"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _chunk_id(f: Path, idx: int) -> str:
    return hashlib.md5(f"{f}:{idx}".encode()).hexdigest()
