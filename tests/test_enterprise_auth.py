"""Tests for EnterpriseAuthPlugin — mocks the corporate .whl modules."""
from __future__ import annotations

import importlib
import sys
import threading
import time
import types
from unittest.mock import MagicMock, patch

import pytest

from aicoder.enterprise_auth import (
    AuthPluginConfig, EnterpriseAuthPlugin, build_plugin,
    EnvVarAuth, WhlModuleAuth,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_mock_module(name: str, **funcs) -> types.ModuleType:
    """Create a fake importable module in sys.modules with the given functions."""
    mod = types.ModuleType(name)
    for fname, func in funcs.items():
        setattr(mod, fname, func)
    sys.modules[name] = mod
    return mod


def _remove_mock_module(name: str):
    sys.modules.pop(name, None)


# ── AuthPluginConfig model ────────────────────────────────────────────────────

class TestAuthPluginConfig:

    def test_defaults(self):
        # AuthPluginConfig is now an alias for WhlModuleAuth so requires token fields
        cfg = AuthPluginConfig(token_module="m", token_func="f")
        assert cfg.setup_module is None
        assert cfg.refresh_on_401 is True
        assert cfg.token_ttl_seconds is None

    def test_is_configured_false_without_token(self):
        # EnvVarAuth doesn't have token_module/func — is_configured specific to WhlModuleAuth
        whl_with_no_token = WhlModuleAuth.__new__(WhlModuleAuth)
        cfg = AuthPluginConfig(token_module="", token_func="")
        assert cfg.is_configured() is False  # empty token_module/func → not configured

    def test_is_configured_true(self):
        cfg = AuthPluginConfig(token_module="rbc_auth", token_func="get_auth_token")
        assert cfg.is_configured() is True

    def test_full_rbc_config(self):
        cfg = AuthPluginConfig(
            setup_module="rbc_security",
            setup_func="enable_certs",
            setup_kwargs={"force": True},
            token_module="rbc_auth",
            token_func="get_auth_token",
            token_kwargs={},
            refresh_on_401=True,
            token_ttl_seconds=3600,
        )
        assert cfg.is_configured() is True
        assert cfg.setup_kwargs == {"force": True}
        assert cfg.token_ttl_seconds == 3600


# ── Setup (cert injection) ────────────────────────────────────────────────────

class TestSetup:

    def teardown_method(self):
        _remove_mock_module("mock_rbc_security")
        _remove_mock_module("mock_rbc_auth")

    def test_setup_calls_enable_certs(self):
        called_with = {}

        def mock_enable_certs(**kwargs):
            called_with.update(kwargs)

        _make_mock_module("mock_rbc_security", enable_certs=mock_enable_certs)

        cfg = AuthPluginConfig(
            setup_module="mock_rbc_security",
            setup_func="enable_certs",
            setup_kwargs={"force": True},
            token_module="mock_rbc_auth",
            token_func="get_token",
        )
        plugin = EnterpriseAuthPlugin(cfg)
        _make_mock_module("mock_rbc_auth", get_token=lambda: "tok")

        plugin.setup()
        assert called_with == {"force": True}

    def test_setup_called_only_once(self):
        call_count = [0]

        def mock_enable_certs(**kwargs):
            call_count[0] += 1

        _make_mock_module("mock_rbc_security", enable_certs=mock_enable_certs)
        _make_mock_module("mock_rbc_auth", get_token=lambda: "tok")

        cfg = AuthPluginConfig(
            setup_module="mock_rbc_security",
            setup_func="enable_certs",
            setup_kwargs={},
            token_module="mock_rbc_auth",
            token_func="get_token",
        )
        plugin = EnterpriseAuthPlugin(cfg)
        plugin.setup()
        plugin.setup()  # second call should be a no-op
        assert call_count[0] == 1

    def test_setup_module_not_found_raises(self):
        cfg = AuthPluginConfig(
            setup_module="nonexistent_rbc_wheel",
            setup_func="enable_certs",
            setup_kwargs={},
            token_module="mock_rbc_auth",
            token_func="get_token",
        )
        plugin = EnterpriseAuthPlugin(cfg)
        with pytest.raises(RuntimeError, match="not found"):
            plugin.setup()

    def test_setup_skipped_when_no_setup_module(self):
        """If no setup_module configured, setup() is a no-op."""
        _make_mock_module("mock_rbc_auth", get_token=lambda: "tok")
        cfg = AuthPluginConfig(token_module="mock_rbc_auth", token_func="get_token")
        plugin = EnterpriseAuthPlugin(cfg)
        plugin.setup()  # should not raise
        assert plugin._setup_done is True


# ── Token acquisition ─────────────────────────────────────────────────────────

class TestGetToken:

    def teardown_method(self):
        _remove_mock_module("mock_rbc_auth")

    def test_returns_token(self):
        _make_mock_module("mock_rbc_auth", get_auth_token=lambda: "rbc-bearer-xyz")

        cfg = AuthPluginConfig(token_module="mock_rbc_auth", token_func="get_auth_token")
        plugin = EnterpriseAuthPlugin(cfg)
        assert plugin.get_token() == "rbc-bearer-xyz"

    def test_token_kwargs_passed(self):
        received = {}

        def mock_get_token(**kwargs):
            received.update(kwargs)
            return "tok"

        _make_mock_module("mock_rbc_auth", get_auth_token=mock_get_token)

        cfg = AuthPluginConfig(
            token_module="mock_rbc_auth",
            token_func="get_auth_token",
            token_kwargs={"scope": "llm.access"},
        )
        plugin = EnterpriseAuthPlugin(cfg)
        plugin.get_token()
        assert received == {"scope": "llm.access"}

    def test_token_cached_within_ttl(self):
        call_count = [0]

        def mock_get_token():
            call_count[0] += 1
            return "cached-token"

        _make_mock_module("mock_rbc_auth", get_auth_token=mock_get_token)

        cfg = AuthPluginConfig(
            token_module="mock_rbc_auth",
            token_func="get_auth_token",
            token_ttl_seconds=60,
        )
        plugin = EnterpriseAuthPlugin(cfg)
        token1 = plugin.get_token()
        token2 = plugin.get_token()  # should use cache
        assert token1 == token2 == "cached-token"
        assert call_count[0] == 1  # only one real call

    def test_token_refreshed_after_ttl(self):
        tokens = iter(["first-token", "second-token"])

        def mock_get_token():
            return next(tokens)

        _make_mock_module("mock_rbc_auth", get_auth_token=mock_get_token)

        cfg = AuthPluginConfig(
            token_module="mock_rbc_auth",
            token_func="get_auth_token",
            token_ttl_seconds=1,
        )
        plugin = EnterpriseAuthPlugin(cfg)
        t1 = plugin.get_token()
        # Simulate TTL expiry
        plugin._token_acquired_at = time.time() - 2
        t2 = plugin.get_token()
        assert t1 == "first-token"
        assert t2 == "second-token"

    def test_force_refresh_bypasses_cache(self):
        call_count = [0]

        def mock_get_token():
            call_count[0] += 1
            return f"token-{call_count[0]}"

        _make_mock_module("mock_rbc_auth", get_auth_token=mock_get_token)

        cfg = AuthPluginConfig(
            token_module="mock_rbc_auth",
            token_func="get_auth_token",
            token_ttl_seconds=300,
        )
        plugin = EnterpriseAuthPlugin(cfg)
        plugin.get_token()
        plugin.get_token(force_refresh=True)
        assert call_count[0] == 2

    def test_empty_token_raises(self):
        _make_mock_module("mock_rbc_auth", get_auth_token=lambda: "")

        cfg = AuthPluginConfig(token_module="mock_rbc_auth", token_func="get_auth_token")
        plugin = EnterpriseAuthPlugin(cfg)
        with pytest.raises(RuntimeError, match="empty"):
            plugin.get_token()

    def test_token_module_not_found_raises(self):
        cfg = AuthPluginConfig(
            token_module="nonexistent_rbc_auth",
            token_func="get_auth_token",
        )
        plugin = EnterpriseAuthPlugin(cfg)
        with pytest.raises(RuntimeError, match="not found"):
            plugin.get_token()

    def test_thread_safety(self):
        """Multiple threads must each receive valid tokens."""
        _make_mock_module("mock_rbc_auth", get_auth_token=lambda: "thread-safe-token")

        cfg = AuthPluginConfig(
            token_module="mock_rbc_auth",
            token_func="get_auth_token",
        )
        plugin = EnterpriseAuthPlugin(cfg)
        results = []
        errors = []

        def worker():
            try:
                results.append(plugin.get_token())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert all(r == "thread-safe-token" for r in results)


# ── build_plugin convenience ──────────────────────────────────────────────────

class TestBuildPlugin:

    def teardown_method(self):
        _remove_mock_module("mock_rbc_auth")

    def test_build_plugin_runs_setup(self):
        setup_called = [False]

        def mock_enable_certs(force=False):
            setup_called[0] = True

        _make_mock_module("mock_rbc_security_bp", enable_certs=mock_enable_certs)
        sys.modules["mock_rbc_security_bp"] = sys.modules.get("mock_rbc_security_bp") or types.ModuleType("mock_rbc_security_bp")
        sys.modules["mock_rbc_security_bp"].enable_certs = mock_enable_certs
        _make_mock_module("mock_rbc_auth", get_auth_token=lambda: "tok")

        cfg = AuthPluginConfig(
            setup_module="mock_rbc_security_bp",
            setup_func="enable_certs",
            setup_kwargs={"force": True},
            token_module="mock_rbc_auth",
            token_func="get_auth_token",
        )
        plugin = build_plugin(cfg)
        assert setup_called[0] is True
        assert isinstance(plugin, EnterpriseAuthPlugin)
        _remove_mock_module("mock_rbc_security_bp")
