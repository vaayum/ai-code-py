"""
AICoder Web Shell — FastAPI backend.

Endpoints:
  GET  /              → serves index.html
  GET  /api/health    → {"status": "ok", "version": ...}
  GET  /api/config    → current project config info
  POST /api/run       → start an agent run, returns a session_id
  GET  /api/stream/{session_id}  → SSE stream of agent output
  POST /api/cancel/{session_id}  → cancel a running session
  GET  /api/history   → list of past runs from memory
  GET  /api/files     → list project files
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Iterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Config ─────────────────────────────────────────────────────────────────────

# Mutable server state — project root can be switched at runtime via API
_state: dict = {
    "project_root": Path(os.environ.get("AICODER_DIR", ".")).resolve(),
    "recent_projects": [],   # list of recently used project paths
}
PROJECT_ROOT = _state["project_root"]   # kept for backward compat on boot
AICODER_BIN  = sys.executable
STATIC_DIR   = Path(__file__).parent / "static"

# In-memory session registry
_sessions: dict[str, dict] = {}   # session_id → {proc, output_lines, done, cancelled}

def _current_root() -> Path:
    return _state["project_root"]

app = FastAPI(title="AICoder Web Shell", version="0.1.0", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_header(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# ── Models ─────────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    instruction: str
    model: str = "deepseek"
    agents: bool = False
    dry_run: bool = False
    interactive: bool = False
    directory: str = ""  # empty = use current project root


class SwitchProjectRequest(BaseModel):
    path: str


# ── API routes ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    try:
        from aicoder import __version__
        version = __version__
    except Exception:
        version = "unknown"
    return {"status": "ok", "version": version, "project": str(_current_root())}


@app.get("/api/project")
def get_project():
    """Return current project root and recent projects list."""
    root = _current_root()
    return {
        "path": str(root),
        "name": root.name,
        "recent": _state["recent_projects"],
    }


@app.post("/api/project/switch")
def switch_project(req: SwitchProjectRequest):
    """Switch the active project to the given path."""
    new_root = Path(req.path).resolve()
    if not new_root.exists():
        raise HTTPException(status_code=400, detail=f"Path does not exist: {new_root}")
    if not new_root.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {new_root}")
    _state["project_root"] = new_root
    # Track recent projects (keep last 8, dedup)
    recent = _state["recent_projects"]
    entry = str(new_root)
    if entry in recent:
        recent.remove(entry)
    recent.insert(0, entry)
    _state["recent_projects"] = recent[:8]
    return {"status": "ok", "project": str(new_root), "name": new_root.name}


@app.get("/api/browse")
def browse_dir(path: str = ""):
    """
    List subdirectories + parent inside `path` (default: home dir).
    Used by the frontend directory picker.
    """
    if path:
        target = Path(path).resolve()
    else:
        target = Path.home()
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    dirs = []
    try:
        for child in sorted(target.iterdir()):
            if child.is_dir() and not child.name.startswith("."):
                # Flag whether it looks like a code project
                markers = {"package.json", "pyproject.toml", "setup.py", "Cargo.toml", "go.mod", ".git", "pom.xml"}
                is_project = any((child / m).exists() for m in markers)
                dirs.append({"name": child.name, "path": str(child), "is_project": is_project})
    except PermissionError:
        pass
    parent = str(target.parent) if target != target.parent else None
    return {"current": str(target), "parent": parent, "dirs": dirs}


@app.get("/api/config")
def get_config():
    root = _current_root()
    config_path = root / ".aicoder.yml"
    memory_path = root / ".aicoder" / "memory.json"
    try:
        import yaml
        cfg = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    except Exception:
        cfg = {}
    try:
        memory = json.loads(memory_path.read_text()) if memory_path.exists() else {}
    except Exception:
        memory = {}
    return {
        "config_exists": config_path.exists(),
        "provider": cfg.get("provider", "deepseek"),
        "interactive": cfg.get("agent", {}).get("interactive", False),
        "project_summary": memory.get("project_summary", ""),
        "recent_actions": memory.get("recent_actions", [])[-5:],
    }


@app.get("/api/files")
def list_files():
    root = _current_root()
    files = []
    try:
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(root))
                # Check only relative parts so we don't accidentally match parent hidden dirs (e.g. .gemini)
                if any(part.startswith(".") or part in ("__pycache__", "node_modules", ".venv", "venv") for part in Path(rel).parts):
                    continue
                if len(rel) < 200:
                    files.append(rel)
    except Exception as e:
        files = [f"Error: {e}"]
    return {"files": files[:200], "root": str(root)}


@app.post("/api/run")
def start_run(req: RunRequest):
    session_id = str(uuid.uuid4())[:8]
    work_dir = req.directory if req.directory else str(_current_root())

    args = [AICODER_BIN, "-m", "aicoder.cli", req.instruction,
            "--model", req.model, "--dir", work_dir]
    if req.agents:
        args.append("--agents")
    if req.dry_run:
        args.append("--dry-run")

    env = {**os.environ, "FORCE_COLOR": "0", "NO_COLOR": "1", "TERM": "dumb"}

    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            cwd=work_dir,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    _sessions[session_id] = {
        "proc": proc,
        "output_lines": [],
        "done": False,
        "cancelled": False,
    }
    return {"session_id": session_id}


@app.get("/api/stream/{session_id}")
def stream_output(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    def event_generator() -> Iterator[str]:
        proc = session["proc"]
        yield f"data: {json.dumps({'type': 'start', 'session_id': session_id})}\n\n"

        for line in proc.stdout:
            if session.get("cancelled"):
                proc.kill()
                yield f"data: {json.dumps({'type': 'cancelled'})}\n\n"
                break
            session["output_lines"].append(line)
            yield f"data: {json.dumps({'type': 'line', 'text': line})}\n\n"

        proc.wait()
        session["done"] = True
        exit_code = proc.returncode
        yield f"data: {json.dumps({'type': 'done', 'exit_code': exit_code})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/cancel/{session_id}")
def cancel_run(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session["cancelled"] = True
    try:
        session["proc"].terminate()
    except Exception:
        pass
    return {"status": "cancel_requested"}


@app.get("/api/history")
def get_history():
    root = _current_root()
    memory_path = root / ".aicoder" / "memory.json"
    try:
        memory = json.loads(memory_path.read_text()) if memory_path.exists() else {}
        return {"actions": memory.get("recent_actions", [])}
    except Exception:
        return {"actions": []}



# ── Serve static frontend ───────────────────────────────────────────────────────

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/{full_path:path}")
def serve_spa(full_path: str):
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "AICoder Web Shell API — visit /api/docs for Swagger UI"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8765))
    print(f"\n🌐  AICoder Web Shell running at http://localhost:{port}\n")
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
