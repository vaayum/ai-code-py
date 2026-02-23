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

from aicoder.enterprise_auth import AuthPluginConfig, EnterpriseAuthPlugin


# ── Config schema ────────────────────────────────────────────────────────────

class AgentConfig(BaseModel):
    max_retries: int = 3
    temperature: float = 0.1
    max_tokens: int = 4096


class EnterpriseConfig(BaseModel):
    """On-premise LLM configuration (OpenAI-compatible API)."""
    base_url: str = ""                          # e.g. https://llm.corp.internal/v1
    model: str = ""                             # e.g. codellama-70b, mistral-7b
    api_key: str = ""                           # internal API token (if static)
    tls_verify: bool = True                     # set False for dev/self-signed
    ca_bundle: Optional[str] = None             # path to custom PEM bundle
    proxy_url: Optional[str] = None             # e.g. http://proxy:3128
    auth_plugin: Optional[AuthPluginConfig] = None  # dynamic .whl auth plugin

    def has_proxy(self) -> bool:
        return bool(self.proxy_url)

    def has_custom_tls(self) -> bool:
        return bool(self.ca_bundle) or not self.tls_verify

    def has_auth_plugin(self) -> bool:
        return self.auth_plugin is not None and self.auth_plugin.is_configured()


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
            raise ValueError(
                f"Unknown provider: {provider}. Use: openai, anthropic, deepseek, ollama, enterprise"
            )


def _create_enterprise_llm(config: AiCoderConfig, temp: float, max_tok: int):
    """Create an LLM for on-premise / enterprise deployments."""
    from langchain_openai import ChatOpenAI

    ent = config.enterprise
    if ent is None:
        ent = EnterpriseConfig()

    base_url = ent.base_url or _require_env("ENTERPRISE_LLM_URL")
    model    = ent.model    or config.model or _require_env("ENTERPRISE_LLM_MODEL")

    # ── Auth plugin (e.g. rbc_security + rbc_auth) ─────────────────────────
    if ent.has_auth_plugin():
        plugin = EnterpriseAuthPlugin(ent.auth_plugin)
        plugin.setup()               # run enable_certs(force=True) etc.
        api_key = plugin.get_token() # acquire initial bearer token
        http_client = plugin.build_httpx_client(
            verify=ent.ca_bundle if ent.ca_bundle else ent.tls_verify,
            proxy_url=ent.proxy_url,
        )
    else:
        # Static API key mode (fall back to env var)
        api_key     = ent.api_key or os.environ.get("ENTERPRISE_LLM_KEY", "none")
        http_client = _build_http_client(ent)

    print(f"🏢 Enterprise LLM: {base_url} | model={model}")

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
