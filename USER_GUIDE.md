# 📖 AICoder User Guide

Welcome to **AICoder**, your autonomous AI coding agent! This guide will teach you how to effectively collaborate with the agent to drastically speed up your day-to-day coding tasks.

---

## 🚀 1. Quick Start

### Installation
Ensure you have Python 3.11+ installed.
```bash
pip install aicoder
```

### Authentication
AICoder requires an LLM provider to think. DeepSeek is highly recommended as it provides GPT-4 quality at 10% of the cost.
```bash
export DEEPSEEK_API_KEY="your-api-key-here"
```
*(You can also use `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GOOGLE_API_KEY`)*

### Your First Task
Simply tell AICoder what to do in plain English!
```bash
aicoder "add error handling to the login function in auth.py" --model deepseek
```
*On the very first run in a project, AICoder will take ~10 seconds to automatically index your codebase. Future runs will be instant!*

---

## 💻 2. The Interactive Shell (REPL)

For sustained coding sessions, launch the **Interactive Shell** instead of running single hit-and-run commands. 

To launch the shell:
```bash
aicoder --model deepseek
```

### Features of the Shell:
1. **Multi-line Input**: You can paste code snippets or write long instructions easily.
2. **Context Retention**: The agent remembers the conversation within the session.
3. **Slash Commands**: Type `/help` at any time to see available tools:
   - `/files`: See exactly which files the agent has modified during this session.
   - `/diff [file]`: View the git diff of what the agent changed.
   - `/undo`: Discard the last file edit the agent made.
   - `/memory`: View the agent's long-term cross-session memory.
   - `/tests`: Quickly run your project's test suite to verify the agent's code.

---

## 🛠️ 3. Safe Execution Modes

AICoder respects your codebase. You are exactly as safe as you choose to be.

### 🔍 Interactive Approval (`--interactive` / `-i`)
If you don't fully trust the agent to write files automatically, use the `--interactive` flag. The agent will show you a beautiful, colored Git-style diff of the proposed changes and ask for your permission before modifying the file.
```bash
aicoder "migrate the database schema to v2" --interactive --model deepseek
```
*Prompt: `Apply change? [y]es  [n]o  [a]ll  [q]uit >`*

### 👁️ Dry-Run (`--dry-run`)
Perfect for CI/CD or sanity checks. The agent will formulate a plan, read files, and output exactly what it *would* change, but it will never actually touch your disk.
```bash
aicoder "remove all redundant console.log statements" --dry-run
```

---

## 🧠 4. Core Capabilities & Intent Auto-Detection

When you give AICoder an instruction, it automatically detects your intent and adjusts its prompt accordingly. However, you can also force specific behaviors:

### 🐛 Fixing & Implementing (`aicoder fix`)
The default mode. Best for writing new features or fixing bugs.
```bash
aicoder fix "implement the forgot password flow" 
```

### ♻️ Refactoring (`aicoder refactor`)
Instructs the agent specifically *not* to change the underlying business logic, but only to improve code quality, readability, or architecture.
```bash
aicoder refactor "extract the checkout logic into a PaymentService class"
```

### 🛡️ Auditing (`aicoder audit`)
A read-only security and code-quality sweep. The agent will scan your codebase for vulnerabilities like SQL injections, hardcoded secrets, or bad practices.
```bash
aicoder audit "check for PII data leaks in our logging" --dir ./src
```
*Pro-tip: Combine with `--since main` to only audit the code you changed in your current feature branch!*

### 🧪 Test Generation (`aicoder test-gen`)
Instructs the agent to specifically construct a test suite covering happy paths, edge cases, and errors.
```bash
aicoder test-gen --file src/payment/stripe.py
```

---

## 🤖 5. Advanced Workflows

### 👥 Multi-Agent Pipeline (`--agents`)
For exceedingly complex tasks where a single agent gets confused, invoke the Multi-Agent pipeline. This spins up four distinct AI personas:
1. **Planner**: Analyzes the codebase and drafts an Implementation Plan.
2. **Coder**: Focuses strictly on writing the code based on the plan.
3. **Reviewer / Tester**: Audits the Coder's work and runs your tests to try and break it.
4. **Synthesis**: Joins everything together in a final report.
```bash
aicoder "add comprehensive rate-limiting across every route in the app" --agents
```

### 📄 Design Specs (`--spec`)
Got a Jira ticket or a Markdown design doc? Feed it directly to the agent. It will anchor its implementation entirely around the acceptance criteria in your spec.
```bash
aicoder "build the UI component" --spec my-feature-ticket.md
```

---

## 🧩 6. Under the Hood Features

### 📡 Semantic Search & Hot-Reloading Index
AICoder uses ChromaDB to map out your codebase semantically. 
* If you ask it to "fix the auth system", you don't need to specify the filenames; it will semantically search and find `login.py` itself.
* Whenever the agent edits or creates a file, the vector index is hot-reloaded seamlessly in the background.

### 🧠 Cross-Session Memory
AICoder persists memory inside the `.aicoder/memory.json` file.
* **Key Files**: It tracks which files it frequently works on to build better context.
* **Conventions**: It automatically remembers your coding styles (e.g., "Always use `structlog` for logging") between completely separate terminal sessions.

### 🔌 MCP Servers (Model Context Protocol)
Need the agent to read your Slack messages, or search Jira?
Use `aicoder mcp-init` to create an `.aicoder/mcp.json` file, drop in any open-source Claude MCP server, and AICoder will automatically gain those abilities!
