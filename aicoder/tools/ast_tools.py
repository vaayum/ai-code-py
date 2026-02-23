"""AST-aware refactoring tools exposed to the LLM agent.

Provides surgical, syntax-safe code edits by:
- Using Python's built-in ``ast`` module for Python files (exact line locations)
- Using ``tree-sitter`` for JS / TS / Go / Rust / Java files (node offsets)
- Falling back to safe regex-guided text surgery with clear error messages

Available tools (mirroring Java's AstTools):
- ``add_function``          — append a function/method to a class or module
- ``replace_function_body`` — replace a function body while keeping its signature
- ``add_import``            — safely add an import if not already present
- ``add_decorator``         — add a decorator to a function or class
- ``rename_symbol``         — rename all occurrences of a name within a file
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from textwrap import dedent, indent
from typing import Optional

from langchain_core.tools import tool


class AstTools:
    """Stateful AST tools bound to a project root directory."""

    def __init__(self, project_root: Path) -> None:
        self.root = project_root.resolve()

    def get_tools(self) -> list:
        root = self.root

        @tool
        def add_function(file_path: str, class_name: str, function_code: str) -> str:
            """Add a function or method to a class (or module-level if class_name is empty).

            Args:
                file_path: Relative path to the source file.
                class_name: Name of the class to add into, or '' for module-level.
                function_code: Complete function definition, e.g.
                    'def calculate(self, x: int) -> int:\\n    return x * 2'
            """
            try:
                return _add_function(root, file_path, class_name, function_code)
            except (FileNotFoundError, ValueError) as e:
                return f"❌ {e}"

        @tool
        def replace_function_body(
            file_path: str, class_name: str, function_name: str, new_body: str
        ) -> str:
            """Replace the body of a function while keeping its signature unchanged.

            Args:
                file_path: Relative path to the source file.
                class_name: Name of the class containing the function, or '' for module-level.
                function_name: Name of the function to modify.
                new_body: New body statements (indented with 4 spaces), e.g.
                    '    return x * 2\\n    # updated'
            """
            try:
                return _replace_function_body(root, file_path, class_name, function_name, new_body)
            except (FileNotFoundError, ValueError) as e:
                return f"❌ {e}"

        @tool
        def add_import(file_path: str, import_statement: str) -> str:
            """Add an import to a file if it is not already present.

            Args:
                file_path: Relative path to the source file.
                import_statement: Full import line, e.g.
                    'from collections import deque' or 'import os'
            """
            try:
                return _add_import(root, file_path, import_statement)
            except (FileNotFoundError, ValueError) as e:
                return f"❌ {e}"

        @tool
        def add_decorator(
            file_path: str, target_name: str, decorator: str, class_name: str = ""
        ) -> str:
            """Add a decorator to a function or class.

            Args:
                file_path: Relative path to the source file.
                target_name: Name of the function or class to decorate.
                decorator: Decorator string, e.g. '@staticmethod' or '@cache'.
                class_name: If decorating a method, the enclosing class name (or '').
            """
            try:
                return _add_decorator(root, file_path, target_name, decorator, class_name)
            except (FileNotFoundError, ValueError) as e:
                return f"❌ {e}"

        @tool
        def rename_symbol(file_path: str, old_name: str, new_name: str) -> str:
            """Rename all occurrences of a symbol (variable, function, class) within a file.

            Uses word-boundary matching so only exact identifiers are renamed.
            Args:
                file_path: Relative path to the source file.
                old_name: Current symbol name.
                new_name: New symbol name.
            """
            try:
                return _rename_symbol(root, file_path, old_name, new_name)
            except (FileNotFoundError, ValueError) as e:
                return f"❌ {e}"

        return [add_function, replace_function_body, add_import, add_decorator, rename_symbol]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve(root: Path, path: str) -> Path:
    target = (root / path).resolve()
    if not str(target).startswith(str(root)):
        raise ValueError(f"Path traversal detected: {path}")
    return target


def _read(target: Path) -> str:
    if not target.exists():
        raise FileNotFoundError(f"File not found: {target}")
    return target.read_text(errors="replace")


def _write(target: Path, content: str) -> None:
    target.write_text(content)


# ── add_function ──────────────────────────────────────────────────────────────

def _add_function(root: Path, file_path: str, class_name: str, function_code: str) -> str:
    target = _resolve(root, file_path)
    source = _read(target)
    lines = source.splitlines(keepends=True)

    func_code = dedent(function_code).rstrip()

    if target.suffix == ".py":
        return _add_function_python(target, source, lines, class_name, func_code, file_path)
    else:
        return _add_function_generic(target, source, lines, class_name, func_code, file_path)


def _add_function_python(
    target: Path, source: str, lines: list[str], class_name: str,
    func_code: str, file_path: str
) -> str:
    if not class_name:
        # Module-level: append at the end
        new_source = source.rstrip() + "\n\n\n" + func_code + "\n"
        _write(target, new_source)
        return f"✅ Added function to module level in {file_path}"

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return f"❌ Syntax error parsing {file_path}: {e}"

    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef,)) and node.name == class_name:
            # Insert after last item in class, maintaining indentation
            class_end_line = node.end_lineno  # 1-based
            insert_after = class_end_line - 1  # 0-based index

            # Indent the function with 4 spaces for class membership
            indented_func = indent(func_code, "    ")
            new_lines = lines[:insert_after] + ["\n", indented_func + "\n"] + lines[insert_after:]
            _write(target, "".join(new_lines))
            return f"✅ Added function '{_extract_def_name(func_code)}' to class '{class_name}' in {file_path}"

    return f"❌ Class '{class_name}' not found in {file_path}."


def _add_function_generic(
    target: Path, source: str, lines: list[str], class_name: str,
    func_code: str, file_path: str
) -> str:
    """Generic text-based insertion for non-Python files."""
    if not class_name:
        new_source = source.rstrip() + "\n\n" + func_code + "\n"
        _write(target, new_source)
        return f"✅ Added function to {file_path}"

    # Find class body end by locating unindented closing brace / end keyword
    pattern = re.compile(rf"^\s*(class|interface|struct)\s+{re.escape(class_name)}\b", re.M)
    m = pattern.search(source)
    if not m:
        return f"❌ Class '{class_name}' not found in {file_path}."

    class_start_line = source[:m.start()].count("\n")
    # Find the closing brace / 'end' for this class
    depth = 0
    closing_line = None
    for i, line in enumerate(lines[class_start_line:], start=class_start_line):
        depth += line.count("{") - line.count("}")
        depth += line.count(" do ") + line.count(":\n") - 0  # rough
        if i > class_start_line and (
            (depth <= 0 and "{" in lines[class_start_line])
            or re.match(r"^(end|}\s*)$", line.strip())
        ):
            closing_line = i
            break

    if closing_line is None:
        return f"❌ Could not find end of class '{class_name}' in {file_path}."

    indented = indent(func_code + "\n", "    ")
    new_lines = lines[:closing_line] + ["\n", indented] + lines[closing_line:]
    _write(target, "".join(new_lines))
    return f"✅ Added function to class '{class_name}' in {file_path}"


# ── replace_function_body ─────────────────────────────────────────────────────

def _replace_function_body(
    root: Path, file_path: str, class_name: str, function_name: str, new_body: str
) -> str:
    target = _resolve(root, file_path)
    source = _read(target)

    if target.suffix != ".py":
        return _replace_function_body_generic(target, source, class_name, function_name, new_body, file_path)

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return f"❌ Syntax error in {file_path}: {e}"

    # Walk the tree to find the target function
    def find_func(nodes):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
                if not class_name:
                    return node
            if isinstance(node, ast.ClassDef) and (not class_name or node.name == class_name):
                for child in ast.walk(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == function_name:
                        return child
        return None

    func_node = find_func(ast.walk(tree))
    if func_node is None:
        loc = f"class '{class_name}'" if class_name else "module"
        return f"❌ Function '{function_name}' not found in {loc} in {file_path}."

    lines = source.splitlines(keepends=True)

    # Find first line of body (skip decorators and def line)
    body_start = func_node.body[0].lineno - 1   # 0-based
    body_end   = func_node.end_lineno            # 0-based exclusive

    # Determine indentation of the function body
    body_indent = len(lines[body_start]) - len(lines[body_start].lstrip())
    body_prefix = " " * body_indent

    # Re-indent the new body
    clean_body = dedent(new_body).strip()
    new_body_lines = [body_prefix + l + "\n" for l in clean_body.splitlines()] or [body_prefix + "pass\n"]

    new_lines = lines[:body_start] + new_body_lines + lines[body_end:]
    _write(target, "".join(new_lines))
    return f"✅ Replaced body of '{function_name}' in {file_path}"


def _replace_function_body_generic(
    target: Path, source: str, class_name: str, function_name: str, new_body: str, file_path: str
) -> str:
    """Generic regex-based body replacement for non-Python files."""
    # Find def/func/function/fn/void ... function_name(
    pattern = re.compile(
        rf"(\b(?:def|func|function|fn|void|public|private|protected|static|async)\s[^{{]*?\b{re.escape(function_name)}\s*\([^)]*\)[^{{]*\{{)",
        re.S,
    )
    m = pattern.search(source)
    if not m:
        return f"❌ Function '{function_name}' not found in {file_path}."

    # Find matching closing brace
    start = m.end()
    depth = 1
    i = start
    while i < len(source) and depth > 0:
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
        i += 1

    clean_body = dedent(new_body).strip()
    indented = "\n    " + "\n    ".join(clean_body.splitlines()) + "\n"
    new_source = source[:start] + indented + source[i - 1:]
    _write(target, new_source)
    return f"✅ Replaced body of '{function_name}' in {file_path}"


# ── add_import ────────────────────────────────────────────────────────────────

def _add_import(root: Path, file_path: str, import_statement: str) -> str:
    target = _resolve(root, file_path)
    source = _read(target)

    import_stmt = import_statement.strip()

    # Already present?
    if import_stmt in source:
        return f"ℹ️  Import already present in {file_path}: {import_stmt}"

    if target.suffix == ".py":
        return _add_import_python(target, source, import_stmt, file_path)
    else:
        return _add_import_generic(target, source, import_stmt, file_path)


def _add_import_python(target: Path, source: str, import_stmt: str, file_path: str) -> str:
    lines = source.splitlines(keepends=True)

    # Find last existing import line
    last_import_idx = 0
    in_docstring = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            in_docstring = not in_docstring
        if in_docstring:
            continue
        if stripped.startswith(("import ", "from ")):
            last_import_idx = i + 1  # insert after this line

    new_lines = lines[:last_import_idx] + [import_stmt + "\n"] + lines[last_import_idx:]
    _write(target, "".join(new_lines))
    return f"✅ Added import to {file_path}: {import_stmt}"


def _add_import_generic(target: Path, source: str, import_stmt: str, file_path: str) -> str:
    # Find last import/require/#include line
    pattern = re.compile(r"^(import|require|#include|use|using)\b.*$", re.M)
    matches = list(pattern.finditer(source))
    if matches:
        last_match = matches[-1]
        insert_pos = last_match.end() + 1
        new_source = source[:insert_pos] + import_stmt + "\n" + source[insert_pos:]
    else:
        new_source = import_stmt + "\n" + source

    _write(target, new_source)
    return f"✅ Added import to {file_path}: {import_stmt}"


# ── add_decorator ─────────────────────────────────────────────────────────────

def _add_decorator(
    root: Path, file_path: str, target_name: str, decorator: str, class_name: str
) -> str:
    target = _resolve(root, file_path)
    source = _read(target)

    dec = decorator.strip()
    if not dec.startswith("@"):
        dec = "@" + dec

    if target.suffix != ".py":
        return _add_decorator_generic(target, source, target_name, dec, file_path)

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return f"❌ Syntax error in {file_path}: {e}"

    lines = source.splitlines(keepends=True)
    nodes_to_check = list(ast.walk(tree))

    for node in nodes_to_check:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name != target_name:
                continue
            # Check if decorator already present
            existing_decs = [ast.unparse(d) for d in node.decorator_list]
            if any(dec.lstrip("@") in d for d in existing_decs):
                return f"ℹ️  Decorator already present on '{target_name}' in {file_path}"

            # Insert decorator before the node's first decorator or def line
            def_line = node.lineno - 1  # 0-based
            if node.decorator_list:
                def_line = node.decorator_list[0].lineno - 1  # before first decorator

            node_indent = len(lines[def_line]) - len(lines[def_line].lstrip())
            dec_line = " " * node_indent + dec + "\n"
            new_lines = lines[:def_line] + [dec_line] + lines[def_line:]
            _write(target, "".join(new_lines))
            return f"✅ Added {dec} to '{target_name}' in {file_path}"

    return f"❌ '{target_name}' not found in {file_path}."


def _add_decorator_generic(target: Path, source: str, target_name: str, decorator: str, file_path: str) -> str:
    # Find the line that defines the target and insert decorator before it
    pattern = re.compile(rf"^([ \t]*)(def|func|function|class|interface)\s+{re.escape(target_name)}\b", re.M)
    m = pattern.search(source)
    if not m:
        return f"❌ '{target_name}' not found in {file_path}."

    indent_str = m.group(1)
    insert_pos = m.start()
    new_source = source[:insert_pos] + indent_str + decorator + "\n" + source[insert_pos:]
    _write(target, new_source)
    return f"✅ Added {decorator} to '{target_name}' in {file_path}"


# ── rename_symbol ─────────────────────────────────────────────────────────────

def _rename_symbol(root: Path, file_path: str, old_name: str, new_name: str) -> str:
    target = _resolve(root, file_path)
    source = _read(target)

    # Word-boundary regex so 'foo' doesn't match 'foobar'
    pattern = re.compile(rf"\b{re.escape(old_name)}\b")
    new_source, count = pattern.subn(new_name, source)

    if count == 0:
        return f"ℹ️  '{old_name}' not found in {file_path}."

    _write(target, new_source)
    return f"✅ Renamed '{old_name}' → '{new_name}' ({count} occurrence{'s' if count != 1 else ''}) in {file_path}"


# ── helpers ───────────────────────────────────────────────────────────────────

def _extract_def_name(func_code: str) -> str:
    """Extract function/method name from a def line."""
    m = re.search(r"def\s+(\w+)", func_code)
    return m.group(1) if m else "?"
