"""Tests for AstTools — no LLM required, pure Python ast manipulation."""
from __future__ import annotations

import textwrap
import tempfile
from pathlib import Path

import pytest

from aicoder.tools.ast_tools import AstTools


# ── Fixtures ───────────────────────────────────────────────────────────────────

SAMPLE_PY = """\
\"\"\"Sample module for AST tool tests.\"\"\"
import os

CONSTANT = 42


class Calculator:
    \"\"\"A simple calculator.\"\"\"

    def add(self, x: int, y: int) -> int:
        return x + y

    def subtract(self, x: int, y: int) -> int:
        return x - y


def helper(n: int) -> int:
    return n * 2
"""


@pytest.fixture
def tmp_py(tmp_path):
    """Create a temp project with a sample Python file."""
    src = tmp_path / "calc.py"
    src.write_text(SAMPLE_PY)
    return tmp_path, src


# ── add_function ──────────────────────────────────────────────────────────────

class TestAddFunction:

    def test_add_method_to_class(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["add_function"].invoke({
            "file_path": "calc.py",
            "class_name": "Calculator",
            "function_code": "def multiply(self, x: int, y: int) -> int:\n    return x * y",
        })
        assert "✅" in result
        source = src.read_text()
        assert "def multiply" in source
        assert "return x * y" in source

    def test_add_module_level_function(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["add_function"].invoke({
            "file_path": "calc.py",
            "class_name": "",
            "function_code": "def new_helper(n: int) -> str:\n    return str(n)",
        })
        assert "✅" in result
        assert "def new_helper" in src.read_text()

    def test_add_to_nonexistent_class(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["add_function"].invoke({
            "file_path": "calc.py",
            "class_name": "NonExistent",
            "function_code": "def foo(self): pass",
        })
        assert "❌" in result
        assert "NonExistent" in result

    def test_add_to_nonexistent_file(self, tmp_path):
        at = AstTools(tmp_path)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["add_function"].invoke({
            "file_path": "missing.py",
            "class_name": "",
            "function_code": "def foo(): pass",
        })
        assert "❌" in result


# ── replace_function_body ─────────────────────────────────────────────────────

class TestReplaceFunctionBody:

    def test_replace_method_body(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["replace_function_body"].invoke({
            "file_path": "calc.py",
            "class_name": "Calculator",
            "function_name": "add",
            "new_body": "result = x + y\nreturn result",
        })
        assert "✅" in result
        source = src.read_text()
        assert "result = x + y" in source
        # Original single-line body should be gone
        assert "return x + y" not in source

    def test_replace_module_function_body(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["replace_function_body"].invoke({
            "file_path": "calc.py",
            "class_name": "",
            "function_name": "helper",
            "new_body": "doubled = n * 2\nreturn doubled",
        })
        assert "✅" in result
        assert "doubled = n * 2" in src.read_text()

    def test_replace_nonexistent_function(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["replace_function_body"].invoke({
            "file_path": "calc.py",
            "class_name": "Calculator",
            "function_name": "nonexistent",
            "new_body": "pass",
        })
        assert "❌" in result


# ── add_import ────────────────────────────────────────────────────────────────

class TestAddImport:

    def test_add_new_import(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["add_import"].invoke({
            "file_path": "calc.py",
            "import_statement": "from collections import deque",
        })
        assert "✅" in result
        assert "from collections import deque" in src.read_text()

    def test_add_duplicate_import_skipped(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["add_import"].invoke({
            "file_path": "calc.py",
            "import_statement": "import os",
        })
        assert "ℹ️" in result
        # Should not duplicate
        assert src.read_text().count("import os") == 1

    def test_add_import_after_existing_imports(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        tools["add_import"].invoke({
            "file_path": "calc.py",
            "import_statement": "import sys",
        })
        source = src.read_text()
        os_pos = source.index("import os")
        sys_pos = source.index("import sys")
        # import sys should be near import os (both in import block)
        assert abs(os_pos - sys_pos) < 200


# ── add_decorator ─────────────────────────────────────────────────────────────

class TestAddDecorator:

    def test_add_decorator_to_method(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["add_decorator"].invoke({
            "file_path": "calc.py",
            "target_name": "add",
            "decorator": "@staticmethod",
            "class_name": "Calculator",
        })
        assert "✅" in result
        source = src.read_text()
        assert "@staticmethod" in source
        # Decorator should appear before def add
        static_pos = source.index("@staticmethod")
        def_pos = source.index("def add")
        assert static_pos < def_pos

    def test_add_decorator_to_class(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["add_decorator"].invoke({
            "file_path": "calc.py",
            "target_name": "Calculator",
            "decorator": "@dataclass",
            "class_name": "",
        })
        assert "✅" in result
        assert "@dataclass" in src.read_text()

    def test_add_decorator_target_not_found(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["add_decorator"].invoke({
            "file_path": "calc.py",
            "target_name": "nonexistent",
            "decorator": "@some_decorator",
            "class_name": "",
        })
        assert "❌" in result


# ── rename_symbol ─────────────────────────────────────────────────────────────

class TestRenameSymbol:

    def test_rename_class_name(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["rename_symbol"].invoke({
            "file_path": "calc.py",
            "old_name": "Calculator",
            "new_name": "MathEngine",
        })
        assert "✅" in result
        source = src.read_text()
        assert "class MathEngine:" in source
        assert "Calculator" not in source

    def test_rename_method(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["rename_symbol"].invoke({
            "file_path": "calc.py",
            "old_name": "helper",
            "new_name": "double",
        })
        assert "✅" in result
        assert "def double" in src.read_text()
        assert "def helper" not in src.read_text()

    def test_rename_reports_count(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["rename_symbol"].invoke({
            "file_path": "calc.py",
            "old_name": "CONSTANT",
            "new_name": "ANSWER",
        })
        assert "1" in result  # 1 occurrence
        assert "ANSWER" in src.read_text()

    def test_rename_word_boundary(self, tmp_py):
        root, src = tmp_py
        # Write a file where 'add' appears as a standalone word and inside 'addition'
        src.write_text("def add(): pass\naddition = 1\n")
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        tools["rename_symbol"].invoke({
            "file_path": "calc.py",
            "old_name": "add",
            "new_name": "sum_values",
        })
        source = src.read_text()
        assert "def sum_values():" in source
        # 'addition' must NOT be renamed
        assert "addition" in source

    def test_rename_not_found(self, tmp_py):
        root, src = tmp_py
        at = AstTools(root)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["rename_symbol"].invoke({
            "file_path": "calc.py",
            "old_name": "XyzNotHere",
            "new_name": "Abc",
        })
        assert "ℹ️" in result


# ── Path security ─────────────────────────────────────────────────────────────

class TestPathSecurity:

    def test_path_traversal_blocked(self, tmp_path):
        at = AstTools(tmp_path)
        tools = {t.name: t for t in at.get_tools()}

        result = tools["rename_symbol"].invoke({
            "file_path": "../../etc/passwd",
            "old_name": "root",
            "new_name": "hacked",
        })
        assert "❌" in result or "traversal" in result.lower() or "not found" in result.lower()
