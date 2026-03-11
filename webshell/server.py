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

PROJECT_ROOT = Path(os.environ.get("AICODER_DIR", ".")).resolve()
AICODER_BIN  = sys.executable   # use same venv Python: python -m aicoder.cli
STATIC_DIR   = Path(__file__).parent / "static"

# In-memory session registry
_sessions: dict[str, dict] = {}   # session_id → {proc, output_lines, done, cancelled}

app = FastAPI(title="AICoder Web Shell", version="0.1.0", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Models ─────────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    instruction: str
    model: str = "deepseek"
    agents: bool = False
    dry_run: bool = False
    interactive: bool = False
    directory: str = str(PROJECT_ROOT)


# ── API routes ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    try:
        from aicoder import __version__
        version = __version__
    except Exception:
        version = "unknown"
    return {"status": "ok", "version": version, "project": str(PROJECT_ROOT)}


@app.get("/api/config")
def get_config():
    config_path = PROJECT_ROOT / ".aicoder.yml"
    memory_path = PROJECT_ROOT / ".aicoder" / "memory.json"
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
    files = []
    try:
        for p in sorted(PROJECT_ROOT.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(PROJECT_ROOT))
                # Skip hidden/build dirs
                if any(part.startswith(".") or part in ("__pycache__", "node_modules", ".venv", "venv") for part in p.parts):
                    continue
                if len(rel) < 200:
                    files.append(rel)
    except Exception as e:
        files = [f"Error: {e}"]
    return {"files": files[:200]}   # cap at 200


@app.post("/api/run")
def start_run(req: RunRequest):
    session_id = str(uuid.uuid4())[:8]

    args = [AICODER_BIN, "-m", "aicoder.cli", req.instruction,
            "--model", req.model, "--dir", req.directory]
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
            cwd=req.directory,
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
    memory_path = PROJECT_ROOT / ".aicoder" / "memory.json"
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
