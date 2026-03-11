from __future__ import annotations
import json, subprocess
from datetime import datetime, timezone
from pathlib import Path
from rich.console import Console
console = Console()
_META_FILE = ".aicoder/last_snapshot.json"
def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
def _is_git_repo(root):
    return _git(["rev-parse", "--show-toplevel"], root).returncode == 0
def _has_uncommitted_changes(root):
    return bool(_git(["status", "--porcelain"], root).stdout.strip())
def _current_branch(root):
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    return r.stdout.strip() if r.returncode == 0 else "unknown"
def _save_meta(root, snap_type, ref):
    p = root / _META_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"type": snap_type, "ref": ref, "branch": _current_branch(root), "timestamp": datetime.now(timezone.utc).isoformat()}, indent=2))
def _load_meta(root):
    p = root / _META_FILE
    try: return json.loads(p.read_text()) if p.exists() else None
    except: return None
def take_snapshot(root):
    root = root.resolve()
    if not _is_git_repo(root):
        console.print("[dim]⚠️  Not a git repo — snapshot skipped.[/dim]")
        return False
    if not _has_uncommitted_changes(root):
        head = _git(["rev-parse", "HEAD"], root).stdout.strip()
        _save_meta(root, "clean", head)
        console.print(f"[dim]📸 Snapshot: clean tree at {head[:8]}[/dim]")
        return True
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    result = _git(["stash", "push", "-u", "-m", f"aicoder-snapshot-{ts}"], root)
    if result.returncode != 0:
        console.print(f"[yellow]⚠️  Could not create snapshot: {result.stderr.strip()}[/yellow]")
        return False
    lr = _git(["stash", "list", "--max-count=1"], root)
    stash_ref = lr.stdout.split(":")[0].strip() if lr.stdout else "stash@{0}"
    _save_meta(root, "stash", stash_ref)
    console.print(f"[dim]📸 Snapshot: stash saved as {stash_ref}[/dim]")
    return True
def rollback(root):
    root = root.resolve()
    meta = _load_meta(root)
    if not meta:
        console.print("[yellow]⚠️  No snapshot found.[/yellow]")
        return False
    console.print(f"[cyan]🔄 Rolling back to snapshot from {meta.get(chr(116)+chr(105)+chr(109)+chr(101)+chr(115)+chr(116)+chr(97)+chr(109)+chr(112), chr(63))} (branch: {meta.get(chr(98)+chr(114)+chr(97)+chr(110)+chr(99)+chr(104))})[/cyan]")
    snap_type = meta.get("type")
    ref = meta.get("ref", "")
    if snap_type == "stash":
        result = _git(["stash", "pop", ref], root)
        ok = result.returncode == 0
        if ok: (root / _META_FILE).unlink(missing_ok=True)
        console.print("[green]✅ Rollback complete.[/green]" if ok else f"[red]❌ Stash pop failed: {result.stderr.strip()}[/red]")
        return ok
    elif snap_type == "clean":
        result = _git(["reset", "--hard", ref], root)
        ok = result.returncode == 0
        if ok: (root / _META_FILE).unlink(missing_ok=True)
        console.print("[green]✅ Rollback complete.[/green]" if ok else f"[red]❌ Reset failed.[/red]")
        return ok
    return False
def snapshot_info(root):
    meta = _load_meta(root.resolve())
    if not meta: return ""
    return f"Last snapshot: {meta.get(chr(116)+chr(121)+chr(112)+chr(101))} @ {meta.get(chr(114)+chr(101)+chr(102), chr(63))[:8]}  branch={meta.get(chr(98)+chr(114)+chr(97)+chr(110)+chr(99)+chr(104))}  time={meta.get(chr(116)+chr(105)+chr(109)+chr(101)+chr(115)+chr(116)+chr(97)+chr(109)+chr(112), chr(63))[:19]}"
