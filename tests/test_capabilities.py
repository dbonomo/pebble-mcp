"""Tests for pebble_mcp.capabilities."""

from __future__ import annotations

import pebble_mcp.capabilities as capabilities_module
from pebble_mcp import __version__
from pebble_mcp.capabilities import get_capabilities


def test_capabilities_shape():
    caps = get_capabilities()
    assert set(caps.keys()) == {
        "tier1_appstore",
        "tier2_design",
        "tier3_devloop",
        "tier4_auth",
        "version",
    }
    assert isinstance(caps["tier1_appstore"], bool)
    assert isinstance(caps["tier2_design"], bool)
    assert isinstance(caps["tier3_devloop"], bool)
    assert isinstance(caps["tier4_auth"], bool)
    assert caps["version"] == __version__


def test_tier1_always_true():
    assert get_capabilities()["tier1_appstore"] is True


def test_tier3_respects_path_present(monkeypatch, tmp_path):
    fake_pebble = tmp_path / "pebble"
    fake_pebble.write_text("#!/bin/sh\n")
    fake_pebble.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert get_capabilities()["tier3_devloop"] is True


def test_tier3_respects_path_absent(monkeypatch, tmp_path):
    # An empty directory on PATH; no `pebble` executable anywhere.
    monkeypatch.setenv("PATH", str(tmp_path))
    assert get_capabilities()["tier3_devloop"] is False


def test_tier4_respects_env_var(monkeypatch):
    monkeypatch.delenv("PEBBLE_API_TOKEN", raising=False)
    assert get_capabilities()["tier4_auth"] is False

    monkeypatch.setenv("PEBBLE_API_TOKEN", "sekrit")
    assert get_capabilities()["tier4_auth"] is True


def test_tier2_reflects_pillow_import(monkeypatch):
    # Force the Pillow import to fail regardless of whether it's installed,
    # by making the internal check function report unavailable.
    monkeypatch.setattr(capabilities_module, "_tier2_available", lambda: False)
    assert get_capabilities()["tier2_design"] is False

    monkeypatch.setattr(capabilities_module, "_tier2_available", lambda: True)
    assert get_capabilities()["tier2_design"] is True
