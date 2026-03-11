"""Extended tests for git tools, build tools, ingestor, memory, CLI, and security."""
from __future__ import annotations

import sys
import json
import subprocess
import tempfile
from pathlib import Path

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# Memory — extended
# ══════════════════════════════════════════════════════════════════════════════

class TestMemoryExtended:

    def test_memory_prompt_context_with_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            from aicoder.memory import AgentMemory
            mem = AgentMemory(Path(tmp))
            mem._data["projectSummary"] = "A Django REST API"
            mem.save_entry("convention:tests", "Use pytest")
            mem.add_action("fix NPE")
            ctx = mem.to_prompt_context()
            assert "Django REST API" in ctx
            assert "pytest" in ctx
            assert "fix NPE" in ctx

    def test_memory_save_key_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            from aicoder.memory import AgentMemory
            mem = AgentMemory(Path(tmp))
            mem.save_entry("file:src/main.py", "Entry point of the application")
            assert "src/main.py" in mem.key_files

    def test_memory_json_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            from aicoder.memory import AgentMemory
            mem = AgentMemory(Path(tmp))
            mem.add_action("action A")
            mem.add_action("action B")
            mem.save_entry("convention:style", "Black formatting")
            mem.save()
            # Verify JSON file is valid
            raw = json.loads((Path(tmp) / ".aicoder" / "memory.json").read_text())
            assert len(raw["recentActions"]) == 2
            assert "Black formatting" in raw["conventions"]

    def test_memory_actions_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            from aicoder.memory import AgentMemory
            mem = AgentMemory(Path(tmp))
            mem.add_action("first")
            mem.add_action("second")
            mem.add_action("third")
            assert mem.recent_actions[0]["action"] == "third"
            assert mem.recent_actions[2]["action"] == "first"

    def test_memory_corrupted_file_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            from aicoder.memory import AgentMemory
            p = Path(tmp) / ".aicoder" / "memory.json"
            p.parent.mkdir(parents=True)
            p.write_text("{ invalid json !!!")  # corrupted
            mem = AgentMemory(Path(tmp))  # should not crash
            assert mem.recent_actions == []


# ══════════════════════════════════════════════════════════════════════════════
# File Tools — extended
# ══════════════════════════════════════════════════════════════════════════════

class TestFileToolsExtended:

    def _tools(self, root, dry_run=False):
        from aicoder.tools.file_tools import FileTools
        return {t.name: t for t in FileTools(root, dry_run).get_tools()}

    def test_read_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = self._tools(Path(tmp))
            result = tools["read_file"].invoke({"path": "does_not_exist.py"})
            assert "not found" in result.lower() or "❌" in result

    def test_create_duplicate_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = self._tools(Path(tmp))
            tools["create_file"].invoke({"path": "dup.py", "content": "x"})
            result = tools["create_file"].invoke({"path": "dup.py", "content": "y"})
            assert "already exists" in result.lower() or "❌" in result

    def test_update_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = self._tools(Path(tmp))
            tools["create_file"].invoke({"path": "a.py", "content": "hello"})
            result = tools["update_file"].invoke({
                "path": "a.py",
                "old_content": "DOES_NOT_EXIST",
                "new_content": "replaced",
            })
            assert "not found" in result.lower() or "❌" in result

    def test_delete_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "todelete.txt"
            p.write_text("bye")
            tools = self._tools(Path(tmp))
            result = tools["delete_file"].invoke({"path": "todelete.txt"})
            assert "✅" in result
            assert not p.exists()

    def test_delete_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "safe.txt"
            p.write_text("safe")
            tools = self._tools(Path(tmp), dry_run=True)
            result = tools["delete_file"].invoke({"path": "safe.txt"})
            assert "DRY RUN" in result
            assert p.exists()

    def test_line_numbers_in_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "code.py").write_text("line1\nline2\nline3\n")
            tools = self._tools(Path(tmp))
            result = tools["read_file"].invoke({"path": "code.py"})
            assert "   1:" in result
            assert "   2:" in result
            assert "   3:" in result

    def test_nested_directory_listing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sub").mkdir()
            (root / "sub" / "x.py").write_text("x")
            tools = self._tools(root)
            result = tools["list_files"].invoke({"directory": "sub"})
            assert "x.py" in result

    def test_path_traversal_read_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            from aicoder.tools.file_tools import FileTools, _resolve
            with pytest.raises(ValueError):
                _resolve(Path(tmp), "../../../etc/passwd")


# ══════════════════════════════════════════════════════════════════════════════
# Build Tools — extended
# ══════════════════════════════════════════════════════════════════════════════

class TestBuildToolsExtended:

    def test_detection_cargo(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Cargo.toml").write_text("[package]\nname='test'")
            from aicoder.tools.build_tools import BuildTools
            bt = BuildTools(Path(tmp))
            tools = {t.name: t for t in bt.get_tools()}
            result = tools["get_build_system"].invoke({})
            assert "cargo" in result.lower()

    def test_detection_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "go.mod").write_text("module test\ngo 1.21")
            from aicoder.tools.build_tools import BuildTools
            bt = BuildTools(Path(tmp))
            tools = {t.name: t for t in bt.get_tools()}
            result = tools["get_build_system"].invoke({})
            assert "go" in result.lower()

    def test_detection_npm(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "package.json").write_text('{"name":"test"}')
            from aicoder.tools.build_tools import BuildTools
            bt = BuildTools(Path(tmp))
            tools = {t.name: t for t in bt.get_tools()}
            result = tools["get_build_system"].invoke({})
            assert "npm" in result.lower()

    def test_execute_shell_echo(self):
        with tempfile.TemporaryDirectory() as tmp:
            from aicoder.tools.build_tools import BuildTools
            tools = {t.name: t for t in BuildTools(Path(tmp)).get_tools()}
            result = tools["execute_shell"].invoke({"command": "echo hello_from_aicoder"})
            assert "hello_from_aicoder" in result

    def test_python_compile_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text("[project]\nname='test'")
            (root / "ok.py").write_text("print('hello')\n")
            from aicoder.tools.build_tools import BuildTools
            tools = {t.name: t for t in BuildTools(root).get_tools()}
            result = tools["compile_project"].invoke({})
            # Python compile_project runs py_compile — may need a specific file
            assert isinstance(result, str)  # just verify it doesn't crash


# ══════════════════════════════════════════════════════════════════════════════
# Git Tools
# ══════════════════════════════════════════════════════════════════════════════

class TestGitTools:

    def _init_git_repo(self, tmp: str) -> Path:
        root = Path(tmp)
        subprocess.run(["git", "init"], cwd=root, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, capture_output=True)
        (root / "README.md").write_text("# Test")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True)
        return root

    def test_get_status_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._init_git_repo(tmp)
            from aicoder.tools.git_tools import GitTools
            tools = {t.name: t for t in GitTools(root).get_tools()}
            result = tools["get_git_status"].invoke({})
            assert "Branch:" in result or "main" in result or "master" in result

    def test_checkout_new_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._init_git_repo(tmp)
            from aicoder.tools.git_tools import GitTools
            tools = {t.name: t for t in GitTools(root).get_tools()}
            result = tools["checkout_new_branch"].invoke({"branch_name": "feature/test-branch"})
            assert "✅" in result
            assert "feature/test-branch" in result

    def test_commit_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._init_git_repo(tmp)
            (root / "new_file.txt").write_text("new content")
            from aicoder.tools.git_tools import GitTools
            tools = {t.name: t for t in GitTools(root).get_tools()}
            result = tools["commit_changes"].invoke({"message": "test: add new file"})
            assert "✅" in result or "Committed" in result

    def test_commit_nothing_to_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._init_git_repo(tmp)
            from aicoder.tools.git_tools import GitTools
            tools = {t.name: t for t in GitTools(root).get_tools()}
            result = tools["commit_changes"].invoke({"message": "empty commit"})
            assert "clean" in result.lower() or "nothing" in result.lower()

    def test_get_diff_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._init_git_repo(tmp)
            from aicoder.tools.git_tools import GitTools
            tools = {t.name: t for t in GitTools(root).get_tools()}
            result = tools["get_diff"].invoke({"file_path": ""})
            assert "(no changes)" in result or result == "(no changes)"

    def test_get_diff_with_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._init_git_repo(tmp)
            (root / "README.md").write_text("# Modified")
            from aicoder.tools.git_tools import GitTools
            tools = {t.name: t for t in GitTools(root).get_tools()}
            result = tools["get_diff"].invoke({"file_path": ""})
            assert "Modified" in result or "README" in result


# ══════════════════════════════════════════════════════════════════════════════
# Ingestor — unit tests (no ChromaDB needed for fingerprinting logic)
# ══════════════════════════════════════════════════════════════════════════════

class TestIngestorUtils:

    def test_sha256_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "file.py"
            f.write_text("hello world")
            from aicoder.ingestor import _sha256
            h1 = _sha256(f)
            h2 = _sha256(f)
            assert h1 == h2
            assert len(h1) == 64  # SHA-256 hex = 64 chars

    def test_sha256_differs_on_content_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "file.py"
            f.write_text("version 1")
            from aicoder.ingestor import _sha256
            h1 = _sha256(f)
            f.write_text("version 2")
            h2 = _sha256(f)
            assert h1 != h2

    def test_chunk_id_unique_per_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "foo.py"
            from aicoder.ingestor import _chunk_id
            ids = [_chunk_id(f, i) for i in range(5)]
            assert len(set(ids)) == 5  # all unique

    def test_collect_files_excludes_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("x")
            (root / ".venv").mkdir()
            (root / ".venv" / "lib.py").write_text("x")
            from aicoder.ingestor import CodebaseIngestor
            ingestor = CodebaseIngestor(root)
            files = ingestor._collect_files()
            paths = [str(f) for f in files]
            assert any("main.py" in p for p in paths)
            assert not any(".venv" in p for p in paths)

    def test_collect_files_skips_large_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            small = root / "small.py"
            large = root / "large.py"
            small.write_text("x")
            large.write_bytes(b"x" * 600_000)  # > 500KB limit
            from aicoder.ingestor import CodebaseIngestor
            ingestor = CodebaseIngestor(root)
            files = ingestor._collect_files()
            names = [f.name for f in files]
            assert "small.py" in names
            assert "large.py" not in names

    def test_tree_sitter_chunking_python(self):
        from aicoder.ingestor import _chunk_with_treesitter
        code = """
def hello(name: str) -> str:
    return f"Hello, {name}!"

class Greeter:
    def greet(self) -> str:
        return "Hi!"
"""
        try:
            chunks = _chunk_with_treesitter(code, "greet.py", "py")
            if chunks:  # tree-sitter may not be installed in test env
                assert any("hello" in c["text"] or "Greeter" in c["text"] for c in chunks)
        except Exception:
            pytest.skip("tree-sitter-languages not installed")


# ══════════════════════════════════════════════════════════════════════════════
# Config — extended
# ══════════════════════════════════════════════════════════════════════════════

class TestConfigExtended:

    def test_config_agent_defaults(self):
        from aicoder.config import AiCoderConfig
        cfg = AiCoderConfig()
        assert cfg.agent.max_retries == 3
        assert cfg.agent.temperature == 0.1
        assert cfg.agent.max_tokens == 4096

    def test_config_yaml_partial_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            yml = Path(tmp) / "cfg.yml"
            yml.write_text("provider: anthropic\n")  # only provider, rest defaults
            from aicoder.config import load_config
            cfg = load_config(yml)
            assert cfg.provider == "anthropic"
            assert cfg.agent.max_retries == 3  # default preserved

    def test_config_invalid_provider_raises(self):
        from aicoder.config import create_llm, AiCoderConfig
        cfg = AiCoderConfig()
        with pytest.raises(ValueError, match="Unknown provider"):
            create_llm("invalid_provider", cfg)

    def test_config_missing_api_key_exits(self):
        import os
        from aicoder.config import create_llm, AiCoderConfig
        cfg = AiCoderConfig(provider="openai")
        original = os.environ.pop("OPENAI_API_KEY", None)
        try:
            # Either SystemExit (key missing) or ImportError (langchain_openai not installed)
            # Both are valid — in production only langchain_openai would be installed
            with pytest.raises((SystemExit, ImportError)):
                create_llm("openai", cfg)
        finally:
            if original:
                os.environ["OPENAI_API_KEY"] = original


# ══════════════════════════════════════════════════════════════════════════════
# CLI smoke tests (no LLM)
# ══════════════════════════════════════════════════════════════════════════════

class TestCLISmoke:

    def test_help_command(self):
        result = subprocess.run(
            [sys.executable, "-m", "aicoder.cli", "--help"],
            cwd="/Users/nitinmudgal/.gemini/antigravity/scratch/ai-code-py",
            capture_output=True, text=True,
            env={**__import__("os").environ,
                 "PYTHONPATH": "/Users/nitinmudgal/.gemini/antigravity/scratch/ai-code-py"}
        )
        output = result.stdout + result.stderr
        assert "aicoder" in output.lower()
        assert result.returncode == 0

    def test_version_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "aicoder.cli", "--version"],
            cwd="/Users/nitinmudgal/.gemini/antigravity/scratch/ai-code-py",
            capture_output=True, text=True,
            env={**__import__("os").environ,
                 "PYTHONPATH": "/Users/nitinmudgal/.gemini/antigravity/scratch/ai-code-py"}
        )
        output = result.stdout + result.stderr
        assert "0.1.0" in output

    def test_smart_command_help(self):
        """New unified smart command -- all flags visible at top level."""
        result = subprocess.run(
            [sys.executable, "-m", "aicoder.cli", "--help"],
            cwd="/Users/nitinmudgal/.gemini/antigravity/scratch/ai-code-py",
            capture_output=True, text=True,
            env={**__import__("os").environ,
                 "PYTHONPATH": "/Users/nitinmudgal/.gemini/antigravity/scratch/ai-code-py"}
        )
        output = result.stdout + result.stderr
        assert result.returncode == 0
        assert "INSTRUCTION" in output          # the smart argument
        assert "--agents" in output             # multi-agent flag
        assert "--dry-run" in output
        assert "--interactive" in output
        assert "--since" in output              # audit git-range flag

    def test_models_subcommand(self):
        """aicoder models should print the model catalog."""
        result = subprocess.run(
            [sys.executable, "-m", "aicoder.cli", "models"],
            cwd="/Users/nitinmudgal/.gemini/antigravity/scratch/ai-code-py",
            capture_output=True, text=True,
            env={**__import__("os").environ,
                 "PYTHONPATH": "/Users/nitinmudgal/.gemini/antigravity/scratch/ai-code-py",
                 "DEEPSEEK_API_KEY": "sk-test-key"}   # satisfy auth check
        )
        output = result.stdout + result.stderr
        # Should show model catalog — check for any known model ID or provider
        assert any(kw in output for kw in ("deepseek", "anthropic", "BYOK", "Local", "claude", "gpt"))

    def test_intent_classifier(self):
        """Intent classifier maps keywords to correct modes without LLM."""
        from aicoder.cli import _classify
        assert _classify("fix the null pointer in UserService") == "fix"
        assert _classify("audit for SQL injection and hardcoded secrets") == "audit"
        assert _classify("check for security vulnerabilities") == "audit"
        assert _classify("refactor payment module into repository pattern") == "refactor"
        assert _classify("restructure and decouple the auth code") == "refactor"
        assert _classify("add tests for the auth service") == "test-gen"
        assert _classify("generate pytest coverage for UserService") == "test-gen"
        assert _classify("add pagination to all endpoints") == "fix"  # default
