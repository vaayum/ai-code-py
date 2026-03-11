"""Configuration management — YAML + env vars + Pydantic validation.

Supports two deployment modes:

**open** (default, cloud BYOK)::

    provider: deepseek           # openai | anthropic | deepseek | ollama
    model: deepseek-chat
    agent:
      max_retries: 3
      temperature: 0.1
      max_tokens: 4096

**enterprise** (on-premise / airgapped LLM)::

    mode: enterprise
    enterprise:
      base_url: https://llm.corp.internal/v1
      model: codellama-70b
      api_key: ${CORP_LLM_TOKEN}   # env-var expansion supported
      tls_verify: true             # false disables cert verification (dev only)
      ca_bundle: /etc/ssl/corp-ca.pem  # path to PEM CA bundle
      proxy_url: http://proxy.corp:3128  # optional HTTP/S proxy
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field

from aicoder.enterprise_auth import (
    AuthPluginConfig, EnterpriseAuthPlugin,
    EnvVarAuth, StaticKeyAuth, TokenEndpointAuth, WhlModuleAuth,
    plugin_from_dict,
)


# ── Config schema ────────────────────────────────────────────────────────────

class AgentConfig(BaseModel):
    max_retries: int = 3
    temperature: float = 0.1
    max_tokens: int = 4096
    interactive: bool = False


class EnterpriseConfig(BaseModel):
    """On-premise LLM configuration (OpenAI-compatible API)."""
    base_url: str = ""                           # e.g. https://llm.corp.internal/v1
    model: str = ""                              # e.g. codellama-70b, mistral-7b
    tls_verify: bool = True                      # set False for dev/self-signed
    ca_bundle: Optional[str] = None              # path to custom PEM bundle
    proxy_url: Optional[str] = None              # e.g. http://proxy:3128

    # ── Auth (choose one) ─────────────────────────────────────────────────
    api_key: str = ""                            # static key shorthand
    auth_strategy: Optional[dict] = None         # generic typed strategy (see enterprise_auth.py)
    auth_plugin: Optional[AuthPluginConfig] = None  # legacy whl_module shorthand

    def has_proxy(self) -> bool:
        return bool(self.proxy_url)

    def has_custom_tls(self) -> bool:
        return bool(self.ca_bundle) or not self.tls_verify

    def has_auth_strategy(self) -> bool:
        """True when a typed auth_strategy or legacy auth_plugin is configured."""
        if self.auth_strategy:
            return True
        return self.auth_plugin is not None and self.auth_plugin.is_configured()

    # kept for backward compat
    def has_auth_plugin(self) -> bool:
        return self.has_auth_strategy()


class AiCoderConfig(BaseModel):
    mode: Literal["open", "enterprise"] = "open"
    provider: str = "deepseek"               # only used in open mode
    model: str = ""
    agent: AgentConfig = Field(default_factory=AgentConfig)
    enterprise: Optional[EnterpriseConfig] = None


_ENV_RE = re.compile(r"\$\{([^}]+)}")


def _expand(value: str | None) -> str | None:
    """Expand ${ENV_VAR} placeholders in config values."""
    if not value:
        return value
    return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def load_config(path: Path | None = None) -> AiCoderConfig:
    """Load config from .aicoder.yml in the project root (or the given path)."""
    candidates = [path] if path else [
        Path.cwd() / ".aicoder.yml",
        Path.cwd() / ".aicoder.yaml",
        Path.home() / ".aicoder.yml",
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            raw = yaml.safe_load(candidate.read_text()) or {}
            # Expand env vars in enterprise block
            if "enterprise" in raw and isinstance(raw["enterprise"], dict):
                ent = raw["enterprise"]
                for key in ("base_url", "api_key", "ca_bundle", "proxy_url"):
                    if key in ent:
                        ent[key] = _expand(ent[key])
            return AiCoderConfig.model_validate(raw)
    return AiCoderConfig()


# ── Model catalog ────────────────────────────────────────────────────────────
# Curated recommendations for an agentic coding tool.
# Priority: reliable tool-use > large context > code quality > cost

MODEL_CATALOG = {
    "byok": [
        # id, provider, env_var, display_name, note
        ("claude-opus-4-6",              "anthropic", "ANTHROPIC_API_KEY", "Claude Opus 4.6",       "⭐ Best for agents & coding — most intelligent"),
        ("claude-sonnet-4-6",            "anthropic", "ANTHROPIC_API_KEY", "Claude Sonnet 4.6",     "Best speed+intelligence balance — recommended default"),
        ("claude-haiku-4-5-20251001",    "anthropic", "ANTHROPIC_API_KEY", "Claude Haiku 4.5",     "Fastest Claude — budget/high-volume use"),
        ("claude-3-7-sonnet-20250219",   "anthropic", "ANTHROPIC_API_KEY", "Claude 3.7 Sonnet",    "Extended thinking, previous gen stable option"),
        ("deepseek-chat",                "deepseek",  "DEEPSEEK_API_KEY",  "DeepSeek V3",          "💰 10× cheaper than OpenAI, excellent code quality"),
        ("deepseek-reasoner",            "deepseek",  "DEEPSEEK_API_KEY",  "DeepSeek R1 (Reasoner)","Chain-of-thought — for hard bugs and complex refactors"),
        ("gpt-4o",                       "openai",    "OPENAI_API_KEY",    "GPT-4o",               "Fast, bulletproof tool calling, 128K ctx"),
        ("gpt-4o-mini",                  "openai",    "OPENAI_API_KEY",    "GPT-4o mini",          "Budget OpenAI option — fast and cheap"),
        ("o3-mini",                      "openai",    "OPENAI_API_KEY",    "o3-mini (reasoning)",  "Best for audit/debugging chains"),
        ("gemini-2.0-flash",             "gemini",    "GOOGLE_API_KEY",    "Gemini 2.0 Flash",     "1M token context — great for whole-codebase analysis"),
        ("gemini-2.0-pro",               "gemini",    "GOOGLE_API_KEY",    "Gemini 2.0 Pro",       "Best Gemini for code, strong reasoning"),
    ],
    "local": [
        # id (ollama pull name), min_vram_gb, display_name, note
        ("qwen2.5-coder:32b",    24, "Qwen2.5-Coder 32B",      "⭐ Best local coding model — beats many cloud models"),
        ("qwen2.5-coder:7b",      6, "Qwen2.5-Coder 7B",       "Consumer GPU (RTX 3080) — great tool-use"),
        ("llama3.1:70b",         48, "Llama 3.1 70B",           "Best general-purpose local, 128K ctx, excellent tool-use"),
        ("llama3.2:3b",           4, "Llama 3.2 3B",            "Ultra-low resource — for CI/hobbyist use"),
        ("deepseek-coder-v2:16b",12, "DeepSeek-Coder-V2 16B",  "Strong coder, runs on 12GB VRAM"),
        ("mistral:7b",            6, "Mistral 7B",              "General fallback, wide hardware support"),
    ],
}


# ── LLM factory ──────────────────────────────────────────────────────────────

def create_llm(provider: str, config: AiCoderConfig):
    """
    Create a LangChain chat model for the given provider.
    All providers support streaming natively.
    """
    temp = config.agent.temperature
    max_tok = config.agent.max_tokens

    # Enterprise mode: on-premise OpenAI-compatible endpoint
    if config.mode == "enterprise" or provider.lower() == "enterprise":
        return _create_enterprise_llm(config, temp, max_tok)

    match provider.lower():
        case "openai":
            from langchain_openai import ChatOpenAI
            key = _require_env("OPENAI_API_KEY")
            model = config.model or "gpt-4o"
            return ChatOpenAI(api_key=key, model=model, temperature=temp, max_tokens=max_tok)

        case "anthropic":
            from langchain_anthropic import ChatAnthropic
            key = _require_env("ANTHROPIC_API_KEY")
            model = config.model or "claude-sonnet-4-6"
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

        case "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI
            key = _require_env("GOOGLE_API_KEY")
            model = config.model or "gemini-2.0-flash"
            return ChatGoogleGenerativeAI(
                google_api_key=key,
                model=model,
                temperature=temp,
                max_output_tokens=max_tok,
            )

        case "ollama":
            from langchain_ollama import ChatOllama
            model = config.model or "qwen2.5-coder:7b"
            return ChatOllama(model=model, temperature=temp)

        case _:
            raise ValueError(
                f"Unknown provider: {provider!r}.\n"
                f"Supported: openai, anthropic, deepseek, gemini, ollama, enterprise\n"
                f"Run 'aicoder models' to see recommended options."
            )



def _create_enterprise_llm(config: AiCoderConfig, temp: float, max_tok: int):
    """Create an LLM for on-premise / enterprise deployments.

    Auth resolution order:
      1. auth_strategy dict  → routed through plugin_from_dict (all 4 strategies)
      2. auth_plugin block   → legacy WhlModuleAuth (backward compat)
      3. api_key / ENTERPRISE_LLM_KEY env var  → simple static key
    """
    from langchain_openai import ChatOpenAI

    ent = config.enterprise
    if ent is None:
        ent = EnterpriseConfig()

    base_url = ent.base_url or _require_env("ENTERPRISE_LLM_URL")
    model    = ent.model    or config.model or _require_env("ENTERPRISE_LLM_MODEL")

    print(f"🏢 Enterprise LLM: {base_url} | model={model}")

    # ── Resolve auth ────────────────────────────────────────────────────────
    if ent.auth_strategy:
        # Generic typed strategy — env_var / static_key / token_endpoint / whl_module
        plugin = plugin_from_dict(ent.auth_strategy)
        api_key = plugin.get_token()
        http_client = plugin.build_httpx_client(
            verify=ent.ca_bundle if ent.ca_bundle else ent.tls_verify,
            proxy_url=ent.proxy_url,
        )

    elif ent.auth_plugin and ent.auth_plugin.is_configured():
        # Legacy whl_module shorthand (backward compat)
        plugin = EnterpriseAuthPlugin(ent.auth_plugin)
        plugin.setup()
        api_key = plugin.get_token()
        http_client = plugin.build_httpx_client(
            verify=ent.ca_bundle if ent.ca_bundle else ent.tls_verify,
            proxy_url=ent.proxy_url,
        )

    else:
        # Static key fallback
        api_key     = ent.api_key or os.environ.get("ENTERPRISE_LLM_KEY", "none")
        http_client = _build_http_client(ent)

    kwargs: dict = dict(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=temp,
        max_tokens=max_tok,
    )
    if http_client is not None:
        kwargs["http_client"] = http_client

    return ChatOpenAI(**kwargs)



def _build_http_client(ent: EnterpriseConfig):
    """Return a custom httpx.Client for proxy / TLS settings, or None."""
    try:
        import httpx
    except ImportError:
        # httpx is not installed — skip custom client
        return None

    needs_custom = ent.has_proxy() or ent.has_custom_tls()
    if not needs_custom:
        return None

    kwargs: dict = {}

    # TLS verification
    if not ent.tls_verify:
        import warnings
        warnings.warn(
            "⚠️  TLS verification DISABLED — insecure, use only in dev/staging",
            stacklevel=3,
        )
        kwargs["verify"] = False
    elif ent.ca_bundle:
        ca_path = Path(ent.ca_bundle).resolve()
        if not ca_path.exists():
            raise FileNotFoundError(f"CA bundle not found: {ca_path}")
        kwargs["verify"] = str(ca_path)

    # Proxy
    if ent.proxy_url:
        kwargs["proxies"] = {
            "http://": ent.proxy_url,
            "https://": ent.proxy_url,
        }

    return httpx.Client(**kwargs)


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(f"❌ {name} not set. Export it with: export {name}=your-value")
    return val
