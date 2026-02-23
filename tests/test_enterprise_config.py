"""Tests for enterprise config and custom endpoint support."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from aicoder.config import AiCoderConfig, EnterpriseConfig, load_config, _expand


# ── EnterpriseConfig model ────────────────────────────────────────────────────

class TestEnterpriseConfigModel:

    def test_defaults(self):
        ent = EnterpriseConfig()
        assert ent.base_url == ""
        assert ent.tls_verify is True
        assert ent.ca_bundle is None
        assert ent.proxy_url is None

    def test_has_proxy_true(self):
        ent = EnterpriseConfig(proxy_url="http://proxy:3128")
        assert ent.has_proxy() is True

    def test_has_proxy_false(self):
        ent = EnterpriseConfig()
        assert ent.has_proxy() is False

    def test_has_custom_tls_via_ca_bundle(self):
        ent = EnterpriseConfig(ca_bundle="/etc/ssl/corp.pem")
        assert ent.has_custom_tls() is True

    def test_has_custom_tls_via_tls_verify_false(self):
        ent = EnterpriseConfig(tls_verify=False)
        assert ent.has_custom_tls() is True

    def test_has_custom_tls_false_when_default(self):
        ent = EnterpriseConfig()
        assert ent.has_custom_tls() is False


# ── Config file loading ────────────────────────────────────────────────────────

class TestAiCoderConfigLoading:

    def test_default_config(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.yml")
        assert cfg.mode == "open"
        assert cfg.provider == "deepseek"

    def test_open_mode_config(self, tmp_path):
        cfg_file = tmp_path / ".aicoder.yml"
        cfg_file.write_text(yaml.dump({
            "provider": "openai",
            "model": "gpt-4o",
            "agent": {"temperature": 0.2, "max_retries": 5},
        }))
        cfg = load_config(cfg_file)
        assert cfg.provider == "openai"
        assert cfg.model == "gpt-4o"
        assert cfg.agent.temperature == 0.2
        assert cfg.agent.max_retries == 5

    def test_enterprise_mode_config(self, tmp_path):
        cfg_file = tmp_path / ".aicoder.yml"
        cfg_file.write_text(yaml.dump({
            "mode": "enterprise",
            "enterprise": {
                "base_url": "https://llm.corp.internal/v1",
                "model": "codellama-70b",
                "api_key": "corp-token-123",
                "tls_verify": True,
            },
        }))
        cfg = load_config(cfg_file)
        assert cfg.mode == "enterprise"
        assert cfg.enterprise is not None
        assert cfg.enterprise.base_url == "https://llm.corp.internal/v1"
        assert cfg.enterprise.model == "codellama-70b"
        assert cfg.enterprise.api_key == "corp-token-123"

    def test_enterprise_with_proxy(self, tmp_path):
        cfg_file = tmp_path / ".aicoder.yml"
        cfg_file.write_text(yaml.dump({
            "mode": "enterprise",
            "enterprise": {
                "base_url": "https://llm.corp.internal/v1",
                "model": "mistral",
                "api_key": "key",
                "proxy_url": "http://proxy.corp:3128",
            },
        }))
        cfg = load_config(cfg_file)
        assert cfg.enterprise.proxy_url == "http://proxy.corp:3128"
        assert cfg.enterprise.has_proxy() is True

    def test_tls_verify_false(self, tmp_path):
        cfg_file = tmp_path / ".aicoder.yml"
        cfg_file.write_text(yaml.dump({
            "mode": "enterprise",
            "enterprise": {
                "base_url": "https://llm.dev.internal/v1",
                "model": "llama3",
                "api_key": "devkey",
                "tls_verify": False,
            },
        }))
        cfg = load_config(cfg_file)
        assert cfg.enterprise.tls_verify is False
        assert cfg.enterprise.has_custom_tls() is True


# ── Env-var expansion ─────────────────────────────────────────────────────────

class TestEnvVarExpansion:

    def test_expand_known_var(self, monkeypatch):
        monkeypatch.setenv("CORP_TOKEN", "secret")
        assert _expand("${CORP_TOKEN}") == "secret"

    def test_expand_unknown_var_kept(self, monkeypatch):
        monkeypatch.delenv("UNKNOWN_VAR", raising=False)
        assert _expand("${UNKNOWN_VAR}") == "${UNKNOWN_VAR}"

    def test_expand_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("HOST", "llm.corp.internal")
        monkeypatch.setenv("PORT", "443")
        result = _expand("https://${HOST}:${PORT}/v1")
        assert result == "https://llm.corp.internal:443/v1"

    def test_expand_no_vars(self):
        assert _expand("plain-string") == "plain-string"

    def test_expand_none(self):
        assert _expand(None) is None

    def test_env_var_expanded_in_config_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CORP_LLM_TOKEN", "mytoken123")
        cfg_file = tmp_path / ".aicoder.yml"
        cfg_file.write_text(yaml.dump({
            "mode": "enterprise",
            "enterprise": {
                "base_url": "https://llm.corp.internal/v1",
                "model": "llama3",
                "api_key": "${CORP_LLM_TOKEN}",
            },
        }))
        cfg = load_config(cfg_file)
        assert cfg.enterprise.api_key == "mytoken123"


# ── Config YAML fallback chain ─────────────────────────────────────────────────

class TestConfigFallback:

    def test_returns_defaults_when_no_file(self, tmp_path):
        cfg = load_config(tmp_path / "no-config.yml")
        assert cfg.mode == "open"
        assert cfg.enterprise is None

    def test_custom_path_takes_priority(self, tmp_path):
        custom = tmp_path / "custom.yml"
        custom.write_text(yaml.dump({"provider": "anthropic", "model": "claude-3-5-sonnet"}))
        cfg = load_config(custom)
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-3-5-sonnet"


# ── AiCoderConfig validation ──────────────────────────────────────────────────

class TestConfigValidation:

    def test_enterprise_config_embedded(self):
        cfg = AiCoderConfig(
            mode="enterprise",
            enterprise=EnterpriseConfig(
                base_url="https://llm.corp/v1",
                model="codellama",
                api_key="key",
            ),
        )
        assert cfg.mode == "enterprise"
        assert cfg.enterprise.model == "codellama"

    def test_max_tokens_default(self):
        cfg = AiCoderConfig()
        assert cfg.agent.max_tokens == 4096
