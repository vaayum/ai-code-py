from __future__ import annotations
from pathlib import Path

def generate_project_summary(llm, project_root: Path) -> str:
    """Generate a fast 1-line project summary using the LLM."""
    from langchain_core.messages import HumanMessage
    
    files = [f.name for f in project_root.iterdir() if f.is_file() and not f.name.startswith('.')]
    readme = ""
    for name in ["README.md", "README.txt", "readme.md"]:
        p = project_root / name
        if p.exists():
            readme = p.read_text(errors="replace")[:1000]
            break
            
    prompt = (
        "You are an expert developer. Look at this codebase and write a single-sentence "
        "summary of what this project is. Start with a relevant emoji. "
        "Keep it under 10 words if possible.\n\n"
        f"Top-level files: {', '.join(files)}\n\n"
    )
    if readme:
        prompt += f"README snippet:\n{readme}\n\n"
        
    prompt += "Expected output format: '📋 Django REST API for e-commerce' or '⚛️ React frontend dashboard'. Respond with ONLY the single sentence summary."
    
    try:
        res = llm.invoke([HumanMessage(content=prompt)])
        content = res.content.strip() if hasattr(res, "content") else str(res).strip()
        # Remove quotes if the LLM added them
        content = content.replace('"', "").replace("'", "")
        return content
    except Exception:
        return ""
