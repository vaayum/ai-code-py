"""aicoder pr — auto-create a GitHub PR with an AI-written title + description."""
from __future__ import annotations
import json, os, re, subprocess, urllib.request
from pathlib import Path
from langchain_core.messages import HumanMessage, SystemMessage
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

console = Console()

_PR_SYSTEM_PROMPT = """You are an expert software engineer writing a GitHub Pull Request description.
Given a git diff and the branch name, write:
1. A concise PR title (max 72 chars, imperative mood)
2. A PR body with sections: ## Summary ## Changes ## Testing
Respond ONLY with JSON: {"title": "...", "body": "..."}"""

def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True).stdout.strip()

def get_current_branch(cwd): return _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
def get_diff_vs_base(cwd, base):
    diff = _git(["diff", f"{base}...HEAD"], cwd)
    return diff[:12_000]

def generate_pr_description(llm, diff, branch, base):
    prompt = f"Branch: {branch}\nMerging into: {base}\n\nGit diff:\n```\n{diff}\n```"
    try:
        response = llm.invoke([SystemMessage(content=_PR_SYSTEM_PROMPT), HumanMessage(content=prompt)])
        raw = re.sub(r"^```(?:json)?\s*", "", response.content.strip())
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception as e:
        title = branch.replace("-", " ").replace("_", " ").replace("/", ": ").title()
        return {"title": title, "body": f"*AI description failed: {e}*"}

def create_pr_gh_cli(title, body, base, cwd):
    result = subprocess.run(["gh", "pr", "create", "--title", title, "--body", body, "--base", base], cwd=str(cwd), capture_output=True, text=True)
    if result.returncode == 0: return result.stdout.strip()
    raise RuntimeError(result.stderr.strip() or "gh pr create failed")

def create_pr_api(title, body, base, head, cwd):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token: raise RuntimeError("No GITHUB_TOKEN found.")
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(cwd), capture_output=True, text=True).stdout.strip()
    repo = remote.replace("git@github.com:", "").replace("https://github.com/", "").rstrip(".git")
    payload = json.dumps({"title": title, "body": body, "head": head, "base": base}).encode()
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/pulls", data=payload, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/vnd.github+json"}, method="POST")
    with urllib.request.urlopen(req) as resp: return json.loads(resp.read())["html_url"]

def open_pr(llm, base, cwd):
    branch = get_current_branch(cwd)
    if branch in (base, "HEAD"):
        console.print(f"[red]❌ Cannot PR from branch '{branch}'. Checkout a feature branch first.[/red]")
        return
    console.print(f"[dim]📡 Diffing [bold]{branch}[/bold] → [bold]{base}[/bold]...[/dim]")
    diff = get_diff_vs_base(cwd, base)
    if not diff.strip():
        console.print("[yellow]⚠️  No changes vs base. Nothing to PR.[/yellow]")
        return
    console.print("[dim]🤖 Generating PR description...[/dim]")
    pr_data = generate_pr_description(llm, diff, branch, base)
    title, body = pr_data.get("title", branch), pr_data.get("body", "")
    console.print(Panel(f"[bold]Title:[/bold] {title}\n\n{body[:600]}", title="[bold cyan]📋 PR Preview[/bold cyan]", border_style="cyan"))
    if not Confirm.ask("Create this PR?", default=True):
        console.print("[dim]Aborted.[/dim]"); return
    console.print("[dim]🚀 Creating PR...[/dim]")
    try:
        url = create_pr_gh_cli(title, body, base, cwd)
    except (FileNotFoundError, RuntimeError):
        try: url = create_pr_api(title, body, base, branch, cwd)
        except Exception as e:
            console.print(f"[red]❌ Could not create PR: {e}[/red]\n[dim]Tip: install gh CLI or set GITHUB_TOKEN.[/dim]"); return
    console.print(Panel(f"[green]✅ PR created![/green]\n\n[link={url}]{url}[/link]", title="[bold green]Pull Request[/bold green]", border_style="green"))
