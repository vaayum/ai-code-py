from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage, HumanMessage
import json

ROUTER_PROMPT = """You are the AICoder Request Router. 
Your job is to read a developer's request and decide whether it should be handled by a single agent or a full multi-agent team.

CRITERIA FOR SINGLE_AGENT:
- Simple Q&A or explanation (e.g. "What does auth.py do?")
- Targeted bug fixes in a known location (e.g. "Fix the null check on line 42")
- Small scope refactoring limited to 1 or 2 files.
- Command generation or simple file reads.

CRITERIA FOR MULTI_AGENT:
- Large structural changes ("Migrate frontend from React to Vue")
- Implementing whole new features spanning multiple layers (DB, API, UI)
- Complex debugging where root cause is unknown.
- Vague instructions requiring extensive planning before execution.

Output ONLY a JSON object exactly matching this schema:
{
    "mode": "SINGLE_AGENT" | "MULTI_AGENT",
    "reasoning": "<1 sentence explaining why>"
}
"""

def route_instruction(llm: BaseChatModel, instruction: str) -> dict:
    """
    Decides whether an instruction should use SINGLE_AGENT or MULTI_AGENT mode.
    Returns:
        dict: {"mode": "...", "reasoning": "..."}
    """
    try:
        response = llm.invoke([
            SystemMessage(content=ROUTER_PROMPT),
            HumanMessage(content=instruction)
        ]).content.strip()
        
        # Parse JSON
        # Clean up markdown code blocks if present
        if response.startswith("```json"):
            response = response[7:-3].strip()
        elif response.startswith("```"):
            response = response[3:-3].strip()
            
        return json.loads(response)
    except Exception as e:
        # Default to single agent on failure to save compute
        return {"mode": "SINGLE_AGENT", "reasoning": f"Routing failed ({e}), defaulting to single agent for safety."}
