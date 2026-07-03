"""Tests for backend registry name hints."""

from unittest.mock import MagicMock

from db_inspector_mcp.backends.registry import BackendRegistry


def _registry_with(*names: str) -> BackendRegistry:
    registry = BackendRegistry()
    for name in names:
        registry._backends[name] = MagicMock()
    return registry


def test_suggest_backend_name_case_insensitive():
    registry = _registry_with("offline", "sync")
    assert registry._suggest_backend_name("OFFLINE") == "offline"


def test_suggest_backend_name_substring_match():
    registry = _registry_with("offline")
    assert registry._suggest_backend_name("Purple_Offline") == "offline"


def test_get_includes_hint_for_close_name():
    registry = _registry_with("offline")
    try:
        registry.get("Purple_Offline")
    except ValueError as exc:
        assert "Did you mean 'offline'?" in str(exc)
    else:
        raise AssertionError("expected ValueError")
