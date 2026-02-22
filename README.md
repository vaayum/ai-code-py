# AICoder — Python Edition 🐍

Autonomous AI coding agent. Best LLM ecosystem. Zero JVM required.

```bash
# Install (requires Python 3.11+)
pip install aicoder
# or with uv (recommended, no pip needed):
uv tool install aicoder
```

## Quick Start

```bash
# Interactive REPL (Claude Code-like)
aicoder

# Fix a bug
aicoder fix "Fix the NPE in UserService.py" --model deepseek

# Full audit since main branch
aicoder audit --since main "Check for security issues"

# Multi-agent mode (Planner + Coder + Reviewer + Tester)
aicoder run --agents "Add null-checks to all public methods in auth module"

# Generate test suite for a file  
aicoder test-gen src/services/payment.py
```

## Configuration

```bash
export DEEPSEEK_API_KEY=sk-...
export OPENAI_API_KEY=sk-...
export ANTHROPIC_API_KEY=sk-ant-...
```

Or create `.aicoder.yml`:
```yaml
provider: deepseek
model: deepseek-chat
temperature: 0.1
```

## Features

| Feature | Status |
|---------|--------|
| Interactive REPL with streaming | ✅ |
| Fix / Refactor / Audit / Test modes | ✅ |
| Multi-agent (Planner+Coder+Reviewer+Tester) | ✅ LangGraph |
| Semantic codebase search | ✅ ChromaDB |
| Multi-language AST (Python/JS/Go/Rust/Java) | ✅ tree-sitter |
| Git integration | ✅ GitPython |
| Agent memory (cross-session) | ✅ |
| 5 LLM providers | ✅ |
| Test generation | ✅ |
| Native binary (no JVM) | ✅ via `pipx` / `uv tool` |
