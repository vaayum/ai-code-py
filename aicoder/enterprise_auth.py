"""Enterprise Auth Plugin — universal pluggable authentication for on-premise corporate LLMs.

Every enterprise LLM deployment follows the same two-step pattern:
  1. Acquire a bearer token (using one of several strategies)
  2. Send that token as ``Authorization: Bearer <token>`` to your hosted LLM endpoint

Supported auth strategies (``auth_strategy`` field in ``.aicoder.yml``)::

  env_var         – token already set in an environment variable (simplest)
  static_key      – literal API key in config / env var (same as env_var, explicit)
  token_endpoint  – call an HTTP endpoint (OAuth2 / custom auth API) to get a token
  whl_module      – import a corporate .whl (e.g. corp_security) and call its auth function

Full YAML examples — see ``example_configs()`` below or run ``aicoder enterprise-init``.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import threading
import time
from typing import Any, Callable, Generator, Literal, Optional

from pydantic import BaseModel


# ── Auth strategy configs (discriminated union) ────────────────────────────────

class EnvVarAuth(BaseModel):
    """Use a token that is already in an environment variable."""
    strategy: Literal["env_var"] = "env_var"
    token_env: str                        # env var name, e.g. "CORP_LLM_TOKEN"
    header_name: str = "Authorization"   # header to inject
    header_prefix: str = "Bearer"        # e.g. "Bearer" or "Token" or ""


class StaticKeyAuth(BaseModel):
    """A literal API key from config or env var."""
    strategy: Literal["static_key"] = "static_key"
    api_key: str = ""                     # literal key, or use ${ENV_VAR} expansion
    header_name: str = "Authorization"
    header_prefix: str = "Bearer"


class TokenEndpointAuth(BaseModel):
    """
    Call an HTTP endpoint to obtain a bearer token (OAuth2 / custom auth API).

    Examples that fit this pattern:
    - OAuth2 client_credentials flow
    - Azure AD / Okta token endpoints
    - Any internal auth service that returns a JSON token
    """
    strategy: Literal["token_endpoint"] = "token_endpoint"
    url: str                              # e.g. https://auth.corp.internal/oauth/token
    method: Literal["POST", "GET"] = "POST"
    payload: dict = {}                    # request body – supports ${ENV_VAR} expansion
    headers: dict = {}                    # extra request headers
    token_path: str = "access_token"      # dot-path into JSON response, e.g. "data.token"
    token_ttl_seconds: Optional[int] = 3600   # cache duration (None = fetch every call)
    refresh_on_401: bool = True


class WhlModuleAuth(BaseModel):
    """
    Import a corporate Python module (from pip-installed .whl) and call its auth function.

    Example: corporate pattern — corp_security.enable_certs() + corp_auth.get_auth_token()
    """
    strategy: Literal["whl_module"] = "whl_module"
    # Setup phase (optional) — e.g. enable_certs
    setup_module: Optional[str] = None
    setup_func: Optional[str] = None
    setup_kwargs: dict = {}
    # Token acquisition
    token_module: str
    token_func: str
    token_kwargs: dict = {}
    token_ttl_seconds: Optional[int] = None
    refresh_on_401: bool = True


# Back-compat alias — keep existing YAML configs working
class AuthPluginConfig(WhlModuleAuth):
    """Legacy name for WhlModuleAuth (kept for backward compatibility)."""
    strategy: Literal["whl_module"] = "whl_module"

    def is_configured(self) -> bool:
        return bool(self.token_module and self.token_func)


# Union type for config validation
AuthStrategy = EnvVarAuth | StaticKeyAuth | TokenEndpointAuth | WhlModuleAuth | AuthPluginConfig


# ── Env-var expansion ─────────────────────────────────────────────────────────

_ENV_RE = re.compile(r"\$\{([^}]+)}")


def _expand(value: str) -> str:
    return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def _expand_dict(d: dict) -> dict:
    return {k: _expand(str(v)) if isinstance(v, str) else v for k, v in d.items()}


# ── Runtime plugin ────────────────────────────────────────────────────────────

class EnterpriseAuthPlugin:
    """
    Runtime token provider.  Instantiate with any AuthStrategy config and call:

    - ``setup()``       → run one-time initialisation (cert injection etc.)
    - ``get_token()``   → returns a fresh (or cached) bearer token string
    - ``build_httpx_client()`` → an httpx.Client that injects the token on every request
    """

    def __init__(self, cfg: AuthStrategy) -> None:
        self._cfg = cfg
        self._token: Optional[str] = None
        self._token_acquired_at: float = 0.0
        self._lock = threading.Lock()
        self._setup_done = False

    # ── setup ──────────────────────────────────────────────────────────────

    def setup(self) -> None:
        """One-time setup (cert injection etc.). Safe to call repeatedly."""
        if self._setup_done:
            return
        cfg = self._cfg
        if isinstance(cfg, (WhlModuleAuth, AuthPluginConfig)):
            if cfg.setup_module and cfg.setup_func:
                try:
                    mod = importlib.import_module(cfg.setup_module)
                except ModuleNotFoundError as e:
                    raise RuntimeError(
                        f"❌ Auth setup module '{cfg.setup_module}' not found.\n"
                        f"   pip install the corporate .whl first.\n   {e}"
                    ) from e
                func: Callable = getattr(mod, cfg.setup_func)
                print(f"🔐 {cfg.setup_module}.{cfg.setup_func}({cfg.setup_kwargs})")
                func(**cfg.setup_kwargs)
                print("✅ Corporate TLS certs configured.")
        self._setup_done = True

    # ── get token ─────────────────────────────────────────────────────────

    def get_token(self, force_refresh: bool = False) -> str:
        """Thread-safe. Returns a valid bearer token string."""
        with self._lock:
            ttl = getattr(self._cfg, "token_ttl_seconds", None)
            if not force_refresh and self._token and ttl:
                if time.time() - self._token_acquired_at < ttl:
                    return self._token
            token = self._acquire_token()
            if not isinstance(token, str) or not token.strip():
                raise RuntimeError(f"❌ Auth returned an empty/invalid token: {token!r}")
            self._token = token
            self._token_acquired_at = time.time()
            return token

    def _acquire_token(self) -> str:
        cfg = self._cfg
        strategy = cfg.strategy

        if strategy == "env_var":
            token = os.environ.get(cfg.token_env, "")
            if not token:
                raise RuntimeError(
                    f"❌ Environment variable '{cfg.token_env}' is not set.\n"
                    f"   Set it with: export {cfg.token_env}=<your-token>"
                )
            return token

        elif strategy == "static_key":
            key = _expand(cfg.api_key) if cfg.api_key else ""
            if not key:
                raise RuntimeError("❌ static_key auth: api_key is empty.")
            return key

        elif strategy == "token_endpoint":
            return self._call_token_endpoint(cfg)

        elif strategy == "whl_module":
            return self._call_whl_module(cfg)

        else:
            raise RuntimeError(f"❌ Unknown auth strategy: {strategy!r}")

    def _call_token_endpoint(self, cfg: TokenEndpointAuth) -> str:
        try:
            import httpx
        except ImportError as e:
            raise ImportError("pip install httpx to use token_endpoint auth") from e

        url = _expand(cfg.url)
        payload = _expand_dict(cfg.payload)
        headers = _expand_dict(cfg.headers)

        print(f"🔑 Fetching token from: {url}")
        if cfg.method == "POST":
            resp = httpx.post(url, json=payload, headers=headers, timeout=30)
        else:
            resp = httpx.get(url, params=payload, headers=headers, timeout=30)

        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"❌ Token endpoint returned HTTP {resp.status_code}:\n{resp.text[:400]}"
            )

        data = resp.json()
        # Traverse dot-path: "data.access_token" → data["data"]["access_token"]
        token = data
        for part in cfg.token_path.split("."):
            if isinstance(token, dict):
                token = token.get(part)
            else:
                token = None
                break

        if not token:
            raise RuntimeError(
                f"❌ Could not find '{cfg.token_path}' in token endpoint response:\n"
                f"{json.dumps(data, indent=2)[:400]}"
            )
        print("✅ Token acquired from endpoint.")
        return str(token)

    def _call_whl_module(self, cfg: WhlModuleAuth) -> str:
        try:
            mod = importlib.import_module(cfg.token_module)
        except ModuleNotFoundError as e:
            raise RuntimeError(
                f"❌ Token module '{cfg.token_module}' not found. pip install the .whl.\n{e}"
            ) from e
        func: Callable = getattr(mod, cfg.token_func)
        print(f"🔑 {cfg.token_module}.{cfg.token_func}()")
        return func(**cfg.token_kwargs)

    # ── build httpx client ─────────────────────────────────────────────────

    def build_httpx_client(
        self,
        verify: bool | str = True,
        proxy_url: Optional[str] = None,
    ):
        """
        Returns an ``httpx.Client`` with:
        - ``Authorization: Bearer <token>`` injected on every request
        - Automatic token refresh on HTTP 401
        """
        try:
            import httpx
        except ImportError as e:
            raise ImportError("pip install httpx") from e

        cfg = self._cfg
        header_name   = getattr(cfg, "header_name",   "Authorization")
        header_prefix = getattr(cfg, "header_prefix",  "Bearer")
        refresh        = getattr(cfg, "refresh_on_401", True)

        auth = _BearerTokenAuth(
            plugin=self,
            header_name=header_name,
            header_prefix=header_prefix,
            refresh_on_401=refresh,
        )
        kwargs: dict[str, Any] = {"auth": auth}
        if verify is not True:
            kwargs["verify"] = verify
        if proxy_url:
            kwargs["proxies"] = {"http://": proxy_url, "https://": proxy_url}
        return httpx.Client(**kwargs)


# ── httpx Auth ────────────────────────────────────────────────────────────────

class _BearerTokenAuth:
    def __init__(
        self,
        plugin: EnterpriseAuthPlugin,
        header_name: str = "Authorization",
        header_prefix: str = "Bearer",
        refresh_on_401: bool = True,
    ) -> None:
        self._plugin = plugin
        self._header_name = header_name
        self._header_prefix = header_prefix
        self._refresh_on_401 = refresh_on_401

    def auth_flow(self, request) -> Generator:
        token = self._plugin.get_token()
        prefix = f"{self._header_prefix} " if self._header_prefix else ""
        request.headers[self._header_name] = f"{prefix}{token}"
        response = yield request
        if response.status_code == 401 and self._refresh_on_401:
            print("⚠️  401 — refreshing auth token and retrying…")
            new_token = self._plugin.get_token(force_refresh=True)
            request.headers[self._header_name] = f"{prefix}{new_token}"
            yield request


# ── Factory ───────────────────────────────────────────────────────────────────

def build_plugin(cfg: AuthStrategy) -> EnterpriseAuthPlugin:
    """Create and initialise an auth plugin from any AuthStrategy config."""
    plugin = EnterpriseAuthPlugin(cfg)
    plugin.setup()
    return plugin


def plugin_from_dict(d: dict) -> EnterpriseAuthPlugin:
    """Build a plugin from a raw dict (e.g. parsed from YAML)."""
    strategy = d.get("strategy", "whl_module")
    model_map = {
        "env_var":        EnvVarAuth,
        "static_key":     StaticKeyAuth,
        "token_endpoint": TokenEndpointAuth,
        "whl_module":     WhlModuleAuth,
    }
    cls = model_map.get(strategy, WhlModuleAuth)
    return build_plugin(cls.model_validate(d))


# ── Example configs ───────────────────────────────────────────────────────────

def example_configs() -> dict[str, str]:
    """Return example YAML snippets for each auth strategy."""
    return {
        "env_var": """\
# Simplest: token is already in an environment variable
mode: enterprise
enterprise:
  base_url: https://llm.corp.internal/v1
  model: corp-llm-model
  auth_strategy:
    strategy: env_var
    token_env: CORP_LLM_TOKEN       # export CORP_LLM_TOKEN=<your-token>
    header_name: Authorization
    header_prefix: Bearer
""",
        "token_endpoint": """\
# Call an HTTP endpoint (OAuth2 / custom auth API) to get a token
mode: enterprise
enterprise:
  base_url: https://llm.corp.internal/v1
  model: corp-llm-model
  auth_strategy:
    strategy: token_endpoint
    url: https://auth.corp.internal/oauth/token
    method: POST
    payload:
      grant_type: client_credentials
      client_id: ${CORP_CLIENT_ID}
      client_secret: ${CORP_CLIENT_SECRET}
      scope: llm.access
    token_path: access_token     # JSONPath into response
    token_ttl_seconds: 3600
    refresh_on_401: true
""",
        "whl_module": """\
# Corporate Python .whl (corporate .whl style)
mode: enterprise
enterprise:
  base_url: https://llm.corp.internal/v1
  model: corp-codellama-70b
  auth_strategy:
    strategy: whl_module
    setup_module: corp_security    # pip install corp_security.whl
    setup_func: enable_certs
    setup_kwargs: {force: true}
    token_module: corp_auth
    token_func: get_auth_token
    token_kwargs: {}
    token_ttl_seconds: 3600
    refresh_on_401: true
""",
        "static_key": """\
# Simple static API key
mode: enterprise
enterprise:
  base_url: https://llm.corp.internal/v1
  model: corp-llm-model
  auth_strategy:
    strategy: static_key
    api_key: ${CORP_API_KEY}     # export CORP_API_KEY=<your-key>
""",
    }
