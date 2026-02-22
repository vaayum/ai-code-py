"""Tests for AICoder Python edition."""
from pathlib import Path
import tempfile
import pytest


# ── Memory tests ─────────────────────────────────────────────────────────────

def test_memory_add_and_persist():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        from aicoder.memory import AgentMemory
        mem = AgentMemory(root)
        mem.add_action("fix NPE in UserService")
        mem.save()

        # Reload
        mem2 = AgentMemory(root)
        assert len(mem2.recent_actions) == 1
        assert mem2.recent_actions[0]["action"] == "fix NPE in UserService"


def test_memory_max_actions():
    with tempfile.TemporaryDirectory() as tmp:
        from aicoder.memory import AgentMemory
        mem = AgentMemory(Path(tmp))
        for i in range(25):
            mem.add_action(f"action {i}")
        assert len(mem.recent_actions) == AgentMemory.MAX_ACTIONS


def test_memory_prompt_context_empty():
    with tempfile.TemporaryDirectory() as tmp:
        from aicoder.memory import AgentMemory
        mem = AgentMemory(Path(tmp))
        assert mem.to_prompt_context() == ""


def test_memory_save_entry():
    with tempfile.TemporaryDirectory() as tmp:
        from aicoder.memory import AgentMemory
        mem = AgentMemory(Path(tmp))
        mem.save_entry("convention:tests", "Use pytest with fixtures")
        assert "Use pytest with fixtures" in mem.conventions


# ── File tools tests ──────────────────────────────────────────────────────────

def test_file_tools_read_write():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        from aicoder.tools.file_tools import FileTools
        ft = FileTools(root)
        tools = {t.name: t for t in ft.get_tools()}

        # Create
        result = tools["create_file"].invoke({"path": "hello.txt", "content": "Hello, world!"})
        assert "Created" in result

        # Read
        content = tools["read_file"].invoke({"path": "hello.txt"})
        assert "Hello, world!" in content
        assert "1:" in content  # line numbers present


def test_file_tools_update():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        from aicoder.tools.file_tools import FileTools
        ft = FileTools(root)
        tools = {t.name: t for t in ft.get_tools()}

        tools["create_file"].invoke({"path": "test.py", "content": "def foo():\n    pass\n"})
        result = tools["update_file"].invoke({
            "path": "test.py",
            "old_content": "    pass",
            "new_content": "    return 42",
        })
        assert "Updated" in result
        content = tools["read_file"].invoke({"path": "test.py"})
        assert "return 42" in content


def test_file_tools_path_traversal_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        from aicoder.tools.file_tools import FileTools, _resolve
        root = Path(tmp)
        with pytest.raises(ValueError, match="traversal"):
            _resolve(root, "../../etc/passwd")


def test_file_tools_dry_run():
    with tempfile.TemporaryDirectory() as tmp:
        from aicoder.tools.file_tools import FileTools
        ft = FileTools(Path(tmp), dry_run=True)
        tools = {t.name: t for t in ft.get_tools()}
        result = tools["create_file"].invoke({"path": "dry.txt", "content": "x"})
        assert "DRY RUN" in result
        assert not (Path(tmp) / "dry.txt").exists()


def test_file_tools_list():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.py").write_text("x")
        (root / "b.txt").write_text("y")
        from aicoder.tools.file_tools import FileTools
        ft = FileTools(root)
        tools = {t.name: t for t in ft.get_tools()}
        result = tools["list_files"].invoke({"directory": "."})
        assert "a.py" in result
        assert "b.txt" in result


# ── Build tools tests ─────────────────────────────────────────────────────────

def test_build_system_detection_python():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pyproject.toml").write_text("[project]\nname='test'")
        from aicoder.tools.build_tools import BuildTools
        bt = BuildTools(root)
        tools = {t.name: t for t in bt.get_tools()}
        result = tools["get_build_system"].invoke({})
        assert "python" in result.lower()


def test_build_system_detection_maven():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "pom.xml").write_text("<project/>")
        from aicoder.tools.build_tools import BuildTools
        bt = BuildTools(root)
        tools = {t.name: t for t in bt.get_tools()}
        result = tools["get_build_system"].invoke({})
        assert "maven" in result.lower()


# ── Config tests ──────────────────────────────────────────────────────────────

def test_config_defaults():
    with tempfile.TemporaryDirectory() as tmp:
        from aicoder.config import load_config
        cfg = load_config(None)
        assert cfg.provider == "deepseek"
        assert cfg.agent.max_retries == 3


def test_config_from_yaml():
    with tempfile.TemporaryDirectory() as tmp:
        yml = Path(tmp) / ".aicoder.yml"
        yml.write_text("provider: openai\nmodel: gpt-4o\n")
        from aicoder.config import load_config
        cfg = load_config(yml)
        assert cfg.provider == "openai"
        assert cfg.model == "gpt-4o"
