"""Interactive enterprise configuration wizard.

Walks the user through a series of questions and generates a valid ``.aicoder.yml``
enterprise config file. Core insight: every enterprise LLM deployment is just:

    1. Acquire a token  (env var / static key / OAuth endpoint / corporate .whl)
    2. Send it as Bearer to a hosted LLM endpoint

The wizard covers all four cases with clear, structured prompts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional


# ── Rich helpers ──────────────────────────────────────────────────────────────

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.rule import Rule
    from rich.syntax import Syntax

    console = Console()

    def _prompt(msg: str, *, default: str = "", password: bool = False) -> str:
        return Prompt.ask(f"[bold cyan]{msg}[/bold cyan]", default=default, password=password)

    def _confirm(msg: str, *, default: bool = True) -> bool:
        return Confirm.ask(f"[bold cyan]{msg}[/bold cyan]", default=default)

    def _header(msg: str) -> None:
        console.print(Rule(f"[bold yellow]{msg}[/bold yellow]"))

    def _info(msg: str) -> None:
        console.print(f"[dim]{msg}[/dim]")

    def _ok(msg: str) -> None:
        console.print(f"[green]✓[/green] {msg}")

    def _warn(msg: str) -> None:
        console.print(f"[yellow]⚠[/yellow]  {msg}")

except ImportError:
    # Fallback for minimal environments
    def _prompt(msg, *, default="", password=False):  # type: ignore[misc]
        hint = f" [{default}]" if default else ""
        return input(f"{msg}{hint}: ").strip() or default

    def _confirm(msg, *, default=True):  # type: ignore[misc]
        hint = "Y/n" if default else "y/N"
        ans = input(f"{msg} [{hint}]: ").strip().lower()
        return default if not ans else ans == "y"

    def _header(msg):  # type: ignore[misc]
        print(f"\n── {msg} ──")

    def _info(msg):  # type: ignore[misc]
        print(f"  {msg}")

    def _ok(msg):  # type: ignore[misc]
        print(f"✓ {msg}")

    def _warn(msg):  # type: ignore[misc]
        print(f"⚠  {msg}")

    console = None  # type: ignore[assignment]


# ── Auth strategy choices ─────────────────────────────────────────────────────

_STRATEGY_MENU = {
    "1": "env_var",
    "2": "static_key",
    "3": "token_endpoint",
    "4": "whl_module",
}

_STRATEGY_DESC = {
    "env_var":        "Token already in an environment variable (e.g. export CORP_TOKEN=...)",
    "static_key":     "Static API key in config or env var",
    "token_endpoint": "Call an HTTP endpoint (OAuth2 / custom auth API) to get a token",
    "whl_module":     "Corporate Python .whl (e.g. corp_security + corp_auth pattern)",
}


# ── Wizard ────────────────────────────────────────────────────────────────────

def run_wizard(output_path: Path) -> None:
    """Ask structured questions and write .aicoder.yml to output_path."""
    _banner()

    # ── 1. LLM Endpoint ───────────────────────────────────────────────────────
    _header("Step 1 / 5 — LLM Endpoint")
    _info("Your enterprise LLM must expose an OpenAI-compatible API.")
    base_url = _prompt("LLM base URL", default="https://llm.corp.internal/v1")
    model    = _prompt("Model name (as your endpoint expects it)", default="corp-llm-model")

    # ── 2. Auth strategy ──────────────────────────────────────────────────────
    _header("Step 2 / 5 — Authentication")
    _info("How does your enterprise obtain the API token?\n")
    for k, v in _STRATEGY_MENU.items():
        _info(f"  [bold]{k}[/bold] — {_STRATEGY_DESC[v]}" if console else f"  {k} — {_STRATEGY_DESC[v]}")

    choice = ""
    while choice not in _STRATEGY_MENU:
        choice = _prompt("Choose (1-4)", default="1")

    strategy = _STRATEGY_MENU[choice]
    auth_block = _collect_auth(strategy)

    # ── 3. TLS / certificate ──────────────────────────────────────────────────
    _header("Step 3 / 5 — TLS / Certificates")
    tls_block: dict = {}
    tls_mode = _prompt(
        "TLS verification? [default=system / pem=custom CA bundle / off=disable]",
        default="default",
    ).strip().lower()
    if tls_mode == "pem":
        ca_path = _prompt("Path to PEM CA bundle", default="/etc/ssl/corp-ca.pem")
        tls_block["ca_bundle"] = ca_path
    elif tls_mode == "off":
        _warn("Disabling TLS verification is insecure — use only in dev/staging!")
        tls_block["tls_verify"] = False
    # else: use OS defaults, nothing needed

    # ── 4. Proxy ──────────────────────────────────────────────────────────────
    _header("Step 4 / 5 — HTTP Proxy")
    proxy_block: dict = {}
    if _confirm("Does traffic go through an HTTP proxy?", default=False):
        proxy_url = _prompt("Proxy URL", default="http://proxy.corp.internal:3128")
        proxy_block["proxy_url"] = proxy_url

    # ── 5. Agent tuning ───────────────────────────────────────────────────────
    _header("Step 5 / 5 — Agent Settings (optional)")
    temp     = _prompt("Temperature (0.0 – 1.0)", default="0.1")
    retries  = _prompt("Max retries", default="3")
    max_tok  = _prompt("Max tokens per response", default="4096")

    # ── Assemble YAML ─────────────────────────────────────────────────────────
    yaml_str = _build_yaml(
        base_url=base_url,
        model=model,
        auth_block=auth_block,
        tls_block=tls_block,
        proxy_block=proxy_block,
        temperature=temp,
        max_retries=retries,
        max_tokens=max_tok,
    )

    # ── Preview ───────────────────────────────────────────────────────────────
    _header("Generated .aicoder.yml")
    if console:
        console.print(Syntax(yaml_str, "yaml", theme="monokai", line_numbers=False))
    else:
        print(yaml_str)

    # ── Write ─────────────────────────────────────────────────────────────────
    if _confirm(f"\nWrite to {output_path}?", default=True):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(yaml_str)
        _ok(f"Config written to {output_path}")
        _info("Run [bold]aicoder fix[/bold] (or any command) to use this config." if console
              else 'Run "aicoder fix" to use this config.')
    else:
        _warn("Not written. Copy the YAML above manually if needed.")


# ── Auth collectors (one per strategy) ────────────────────────────────────────

def _collect_auth(strategy: str) -> dict:
    """Return the auth_strategy dict for the chosen strategy."""

    if strategy == "env_var":
        _info("\nThe token must already exist as an environment variable when aicoder runs.")
        env_name   = _prompt("Environment variable name", default="CORP_LLM_TOKEN")
        hdr_prefix = _prompt("Auth header value prefix", default="Bearer")
        return {
            "strategy":      "env_var",
            "token_env":     env_name,
            "header_name":   "Authorization",
            "header_prefix": hdr_prefix,
        }

    elif strategy == "static_key":
        _info("\nThe key is read once at startup. Use ${ENV_VAR} to avoid storing secrets in the file.")
        key        = _prompt("API key (or ${ENV_VAR} reference)", default="${CORP_API_KEY}")
        hdr_prefix = _prompt("Auth header value prefix", default="Bearer")
        return {
            "strategy":      "static_key",
            "api_key":       key,
            "header_name":   "Authorization",
            "header_prefix": hdr_prefix,
        }

    elif strategy == "token_endpoint":
        _info("\nA fresh token is fetched by calling your auth endpoint. Tokens are cached.")
        url    = _prompt("Token endpoint URL", default="https://auth.corp.internal/oauth/token")
        method = _prompt("HTTP method", default="POST")
        _info("Payload fields (key=value, blank line to finish). Use ${ENV_VAR} for secrets.")
        payload: dict = {}
        _info('  Example: grant_type=client_credentials   client_id=${CORP_CLIENT_ID}')
        while True:
            pair = _prompt("  payload field (or press Enter to skip)", default="")
            if not pair:
                break
            if "=" in pair:
                k, _, v = pair.partition("=")
                payload[k.strip()] = v.strip()
        token_path = _prompt("Key path to token in JSON response", default="access_token")
        ttl        = _prompt("Token TTL in seconds (cache duration)", default="3600")
        return {
            "strategy":          "token_endpoint",
            "url":               url,
            "method":            method.upper(),
            "payload":           payload,
            "token_path":        token_path,
            "token_ttl_seconds": int(ttl),
            "refresh_on_401":    True,
        }

    else:  # whl_module
        _info("\nThe corporate .whl must be pip-installed in the same Python environment.")
        has_setup  = _confirm("Does your .whl have a cert-setup function (e.g. enable_certs)?",
                              default=True)
        setup_block: dict = {}
        if has_setup:
            setup_mod  = _prompt("Setup module name", default="corp_security")
            setup_func = _prompt("Setup function name", default="enable_certs")
            force      = _confirm("  Pass force=True?", default=True)
            setup_block = {
                "setup_module": setup_mod,
                "setup_func":   setup_func,
                "setup_kwargs": {"force": force},
            }
        token_mod  = _prompt("Token module name", default="corp_auth")
        token_func = _prompt("Token function name", default="get_auth_token")
        ttl        = _prompt("Token TTL in seconds (0 = no cache)", default="3600")
        return {
            "strategy":          "whl_module",
            **setup_block,
            "token_module":      token_mod,
            "token_func":        token_func,
            "token_kwargs":      {},
            "token_ttl_seconds": int(ttl) or None,
            "refresh_on_401":    True,
        }


# ── YAML builder ──────────────────────────────────────────────────────────────

def _build_yaml(
    base_url: str,
    model: str,
    auth_block: dict,
    tls_block: dict,
    proxy_block: dict,
    temperature: str,
    max_retries: str,
    max_tokens: str,
) -> str:
    lines = [
        "mode: enterprise",
        "",
        "enterprise:",
        f"  base_url: {base_url}",
        f"  model: {model}",
    ]

    # TLS
    if "tls_verify" in tls_block:
        lines.append(f"  tls_verify: {str(tls_block['tls_verify']).lower()}")
    if "ca_bundle" in tls_block:
        lines.append(f"  ca_bundle: {tls_block['ca_bundle']}")

    # Proxy
    if proxy_block:
        lines.append(f"  proxy_url: {proxy_block['proxy_url']}")

    # Auth strategy
    lines.append("")
    lines.append("  auth_strategy:")
    for k, v in auth_block.items():
        lines.append(_yaml_field(k, v, indent=4))

    # Agent settings
    lines += [
        "",
        "agent:",
        f"  temperature: {temperature}",
        f"  max_retries: {max_retries}",
        f"  max_tokens: {max_tokens}",
        "",
    ]
    return "\n".join(lines)


def _yaml_field(key: str, value, indent: int) -> str:
    prefix = " " * indent
    if isinstance(value, dict):
        if not value:
            return f"{prefix}{key}: {{}}"
        lines = [f"{prefix}{key}:"]
        for k, v in value.items():
            lines.append(_yaml_field(k, v, indent + 2))
        return "\n".join(lines)
    elif isinstance(value, bool):
        return f"{prefix}{key}: {str(value).lower()}"
    elif value is None:
        return f"{prefix}{key}: null"
    else:
        return f"{prefix}{key}: {value}"


# ── Banner ────────────────────────────────────────────────────────────────────

def _banner() -> None:
    msg = (
        "[bold white]AICoder Enterprise Setup Wizard[/bold white]\n"
        "[dim]Generates a .aicoder.yml for your on-premise LLM.\n"
        "Works with any OpenAI-compatible endpoint.[/dim]"
    )
    if console:
        console.print(Panel(msg, expand=False, border_style="cyan"))
    else:
        print("\n=== AICoder Enterprise Setup Wizard ===")
        print("Generates a .aicoder.yml for your on-premise LLM.\n")
