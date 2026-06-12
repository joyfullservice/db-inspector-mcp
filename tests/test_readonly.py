"""Tests for read-only verification."""

from unittest.mock import MagicMock, patch

import pytest

from db_inspector_mcp.readonly import verify_readonly_for_registry


def _registry_with_backend(name: str, backend) -> MagicMock:
    registry = MagicMock()
    registry.list_backends.return_value = [name]
    registry.get.return_value = backend
    return registry


def test_verify_readonly_skipped_when_disabled():
    registry = MagicMock()
    verify_readonly_for_registry(
        {"DB_MCP_VERIFY_READONLY": "false"},
        registry,
    )
    registry.list_backends.assert_not_called()


def test_verify_readonly_fails_on_write_detected():
    backend = MagicMock()
    backend.sql_dialect = "mssql"
    backend.verify_readonly.return_value = {
        "readonly": False,
        "details": "write role detected",
    }
    registry = _registry_with_backend("sync", backend)

    with pytest.raises(ValueError, match="Write permissions detected"):
        verify_readonly_for_registry(
            {"DB_MCP_VERIFY_READONLY": "true"},
            registry,
            exit_on_write_failure=False,
        )


def test_verify_readonly_fails_on_inconclusive():
    backend = MagicMock()
    backend.sql_dialect = "postgres"
    backend.verify_readonly.return_value = {
        "readonly": None,
        "details": "verification timed out after 10.0s",
    }
    registry = _registry_with_backend("offline", backend)

    with pytest.raises(ValueError, match="Could not verify read-only status"):
        verify_readonly_for_registry(
            {"DB_MCP_VERIFY_READONLY": "true"},
            registry,
            exit_on_write_failure=False,
        )


def test_verify_readonly_exits_on_startup_failure():
    backend = MagicMock()
    backend.sql_dialect = "mssql"
    backend.verify_readonly.return_value = {"readonly": False, "details": "writable"}
    registry = _registry_with_backend("sync", backend)

    with patch("db_inspector_mcp.readonly.sys.exit", side_effect=SystemExit(1)) as mock_exit:
        with pytest.raises(SystemExit):
            verify_readonly_for_registry(
                {"DB_MCP_VERIFY_READONLY": "true"},
                registry,
                exit_on_write_failure=True,
            )
    mock_exit.assert_called_once_with(1)


def test_verify_readonly_skips_access_backends():
    access_backend = MagicMock()
    access_backend.sql_dialect = "access"
    registry = _registry_with_backend("sectbl", access_backend)

    verify_readonly_for_registry(
        {"DB_MCP_VERIFY_READONLY": "true"},
        registry,
        exit_on_write_failure=False,
    )
    access_backend.verify_readonly.assert_not_called()
