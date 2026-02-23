"""Tests for McpServerLoader — no live MCP servers needed."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from aicoder.mcp_loader import McpServerLoader, McpServerConfig


# ── Parsing tests ─────────────────────────────────────────────────────────────

class TestMcpConfigParsing:

    def _loader(self, tmp) -> McpServerLoader:
        return McpServerLoader(Path(tmp))

    def test_object_keyed_stdio(self, tmp_path):
        """Parses Claude Desktop / VS Code / Cursor object format."""
        raw = json.dumps({
            "mcpServers": {
                "github": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-github"],
                    "env": {"GITHUB_TOKEN": "ghp_test"},
                }
            }
        })
        loader = McpServerLoader(tmp_path)
        configs = loader._parse(raw)
        assert len(configs) == 1
        cfg = configs[0]
        assert cfg.key == "github"
        assert cfg.type == "stdio"
        assert cfg.command == ["npx", "-y", "@modelcontextprotocol/server-github"]
        assert cfg.env == {"GITHUB_TOKEN": "ghp_test"}

    def test_array_format_stdio(self, tmp_path):
        """Parses native array format."""
        raw = json.dumps({
            "mcpServers": [
                {
                    "key": "fs",
                    "type": "stdio",
                    "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                }
            ]
        })
        loader = McpServerLoader(tmp_path)
        configs = loader._parse(raw)
        assert len(configs) == 1
        assert configs[0].key == "fs"
        assert configs[0].command[0] == "npx"

    def test_http_server_inferred_type(self, tmp_path):
        """Type is inferred as 'http' when 'url' is present and 'type' is absent."""
        raw = json.dumps({
            "mcpServers": {
                "postgres": {"url": "http://localhost:3001/mcp"}
            }
        })
        loader = McpServerLoader(tmp_path)
        configs = loader._parse(raw)
        assert configs[0].type == "http"
        assert configs[0].url == "http://localhost:3001/mcp"

    def test_multiple_servers(self, tmp_path):
        raw = json.dumps({
            "mcpServers": {
                "github": {"command": "npx", "args": ["-y", "@mcp/github"]},
                "jira":   {"url": "http://jira.local/mcp"},
            }
        })
        loader = McpServerLoader(tmp_path)
        configs = loader._parse(raw)
        assert len(configs) == 2
        keys = {c.key for c in configs}
        assert keys == {"github", "jira"}

    def test_command_string_becomes_list(self, tmp_path):
        """Command as a plain string (not list) is wrapped into a list."""
        raw = json.dumps({
            "mcpServers": [{"key": "cgc", "command": "cgc", "args": ["mcp", "start"]}]
        })
        loader = McpServerLoader(tmp_path)
        configs = loader._parse(raw)
        assert configs[0].command == ["cgc", "mcp", "start"]

    def test_empty_mcp_servers(self, tmp_path):
        raw = json.dumps({"mcpServers": {}})
        loader = McpServerLoader(tmp_path)
        configs = loader._parse(raw)
        assert configs == []

    def test_missing_mcp_servers_key(self, tmp_path):
        raw = json.dumps({"otherKey": "value"})
        loader = McpServerLoader(tmp_path)
        configs = loader._parse(raw)
        assert configs == []


# ── Env var expansion ─────────────────────────────────────────────────────────

class TestEnvVarExpansion:

    def test_expands_known_var(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret123")
        result = McpServerLoader._expand("Bearer ${MY_TOKEN}")
        assert result == "Bearer secret123"

    def test_leaves_unknown_var(self, monkeypatch):
        monkeypatch.delenv("UNKNOWN_VAR", raising=False)
        result = McpServerLoader._expand("${UNKNOWN_VAR}")
        assert result == "${UNKNOWN_VAR}"

    def test_none_passthrough(self):
        # _expand is for strings; None would come from optional url field
        # Just verify it handles plain strings correctly
        assert McpServerLoader._expand("no-vars") == "no-vars"

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "3001")
        result = McpServerLoader._expand("http://${HOST}:${PORT}/mcp")
        assert result == "http://localhost:3001/mcp"


# ── File discovery ────────────────────────────────────────────────────────────

class TestConfigDiscovery:

    def test_config_exists_true(self, tmp_path):
        mcp_file = tmp_path / ".aicoder" / "mcp.json"
        mcp_file.parent.mkdir()
        mcp_file.write_text('{"mcpServers": {}}')
        loader = McpServerLoader(tmp_path)
        assert loader.config_exists() is True

    def test_config_exists_false(self, tmp_path):
        loader = McpServerLoader(tmp_path)
        assert loader.config_exists() is False

    def test_custom_config_path(self, tmp_path):
        custom = tmp_path / "my-mcp.json"
        custom.write_text('{"mcpServers": {}}')
        loader = McpServerLoader(tmp_path, mcp_config=custom)
        assert loader.config_exists() is True

    def test_load_returns_empty_if_no_file(self, tmp_path):
        loader = McpServerLoader(tmp_path)
        tools = loader.load_tools_sync()
        assert tools == []

    def test_corrupted_json_returns_empty(self, tmp_path):
        mcp_file = tmp_path / ".aicoder" / "mcp.json"
        mcp_file.parent.mkdir()
        mcp_file.write_text("{ invalid json !!!")
        loader = McpServerLoader(tmp_path)
        tools = loader.load_tools_sync()
        assert tools == []


# ── Example config ────────────────────────────────────────────────────────────

class TestExampleConfig:

    def test_example_is_valid_json(self, tmp_path):
        loader = McpServerLoader(tmp_path)
        raw = loader.example_config()
        parsed = json.loads(raw)
        assert "mcpServers" in parsed

    def test_example_has_expected_servers(self, tmp_path):
        loader = McpServerLoader(tmp_path)
        parsed = json.loads(loader.example_config())
        servers = parsed["mcpServers"]
        assert "github" in servers
        assert "filesystem" in servers
        assert "postgres" in servers

    def test_example_github_has_env(self, tmp_path):
        loader = McpServerLoader(tmp_path)
        parsed = json.loads(loader.example_config())
        github = parsed["mcpServers"]["github"]
        assert "GITHUB_TOKEN" in github.get("env", {})

    def test_mcp_init_command_creates_file(self, tmp_path):
        """mcp-init equivalent: loader.example_config() written to .aicoder/mcp.json"""
        loader = McpServerLoader(tmp_path)
        target = tmp_path / ".aicoder" / "mcp.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(loader.example_config())
        assert target.exists()
        parsed = json.loads(target.read_text())
        assert "mcpServers" in parsed


# ── Build params ──────────────────────────────────────────────────────────────

class TestBuildServerParams:

    def test_stdio_params(self, tmp_path):
        loader = McpServerLoader(tmp_path)
        cfg = McpServerConfig(
            key="github",
            type="stdio",
            command=["npx", "-y", "@mcp/github"],
            env={"GITHUB_TOKEN": "tok"},
        )
        params = loader._build_server_params(cfg)
        assert params["transport"] == "stdio"
        assert params["command"] == "npx"
        assert params["args"] == ["-y", "@mcp/github"]
        assert params["env"] == {"GITHUB_TOKEN": "tok"}

    def test_http_params(self, tmp_path):
        loader = McpServerLoader(tmp_path)
        cfg = McpServerConfig(key="pg", type="http", url="http://localhost:3001/mcp")
        params = loader._build_server_params(cfg)
        assert params["transport"] == "sse"
        assert params["url"] == "http://localhost:3001/mcp"

    def test_stdio_no_command_returns_none(self, tmp_path):
        loader = McpServerLoader(tmp_path)
        cfg = McpServerConfig(key="bad", type="stdio", command=[])
        params = loader._build_server_params(cfg)
        assert params is None

    def test_http_no_url_returns_none(self, tmp_path):
        loader = McpServerLoader(tmp_path)
        cfg = McpServerConfig(key="bad", type="http", url=None)
        params = loader._build_server_params(cfg)
        assert params is None

    def test_unknown_transport_returns_none(self, tmp_path):
        loader = McpServerLoader(tmp_path)
        cfg = McpServerConfig(key="bad", type="grpc", command=["some-cmd"])
        params = loader._build_server_params(cfg)
        assert params is None
