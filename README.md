# AICoder — Python Edition 🤖

> **Autonomous AI coding agent** — fix bugs, refactor code, audit security, generate tests, and run multi-agent pipelines. No JVM required. Works with any LLM: cloud BYOK, local (Ollama), or your enterprise on-premise endpoint.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Option 1 — Cloud (BYOK)](#option-1--cloud-byok)
  - [Option 2 — Local (Ollama)](#option-2--local-ollama)
  - [Option 3 — Enterprise / On-Premise](#option-3--enterprise--on-premise)
- [Commands](#commands)
  - [Smart Commands (Intent Detection)](#smart-commands-intent-detection)
  - [models](#models--list-recommended-models)
  - [enterprise-init](#enterprise-init--setup-wizard)
  - [mcp-init](#mcp-init--mcp-server-config)
- [Flags Reference](#flags-reference)
- [Multi-Agent Mode](#multi-agent-mode)
- [AST Refactoring Tools](#ast-refactoring-tools)
- [Interactive Mode](#interactive-mode)
- [Dry-Run Mode](#dry-run-mode)
- [Design Spec Support](#design-spec-support)
- [Semantic Search](#semantic-search)
- [Agent Memory](#agent-memory)
- [MCP Integration](#mcp-integration)
- [Architecture](#architecture)
- [Live Demo](#live-demo)
- [Supported Models](#supported-models)

---

## Quick Start

```bash
# 1. Install
pip install aicoder   # or: uv tool install aicoder

# 2. Set your key (DeepSeek is cheapest, great quality)
export DEEPSEEK_API_KEY=sk-...

# 3. Run
aicoder "fix the null pointer in UserService" --model deepseek
```

---

## Installation

**Requirements:** Python 3.11+

```bash
# Standard pip
pip install aicoder

# uv (recommended — faster, isolated)
uv tool install aicoder

# For local Ollama models — no API key needed
brew install ollama
ollama pull qwen2.5-coder:7b
aicoder "add logging" --model ollama
```

---

## Configuration

AICoder supports three deployment modes, configured via `.aicoder.yml` or environment variables.

### Option 1 — Cloud (BYOK)

Set one environment variable for your chosen provider:

```bash
export ANTHROPIC_API_KEY=sk-ant-...    # Claude
export OPENAI_API_KEY=sk-...           # GPT-4o or o3-mini
export DEEPSEEK_API_KEY=sk-...         # DeepSeek (10× cheaper)
export GOOGLE_API_KEY=...              # Gemini 2.0 (1M ctx)
```

Then run any command with `--model <provider>`:

```bash
aicoder "add input validation" --model anthropic
aicoder "add input validation" --model deepseek
aicoder "add input validation" --model gemini
```

Or set defaults in `.aicoder.yml`:

```yaml
provider: anthropic
model: claude-sonnet-4-6   # or claude-opus-4-6, claude-haiku-4-5-20251001

agent:
  temperature: 0.1
  max_retries: 3
  max_tokens: 4096
```

### Option 2 — Local (Ollama)

Zero API cost. Runs 100% on your machine.

```bash
# Pull the model once
ollama pull qwen2.5-coder:7b   # 6GB VRAM — recommended for most developers

# Run with it
aicoder "refactor into smaller functions" --model ollama
```

Configure a specific local model in `.aicoder.yml`:

```yaml
provider: ollama
model: qwen2.5-coder:32b   # best local quality (24GB VRAM)
```

### Option 3 — Enterprise / On-Premise

For air-gapped / corporate LLM deployments. The pattern is universal:
**acquire token → send to hosted LLM endpoint**.

#### Run the setup wizard (recommended):

```bash
aicoder enterprise-init
```

The wizard walks through 5 steps and writes `.aicoder.yml` automatically:

```
══ Step 1 / 5 — LLM Endpoint ══════════════════
LLM base URL [https://llm.corp.internal/v1]:
Model name [corp-llm-model]:

══ Step 2 / 5 — Authentication ════════════════
  1 — Token already in an environment variable
  2 — Static API key in config or env var
  3 — Call an HTTP endpoint (OAuth2 / custom auth API)
  4 — Corporate Python .whl (corp_security + corp_auth pattern)
Choose (1-4):

══ Step 3 / 5 — TLS / Certificates ═══════════
TLS verification? [default/pem/off]:

══ Step 4 / 5 — HTTP Proxy ════════════════════
Does traffic go through an HTTP proxy? [y/N]:

══ Step 5 / 5 — Agent Settings ════════════════
Temperature, retries, max tokens...
```

#### Or configure manually:

**Env-var token** (simplest):
```yaml
mode: enterprise
enterprise:
  base_url: https://llm.corp.internal/v1
  model: corp-llm-model
  auth_strategy:
    strategy: env_var
    token_env: CORP_LLM_TOKEN
```

**OAuth2 / Token endpoint** (most common enterprise pattern):
```yaml
mode: enterprise
enterprise:
  base_url: https://llm.corp.internal/v1
  model: corp-llm-model
  auth_strategy:
    strategy: token_endpoint
    url: https://auth.corp.internal/oauth/token
    method: POST
    payload:
      grant_type: client_credentials
      client_id: ${CORP_CLIENT_ID}         # env var expanded at runtime
      client_secret: ${CORP_CLIENT_SECRET}
    token_path: access_token    # JSONPath into response JSON
    token_ttl_seconds: 3600     # cache token, auto-refresh on 401
```

**Corporate .whl module** (e.g. corporate .whl pattern):
```yaml
mode: enterprise
enterprise:
  base_url: https://llm.corp.internal/v1
  model: corp-internal-model
  ca_bundle: /etc/ssl/corp-ca.pem       # optional: custom CA
  proxy_url: http://proxy.corp:3128     # optional: HTTP proxy
  auth_strategy:
    strategy: whl_module
    setup_module: corp_security          # pip install corp_security.whl
    setup_func: enable_certs
    setup_kwargs: {force: true}
    token_module: corp_auth
    token_func: get_auth_token
    token_ttl_seconds: 3600
    refresh_on_401: true                # auto-refresh on expired token
```

All four auth strategies share the same runtime contract:
1. `setup()` — one-time cert/TLS injection (if needed)
2. `get_token()` — thread-safe, cached bearer token
3. `build_httpx_client()` — injects `Authorization: Bearer <token>` on every LLM call, retries on 401

---

## Commands

AICoder operates via a single, smart command interface. You just describe what you want in plain English, and the agent automatically detects your intent (`fix`, `refactor`, `audit`, or `test-gen`).

### Smart Commands (Intent Detection)

**1. Fix a bug or implement a feature** (Default intent)
```bash
aicoder "Fix the ZeroDivisionError in calculator.py" --model deepseek

# With interactive diff approval (approve each file change before it's written)
aicoder "Add input validation to all endpoints" --model anthropic --interactive

# With a design spec document
aicoder "Implement auth module" --spec design/auth-spec.md --model deepseek

# Dry-run (show what would change, write nothing)
aicoder "Remove unused imports" --model deepseek --dry-run

# Target a specific directory
aicoder "Add retry logic" --dir ./services --model deepseek

# Multi-agent pipeline: Planner → Coder → Reviewer + Tester → Synthesis
aicoder "Add rate limiting to the API" --agents
```

**Live demo** — Agent fixed a ZeroDivisionError using DeepSeek:

```
╭─────────────────────────────────────────────╮
│ 🤖 AICoder                                  │
│ Mode:  fix                                  │
│ Model: deepseek                             │
│ Dir:   /tmp/aicoder_demo                    │
╰─────────────────────────────────────────────╯

⠸ Agent is working...

──────────────────── Result ──────────────────

Changes Made:
 1 Fixed the ZeroDivisionError — now handled gracefully
 2 Added safe_divide() function with:
    • Type checking for numerator and denominator
    • Zero division error handling with clear messages
    • General exception handling for unexpected errors
 3 Added comprehensive test suite covering:
    • Normal division, edge cases, error conditions
    • Large/small numbers, negative values, type errors

✅ Zero division errors are caught and handled gracefully
✅ All edge cases tested and handled
✅ Bug fixed: completely removed crash on 1/0
```

**2. Refactor without changing behavior** (Keywords: `refactor`, `reorganize`, `clean up`, etc.)
```bash
aicoder "Extract the payment logic into a PaymentService class" --model deepseek
aicoder "Apply SOLID principles to the user module" --model anthropic
aicoder "Convert callbacks to async/await" --model deepseek --interactive
```

**3. Security & quality review** (Keywords: `audit`, `review`, `security`, `leak`, etc.)
```bash
# Audit the whole codebase
aicoder "Check for SQL injection, hardcoded secrets, and insecure deserialization"

# Since a git branch (only changed files)
aicoder "Security review of all changes" --since main

# Audit a specific directory
aicoder "Check for PII leaks in logging" --dir ./services/user
```

**4. Generate test suites** (Keywords: `test`, `coverage`, `pytest`, etc.)
```bash
# Generate tests for a specific file or directory
aicoder "Generate tests for src/services/payment.py" --model deepseek
```
*(Note: tests are automatically generated, run, and fixed up to 3 times if they fail).*

---

### `models` — List recommended models

```bash
aicoder models
```

Output:
```
╭──────────────────────────────────────── ☁️  Cloud / BYOK Models ────────────────────────────────────────────╮
│  Model ID                    Provider   Env Var            Notes                                             │
│  claude-opus-4-6             anthropic  ANTHROPIC_API_KEY  ⭐ Best for agents & coding — most intelligent   │
│  claude-sonnet-4-6           anthropic  ANTHROPIC_API_KEY  Best speed+intelligence balance — recommended     │
│  claude-haiku-4-5-20251001   anthropic  ANTHROPIC_API_KEY  Fastest Claude — budget/high-volume use          │
│  deepseek-chat               deepseek   DEEPSEEK_API_KEY   💰 10× cheaper than OpenAI, excellent code       │
│  deepseek-reasoner           deepseek   DEEPSEEK_API_KEY   Chain-of-thought — for hard bugs                 │
│  gpt-4o                      openai     OPENAI_API_KEY     Fast, bulletproof tool calling, 128K ctx          │
│  gemini-2.0-flash            gemini     GOOGLE_API_KEY     1M token context — great for whole-codebase      │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────╯

╭───────────────────────────────── 🏠  Local Models (Ollama) ─────────────────────────────────╮
│  Model ID (ollama pull ...)   Min VRAM  Notes                                               │
│  qwen2.5-coder:32b            24GB      ⭐ Best local coding model                          │
│  qwen2.5-coder:7b              6GB      Consumer GPU (RTX 3080) — great tool-use            │
│  llama3.1:70b                 48GB      Best general-purpose local, 128K ctx                │
│  llama3.2:3b                   4GB      Ultra-low resource — CI/hobbyist use                │
╰─────────────────────────────────────────────────────────────────────────────────────────────╯
```

---

### `enterprise-init` — Setup wizard

```bash
aicoder enterprise-init --dir /path/to/project
```

5-step interactive wizard that generates `.aicoder.yml` for enterprise deployments.

---

### `mcp-init` — MCP server config

```bash
aicoder mcp-init
```

Creates `.aicoder/mcp.json` with an example MCP server configuration. Then use `--mcp` on any command to load external tools.

---

## Flags Reference

| Flag | Commands | Description |
|------|----------|-------------|
| `--model` | all | Provider: `anthropic`, `openai`, `deepseek`, `gemini`, `ollama`, `enterprise` |
| `--dir` | all | Target directory (default: current directory) |
| `--config` | all | Path to `.aicoder.yml` config file |
| `--dry-run` | all | Show diffs but write no files |
| `--interactive` | all | Approve each file change with colored diff before writing |
| `--spec` | all | Path to a design doc/spec — prepended to the instruction |
| `--agents` | all | Enable multi-agent pipeline (Planner→Coder→Reviewer→Tester) |
| `--since` | all | Git ref (branch/commit) — prioritize changed files for testing or review |
| `--mcp` | all | Path to MCP config or use `.aicoder/mcp.json` |
| `--reindex` | all | Force a full rebuild of the semantic codebase vector index |

---

## Multi-Agent Mode

```bash
aicoder "Add comprehensive rate limiting to all API endpoints" --agents
```

Launches a **4-agent pipeline**:

```
┌─────────────────────────────────────────────────────────────┐
│                    Multi-Agent Pipeline                      │
│                                                             │
│  📋 Planner                                                 │
│    └─ Creates implementation plan + task breakdown          │
│         ↓                                                   │
│  💻 Coder            🔍 Reviewer         🧪 Tester          │
│    └─ Implements   └─ Reviews changes  └─ Runs tests        │
│         └─────────────────┴─────────────────┘               │
│                           ↓                                  │
│  📊 Synthesis                                               │
│    └─ Merges results, writes final summary                   │
└─────────────────────────────────────────────────────────────┘
```

The Planner sees the full codebase context. The Coder, Reviewer, and Tester run in parallel with independent read/write scopes, then the Synthesis agent merges results.

---

## AST Refactoring Tools

The agent has access to **5 precise AST-aware tools** for surgical code edits — avoiding whole-file rewrites:

| Tool | What it does |
|------|-------------|
| `add_function` | Add a method/function to a class or module |
| `replace_function_body` | Replace a function's body while keeping its signature |
| `add_import` | Safely add an import statement (no duplicates) |
| `add_decorator` | Add `@staticmethod`, `@cache`, `@dataclass` etc. |
| `rename_symbol` | Word-boundary-safe rename across a file |

Python files use Python's `ast` module for exact line locations. All other languages (JS/TS/Go/Rust/Java) use a regex-based fallback.

---

## Interactive Mode

```bash
aicoder "Refactor auth module" --interactive
```

Before writing any file, the agent shows a **colored diff** and asks for approval:

```
┌─ Proposed change: aicoder/auth.py ────────────────────────┐
│ - def login(user, password):                               │
│ -     return db.query(f"SELECT * FROM users WHERE...")      │
│ +                                                          │
│ + def login(user: str, password: str) -> dict | None:     │
│ +     """Authenticate user with parameterized query."""     │
│ +     return db.execute(                                   │
│ +         "SELECT * FROM users WHERE email=? AND pwd=?",   │
│ +         (user, hash_password(password))                  │
│ +     ).fetchone()                                         │
└────────────────────────────────────────────────────────────┘

  [y] Apply   [n] Skip   [a] Apply All   [q] Quit
```

---

## Dry-Run Mode

```bash
aicoder "Remove all print debug statements" --dry-run
```

Shows exactly what would change — no files written. Useful for CI pipelines or reviewing before committing.

---

## Design Spec Support

Pass a design document (markdown, text, or spec) and the agent will follow it:

```bash
# Write your spec
cat > design/auth-spec.md << EOF
# Auth Module Redesign
- Use JWT with RS256 signing
- 15-minute access tokens, 7-day refresh tokens
- Rate limit: 5 failed attempts → 15-minute lockout
EOF

# Agent reads the spec and implements it
aicoder "Implement the auth module" --spec design/auth-spec.md --model anthropic
```

---

## Semantic Search

AICoder indexes your codebase with **ChromaDB + sentence-transformers** for semantic retrieval. The agent automatically finds relevant files even when they're not explicitly mentioned:

```bash
# "payment" will find PaymentService, StripeClient, InvoiceProcessor etc.
aicoder "Add retry logic to all payment operations" --model deepseek
```

- **Lazy Loading**: On the first run, the codebase is indexed automatically. Subsequent runs start up instantly using the cached SQLite index. Use `--reindex` to force a rebuild.
- **Real-time Hot-Reloading**: The vector index updates automatically in the background whenever the agent creates, edits, or deletes files.

---

## Agent Memory

AICoder maintains **cross-session memory** in `.aicoder/memory.json`:

```bash
# First session: agent learns your conventions
aicoder "Add logging" --model deepseek
# → Memory: "Project uses structlog with JSON format, level = INFO"

# Next session: agent automatically follows the same conventions
aicoder "Add logging to payment module" --model deepseek
# → Agent applies structlog JSON logging without being told
```

Memory tracks:
- **Recent Actions**: History of recent commands.
- **Learned Conventions**: Automatically or manually saved project patterns.
- **Project Summary**: An auto-generated high-level description of your codebase.
- **Key Files**: Frequently accessed files are tracked; popular files are promoted to key files and injected into the agent's context.

---

## MCP Integration

Connect external tools via the **Model Context Protocol**:

```bash
# Initialize MCP config
aicoder mcp-init

# Edit .aicoder/mcp.json
{
  "servers": [
    {
      "name": "github",
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
    }
  ]
}

# Use MCP tools in any command
aicoder "Create a PR for the current changes" --mcp
```

Supports both **stdio** (local process) and **HTTP** (remote server) transports.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          AICoder Architecture                        │
│                                                                     │
│  CLI (Typer)                                                        │
│   ├─ Smart Command (Intent Detection)                              │
│   ├─ models / enterprise-init / mcp-init                           │
│   └─ --dry-run / --interactive / --agents / --spec / --mcp         │
│                          │                                           │
│  Agent Layer (LangChain + LangGraph)                                │
│   ├─ Single Agent       → ReAct loop with tool-use                 │
│   └─ Multi-Agent        → Planner+Coder+Reviewer+Tester pipeline   │
│                          │                                           │
│  Tools                                                              │
│   ├─ FileTools          → read / write / delete / list (diff+approval) │
│   ├─ GitTools           → diff / log / status / since-branch       │
│   ├─ BuildTools         → run tests / linter / build commands      │
│   ├─ SearchTools        → semantic search via ChromaDB             │
│   └─ AstTools           → add_function / rename_symbol / add_import │
│                          │                                           │
│  LLM Providers                                                      │
│   ├─ Cloud BYOK         → Anthropic / OpenAI / DeepSeek / Gemini   │
│   ├─ Local              → Ollama (qwen2.5-coder, llama3.1, …)     │
│   └─ Enterprise         → Custom endpoint + auth plugin            │
│       └─ Auth Strategies: env_var / static_key / token_endpoint / whl_module │
│                                                                     │
│  Memory & Indexing                                                  │
│   ├─ AgentMemory        → cross-session JSON store                 │
│   ├─ CodebaseIngestor   → ChromaDB + sentence-transformers index   │
│   └─ McpServerLoader    → stdio + HTTP MCP server support         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Live Demo

**DeepSeek fixing a ZeroDivisionError** (actual run — no edits):

```
$ aicoder "fix the zero division error and add proper error handling" \
    --dir ./demo --model deepseek

╭─────────────────────────────────────────────╮
│ 🤖 AICoder                                  │
│ Mode:  fix   Model: deepseek   Dir: ./demo  │
╰─────────────────────────────────────────────╯

⠸ Indexing codebase...
⠇ Agent is working...

🔧 read_file("demo_bug.py")
🔧 update_file("demo_bug.py", ...)

──────────────────────── Result ──────────────────────────

Changes Made:

 1 Fixed the ZeroDivisionError: result = x / y with y = 0 → now handled
 2 Added safe_divide() with:
    • Type checking for numerator and denominator
    • ZeroDivisionError handling with clear error messages
    • General exception handling for unexpected errors
 3 Added comprehensive test suite (15 test cases):
    • Normal division, edge cases, negative numbers, floats
    • Error cases: strings, None, invalid types, large numbers

Key outcomes:
 ✅ Zero division errors caught and handled gracefully
 ✅ Type errors provide informative messages
 ✅ All 15 edge cases tested
 ✅ Code is now robust — no crashes on invalid input
```

---

## Supported Models

Run `aicoder models` to see the full list. Recommended picks:

| Use Case | Model | Provider |
|----------|-------|----------|
| Best overall (agents) | `claude-opus-4-6` | anthropic |
| Daily use (best balance) | `claude-sonnet-4-6` | anthropic |
| Budget / high-volume | `deepseek-chat` | deepseek |
| Large codebase search | `gemini-2.0-flash` | gemini |
| No API / offline | `qwen2.5-coder:7b` | ollama |
| Complex debugging | `deepseek-reasoner` | deepseek |

---

## Project Status

| Feature | Status |
|---------|--------|
| Smart Commands (fix / refactor / audit / test-gen) | ✅ |
| Interactive diff approval | ✅ |
| Dry-run mode | ✅ |
| Design spec support | ✅ |
| Multi-agent (Planner→Coder→Reviewer→Tester) | ✅ LangGraph |
| AST tools (add/replace/rename/import/decorator) | ✅ |
| Semantic codebase search | ✅ ChromaDB |
| Git integration (diff, log, since-branch) | ✅ GitPython |
| Agent memory (cross-session) | ✅ |
| 5 LLM providers (Anthropic/OpenAI/DeepSeek/Gemini/Ollama) | ✅ |
| Enterprise auth (env_var / oauth / whl module) | ✅ |
| Enterprise setup wizard | ✅ |
| MCP server integration (stdio + HTTP) | ✅ |
| Model catalog (`aicoder models`) | ✅ |

---

## License

MIT
