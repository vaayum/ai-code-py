import pytest
from pathlib import Path
from aicoder.memory import AgentMemory
from aicoder.tools.memory_tools import make_memory_tools

def test_memory_save_and_load(tmp_path):
    mem = AgentMemory(tmp_path)
    mem.save_entry("convention:test", "use pytest")
    
    # Read file 3 times to trigger key_files promotion
    mem.record_file_read("main.py")
    mem.record_file_read("main.py")
    mem.record_file_read("main.py")
    
    assert "use pytest" in mem.conventions
    assert "main.py" in mem.key_files
    
    mem.save()
    
    # Verify persistence
    mem2 = AgentMemory(tmp_path)
    assert "use pytest" in mem2.conventions
    assert "main.py" in mem2.key_files

def test_memory_tools(tmp_path):
    mem = AgentMemory(tmp_path)
    tools = {t.name: t for t in make_memory_tools(mem)}
    
    res = tools["save_memory"].invoke({"key": "project:summary", "value": "A test project"})
    assert "Remembered" in res
    assert mem.project_summary == "A test project"
    
    res = tools["recall_memory"].invoke({})
    assert "A test project" in res
