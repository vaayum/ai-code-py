"""MCP (Model Context Protocol) server loader.

Loads MCP server configs from ``.aicoder/mcp.json`` and returns them as
LangChain-compatible tools that the agent can use alongside built-in tools.

Supports both config formats (identical to the Java McpServerLoader):

**Array format (native)**::

    {
      "mcpServers": [
        {"key": "github", "type": "stdio", "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
         "env": {"GITHUB_TOKEN": "ghp_..."}},
        {"key": "postgres", "type": "http", "url": "http://localhost:3001/mcp"}
      ]
    }

**Object-keyed format (Claude Desktop / VS Code / Cursor standard)**::

    {
      "mcpServers": {
        "CodeGraphContext": {
          "command": "cgc",
          "args": ["mcp", "start"],
          "env": {"NEO4J_URI": "bolt://localhost:7687"}
        }
      }
    }

Usage::

    loader = McpServerLoader(project_root=Path("."))
    mcp_tools = await loader.load_tools()   # async — runs all MCP servers
    all_tools = built_in_tools + mcp_tools
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_MCP_FILE = ".aicoder/mcp.json"
ENV_VAR_RE = re.compile(r"\$\{([^}]+)}")


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class McpServerConfig:
    key: str
    type: str = "stdio"             # "stdio" | "http" | "sse"
    command: list[str] = field(default_factory=list)
    args: list[str] = field(default_factory=list)
    url: Optional[str] = None
    env: dict[str, str] = field(default_factory=dict)


# ── Loader ────────────────────────────────────────────────────────────────────

class McpServerLoader:
    """
    Load MCP servers from ``.aicoder/mcp.json`` and expose them as LangChain tools.

    The loader is intentionally fault-tolerant: if one server fails to connect,
    the others still work (mirrors Java's ``failIfOneServerFails=false``).
    """

    def __init__(self, project_root: Path, mcp_config: Optional[Path] = None) -> None:
        self.root = project_root.resolve()
        self._config_path = mcp_config or (self.root / DEFAULT_MCP_FILE)

    # ── Public API ────────────────────────────────────────────────────────────

    def config_exists(self) -> bool:
        return self._config_path.exists()

    async def load_tools(self) -> list:
        """
        Asynchronously connect to all configured MCP servers and return
        a flat list of LangChain-compatible tools.

        Returns an empty list if ``mcp.json`` is missing or all servers fail.
        """
        if not self.config_exists():
            return []

        try:
            raw = self._config_path.read_text()
            configs = self._parse(raw)
        except Exception as e:
            log.warning("Could not parse MCP config: %s", e)
            print(f"⚠️  Could not parse MCP config: {e}")
            return []

        if not configs:
            print("⚠️  mcp.json has no server entries.")
            return []

        print(f"\n🔌 MCP Servers ({len(configs)} configured):")
        all_tools: list = []
        for cfg in configs:
            tools = await self._load_server(cfg)
            if tools:
                all_tools.extend(tools)

        if all_tools:
            print(f"   ✓ {len(all_tools)} MCP tool(s) loaded\n")
        else:
            print("   ⚠️  No MCP tools loaded (all servers failed or empty)\n")

        return all_tools

    def load_tools_sync(self) -> list:
        """Synchronous wrapper around ``load_tools()`` for non-async contexts."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Already in an async context — use a thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, self.load_tools())
                    return future.result()
            else:
                return loop.run_until_complete(self.load_tools())
        except Exception as e:
            log.warning("MCP load_tools_sync failed: %s", e)
            return []

    def example_config(self) -> str:
        """Return an example mcp.json snippet the user can place in their project."""
        return json.dumps({
            "mcpServers": {
                "github": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
                },
                "filesystem": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
                },
                "postgres": {
                    "type": "http",
                    "url": "http://localhost:3001/mcp"
                }
            }
        }, indent=2)

    # ── Private ───────────────────────────────────────────────────────────────

    async def _load_server(self, cfg: McpServerConfig) -> list:
        """Connect to a single MCP server and return its tools. Errors are swallowed."""
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            server_params = self._build_server_params(cfg)
            if server_params is None:
                return []

            async with MultiServerMCPClient({cfg.key: server_params}) as client:
                tools = client.get_tools()
                print(f"   ✓ {cfg.key:<16} [{cfg.type}] → {len(tools)} tool(s)")
                return tools

        except ImportError:
            print("   ✗ langchain-mcp-adapters not installed. Run: pip install langchain-mcp-adapters")
            return []
        except Exception as e:
            log.warning("MCP server '%s' failed: %s", cfg.key, e)
            print(f"   ✗ {cfg.key:<16} [{cfg.type}] FAILED: {e}")
            return []

    def _build_server_params(self, cfg: McpServerConfig) -> dict | None:
        """Build the params dict for MultiServerMCPClient."""
        transport = cfg.type.lower()

        if transport == "stdio":
            cmd = [self._expand(c) for c in cfg.command]
            if not cmd:
                log.warning("MCP server '%s' has no command", cfg.key)
                return None
            return {
                "transport": "stdio",
                "command": cmd[0],
                "args": cmd[1:],
                "env": {k: self._expand(v) for k, v in cfg.env.items()},
            }

        elif transport in ("http", "sse"):
            if not cfg.url:
                log.warning("MCP server '%s' has no url", cfg.key)
                return None
            return {
                "transport": "sse",
                "url": self._expand(cfg.url),
            }

        else:
            log.warning("Unknown MCP transport '%s' for server '%s'", transport, cfg.key)
            return None

    # ── Config parsing ────────────────────────────────────────────────────────

    def _parse(self, raw: str) -> list[McpServerConfig]:
        """Parse both array format and object-keyed format."""
        data = json.loads(raw)
        servers_node = data.get("mcpServers", {})
        configs: list[McpServerConfig] = []

        if isinstance(servers_node, list):
            # Array format: [{"key": "github", "command": [...]}]
            for item in servers_node:
                if isinstance(item, dict):
                    configs.append(self._from_dict(item, fallback_key=None))

        elif isinstance(servers_node, dict):
            # Object-keyed format: {"github": {"command": "...", "args": [...]}}
            for key, value in servers_node.items():
                if isinstance(value, dict):
                    configs.append(self._from_dict(value, fallback_key=key))

        return configs

    def _from_dict(self, d: dict, fallback_key: str | None) -> McpServerConfig:
        key = d.get("key", fallback_key or "mcp")

        # Infer type if not explicit
        if "type" in d:
            transport = d["type"]
        elif "url" in d:
            transport = "http"
        else:
            transport = "stdio"

        # command: string or list
        raw_cmd = d.get("command", [])
        if isinstance(raw_cmd, str):
            command = [raw_cmd]
        elif isinstance(raw_cmd, list):
            command = [str(c) for c in raw_cmd]
        else:
            command = []

        # args: separate "args" array (Claude Desktop format)
        raw_args = d.get("args", [])
        args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []

        # Merge: full command = command + args
        full_command = command + args

        env = {k: str(v) for k, v in d.get("env", {}).items()}

        return McpServerConfig(
            key=key,
            type=transport,
            command=full_command,
            url=d.get("url"),
            env=env,
        )

    @staticmethod
    def _expand(value: str) -> str:
        """Expand ${ENV_VAR} placeholders using runtime environment variables."""
        return ENV_VAR_RE.sub(
            lambda m: os.environ.get(m.group(1), m.group(0)),
            value,
        )
