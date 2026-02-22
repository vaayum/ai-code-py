"""Configuration management — YAML + env vars + Pydantic validation."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


# ── Config schema ────────────────────────────────────────────────────────────

class AgentConfig(BaseModel):
    max_retries: int = 3
    temperature: float = 0.1
    max_tokens: int = 4096


class AiCoderConfig(BaseModel):
    provider: Literal["openai", "anthropic", "deepseek", "ollama"] = "deepseek"
    model: str = ""
    agent: AgentConfig = Field(default_factory=AgentConfig)


def load_config(path: Path | None = None) -> AiCoderConfig:
    """Load config from .aicoder.yml in the project root (or the given path)."""
    candidates = [path] if path else [
        Path.cwd() / ".aicoder.yml",
        Path.cwd() / ".aicoder.yaml",
        Path.home() / ".aicoder.yml",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            raw = yaml.safe_load(candidate.read_text())
            return AiCoderConfig.model_validate(raw or {})
    return AiCoderConfig()


# ── LLM factory ──────────────────────────────────────────────────────────────

def create_llm(provider: str, config: AiCoderConfig):
    """
    Create a LangChain chat model for the given provider.
    All providers support streaming natively.
    """
    temp = config.agent.temperature
    max_tok = config.agent.max_tokens

    match provider.lower():
        case "openai":
            from langchain_openai import ChatOpenAI
            key = _require_env("OPENAI_API_KEY")
            model = config.model or "gpt-4o"
            return ChatOpenAI(api_key=key, model=model, temperature=temp, max_tokens=max_tok)

        case "anthropic":
            from langchain_anthropic import ChatAnthropic
            key = _require_env("ANTHROPIC_API_KEY")
            model = config.model or "claude-opus-4-5"
            return ChatAnthropic(api_key=key, model_name=model, temperature=temp, max_tokens=max_tok)

        case "deepseek":
            from langchain_openai import ChatOpenAI
            key = _require_env("DEEPSEEK_API_KEY")
            model = config.model or "deepseek-chat"
            return ChatOpenAI(
                api_key=key,
                base_url="https://api.deepseek.com",
                model=model,
                temperature=temp,
                max_tokens=max_tok,
            )

        case "ollama":
            from langchain_ollama import ChatOllama
            model = config.model or "llama3.1"
            return ChatOllama(model=model, temperature=temp)

        case _:
            raise ValueError(f"Unknown provider: {provider}. Use: openai, anthropic, deepseek, ollama")


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(f"❌ {name} not set. Export it with: export {name}=your-key")
    return val
