"""Enterprise Auth Plugin — pluggable authentication for on-premise corporate LLMs.

Solves the common banking/enterprise pattern:

1. A corporate ``.whl`` is pip-installed into the environment (e.g. ``rbc_security``)
2. It must be called at startup to inject TLS certificates:
   ``from rbc_security import enable_certs; enable_certs(force=True)``
3. A bearer token is obtained via an auth function:
   ``from rbc_auth import get_auth_token; token = get_auth_token()``
4. The token is sent as ``Authorization: Bearer <token>`` on every LLM API call
5. When the token expires (HTTP 401), it is automatically refreshed

Configuration lives in ``.aicoder.yml``::

    mode: enterprise
    enterprise:
      base_url: https://llm.rbc.internal/v1
      model: rbc-codellama-70b
      auth_plugin:
        # Cert setup (called once at startup)
        setup_module: rbc_security
        setup_func: enable_certs
        setup_kwargs:
          force: true
        # Token acquisition (called at startup + on 401 refresh)
        token_module: rbc_auth
        token_func: get_auth_token
        token_kwargs: {}
        refresh_on_401: true
"""
from __future__ import annotations

import importlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Generator, Iterator, Optional

from pydantic import BaseModel


# ── Config model ──────────────────────────────────────────────────────────────

class AuthPluginConfig(BaseModel):
    """Pydantic model for the auth_plugin: block in .aicoder.yml."""

    # ── Cert / TLS setup ──
    setup_module: Optional[str] = None     # e.g. "rbc_security"
    setup_func: Optional[str] = None       # e.g. "enable_certs"
    setup_kwargs: dict = {}                # e.g. {"force": True}

    # ── Token acquisition ──
    token_module: Optional[str] = None     # e.g. "rbc_auth"
    token_func: Optional[str] = None       # e.g. "get_auth_token"
    token_kwargs: dict = {}                # passed to token_func

    # ── Token management ──
    refresh_on_401: bool = True            # auto-refresh on HTTP 401
    token_ttl_seconds: Optional[int] = None  # cache tokens this long (None = no cache)

    def is_configured(self) -> bool:
        return bool(self.token_module and self.token_func)


# ── Plugin class ──────────────────────────────────────────────────────────────

class EnterpriseAuthPlugin:
    """
    Runtime handle for corporate auth.

    Usage::

        plugin = EnterpriseAuthPlugin(config.enterprise.auth_plugin)
        plugin.setup()            # run enable_certs() etc.
        token = plugin.get_token()
        http_client = plugin.build_httpx_client()  # inject into LangChain
    """

    def __init__(self, cfg: AuthPluginConfig) -> None:
        self._cfg = cfg
        self._token: Optional[str] = None
        self._token_acquired_at: float = 0.0
        self._lock = threading.Lock()
        self._setup_done = False

    # ── public API ────────────────────────────────────────────────────────────

    def setup(self) -> None:
        """Run the cert/TLS setup function (if configured). Safe to call multiple times."""
        if self._setup_done:
            return
        cfg = self._cfg
        if cfg.setup_module and cfg.setup_func:
            try:
                mod = importlib.import_module(cfg.setup_module)
            except ModuleNotFoundError as e:
                raise RuntimeError(
                    f"❌ Auth plugin setup module '{cfg.setup_module}' not found.\n"
                    f"   Did you pip install the corporate .whl?\n"
                    f"   Original error: {e}"
                ) from e

            func: Callable = getattr(mod, cfg.setup_func)
            print(f"🔐 Running cert setup: {cfg.setup_module}.{cfg.setup_func}({cfg.setup_kwargs})")
            func(**cfg.setup_kwargs)
            print("✅ Corporate TLS certs configured.")

        self._setup_done = True

    def get_token(self, force_refresh: bool = False) -> str:
        """
        Return a valid bearer token. Caches the token if token_ttl_seconds is set.
        Thread-safe.
        """
        cfg = self._cfg
        with self._lock:
            # Check cache
            if not force_refresh and self._token and cfg.token_ttl_seconds:
                age = time.time() - self._token_acquired_at
                if age < cfg.token_ttl_seconds:
                    return self._token

            # Acquire fresh token
            if not cfg.token_module or not cfg.token_func:
                raise RuntimeError(
                    "❌ No token_module/token_func configured in auth_plugin."
                )

            try:
                mod = importlib.import_module(cfg.token_module)
            except ModuleNotFoundError as e:
                raise RuntimeError(
                    f"❌ Auth token module '{cfg.token_module}' not found.\n"
                    f"   Original error: {e}"
                ) from e

            func: Callable = getattr(mod, cfg.token_func)
            print(f"🔑 Acquiring auth token: {cfg.token_module}.{cfg.token_func}()")
            token = func(**cfg.token_kwargs)

            if not isinstance(token, str) or not token:
                raise RuntimeError(
                    f"❌ {cfg.token_func}() returned an empty or non-string token: {token!r}"
                )

            self._token = token
            self._token_acquired_at = time.time()
            print("✅ Bearer token acquired.")
            return token

    def build_httpx_client(
        self,
        verify: bool | str = True,
        proxy_url: Optional[str] = None,
    ):
        """
        Return an ``httpx.Client`` that:
        - Injects ``Authorization: Bearer <token>`` on every request
        - Re-acquires the token on HTTP 401 and retries (if refresh_on_401=True)
        - Handles TLS verify and proxy settings
        """
        try:
            import httpx
        except ImportError as e:
            raise ImportError("pip install httpx to use the enterprise auth plugin") from e

        auth = _BearerTokenAuth(self, refresh_on_401=self._cfg.refresh_on_401)

        kwargs: dict[str, Any] = {"auth": auth}
        if verify is not True:
            kwargs["verify"] = verify
        if proxy_url:
            kwargs["proxies"] = {"http://": proxy_url, "https://": proxy_url}

        return httpx.Client(**kwargs)


# ── httpx Auth implementation ─────────────────────────────────────────────────

class _BearerTokenAuth:
    """
    httpx Auth implementation that injects a bearer token and handles refresh.

    httpx's sync Auth protocol requires ``auth_flow`` to be a generator that:
    1. Yields the modified request (with the token header injected)
    2. Receives the response
    3. Optionally yields a retry request if the response is 401
    """

    def __init__(self, plugin: EnterpriseAuthPlugin, refresh_on_401: bool = True) -> None:
        self._plugin = plugin
        self._refresh_on_401 = refresh_on_401

    def auth_flow(self, request) -> Generator:
        token = self._plugin.get_token()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request

        if response.status_code == 401 and self._refresh_on_401:
            print("⚠️  Got 401 — refreshing auth token and retrying…")
            new_token = self._plugin.get_token(force_refresh=True)
            request.headers["Authorization"] = f"Bearer {new_token}"
            yield request


# ── Convenience factory ───────────────────────────────────────────────────────

def build_plugin(cfg: AuthPluginConfig) -> EnterpriseAuthPlugin:
    """Create and set up an auth plugin from config. Calls setup() automatically."""
    plugin = EnterpriseAuthPlugin(cfg)
    plugin.setup()
    return plugin
