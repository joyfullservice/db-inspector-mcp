"""FastMCP contract and integration tests for db_tool registration.

These tests invoke tools through Tool.run() (schema validation + Context
injection), not by calling the wrapped function directly. That is the path
Cursor uses and the layer that regressed when the decorator metadata broke.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.utilities.context_injection import find_context_parameter

from db_inspector_mcp.tools import mcp

# All tools registered via @db_tool in tools.py (keep in sync when adding tools).
EXPECTED_TOOL_NAMES = frozenset({
    "db_check_readonly_status",
    "db_compare_queries",
    "db_count_query_results",
    "db_explain",
    "db_get_access_query_definition",
    "db_get_query_columns",
    "db_list_databases",
    "db_list_tables",
    "db_list_views",
    "db_measure_query",
    "db_preview",
    "db_sql_help",
    "db_sum_query_column",
})

_FORBIDDEN_SCHEMA_PARAMS = frozenset({"ctx", "args", "kwargs", "self"})


async def _run_tool(name: str, arguments: dict) -> object:
    """Invoke a tool through the real FastMCP Tool.run path."""
    tool = mcp._tool_manager.get_tool(name)
    assert tool is not None, f"Tool {name!r} is not registered"
    return await tool.run(arguments, context=MagicMock())


@pytest.fixture
def mock_backend():
    """Backend mock with typical return values."""
    backend = MagicMock()
    backend.count_query_results.return_value = 100
    backend.get_query_columns.return_value = [
        {"name": "id", "type": "int", "nullable": False},
    ]
    backend.preview.return_value = [{"id": 1}]
    backend.list_tables.return_value = [{"name": "users", "schema": "dbo"}]
    backend.sql_dialect = "access"
    return backend


@pytest.fixture
def patched_workspace(mock_backend):
    """Patch workspace resolution so Tool.run can reach tool bodies."""
    registry = MagicMock()
    registry.get.return_value = mock_backend
    registry.list_backends.return_value = ["default"]
    registry.get_default_name.return_value = "default"

    async def fake_get_registry_for(ctx):
        return registry, {}, Path("/fake/workspace")

    with patch("db_inspector_mcp.tools.get_workspace_manager") as mock_mgr, \
         patch("db_inspector_mcp.tools.refresh_logging_from_env"):
        manager = MagicMock()
        manager.get_registry_for = fake_get_registry_for
        mock_mgr.return_value = manager
        yield registry, mock_backend


class TestRegisteredToolContract:
    """Registry-driven checks for every @db_tool registration."""

    def test_all_expected_tools_registered(self):
        registered = {t.name for t in mcp._tool_manager.list_tools()}
        assert registered == EXPECTED_TOOL_NAMES

    @pytest.mark.parametrize("tool_name", sorted(EXPECTED_TOOL_NAMES))
    def test_tool_metadata_contract(self, tool_name: str):
        tool = mcp._tool_manager.get_tool(tool_name)
        assert tool is not None
        assert tool.name == tool_name
        assert tool.context_kwarg == "ctx"
        assert find_context_parameter(tool.fn) == "ctx"

        props = set(tool.parameters.get("properties", {}))
        assert props & _FORBIDDEN_SCHEMA_PARAMS == set(), (
            f"{tool_name} schema leaked forbidden params: {props & _FORBIDDEN_SCHEMA_PARAMS}"
        )


class TestDbPreviewSchema:
    """Snapshot guard for a representative tool schema."""

    def test_db_preview_schema_shape(self):
        tool = mcp._tool_manager.get_tool("db_preview")
        assert tool is not None
        schema = tool.parameters
        assert schema["required"] == ["query"]
        props = schema["properties"]
        assert "query" in props
        assert "max_rows" in props
        assert "database" in props
        assert "max_rows" not in schema["required"]
        assert "database" not in schema["required"]
        assert props["max_rows"].get("default") == 100


@pytest.mark.anyio
class TestToolRunIntegration:
    """Representative tools invoked via Tool.run (not direct wrapper calls)."""

    async def test_db_count_query_results_via_tool_run(self, patched_workspace):
        registry, mock_backend = patched_workspace
        result = await _run_tool(
            "db_count_query_results",
            {"query": "SELECT * FROM users"},
        )
        assert result == {"count": 100}
        mock_backend.count_query_results.assert_called_once_with("SELECT * FROM users")

    async def test_db_list_tables_via_tool_run(self, patched_workspace):
        registry, mock_backend = patched_workspace
        result = await _run_tool("db_list_tables", {})
        assert "tables" in result
        assert len(result["tables"]) == 1
        mock_backend.list_tables.assert_called_once()

    async def test_db_sql_help_via_tool_run(self, patched_workspace):
        """Help content path — no live DB connection, uses registry dialect only."""
        registry, mock_backend = patched_workspace
        mock_backend.sql_dialect = "access"
        result = await _run_tool("db_sql_help", {"topic": "joins"})
        assert result["dialect"] == "access"
        assert result["topic"] == "joins"
        assert "title" in result

    async def test_db_preview_permission_denied_via_tool_run(self, patched_workspace):
        registry, mock_backend = patched_workspace
        with patch("db_inspector_mcp.tools.check_data_access") as mock_check:
            mock_check.side_effect = PermissionError("Data access not authorized")
            with pytest.raises(ToolError, match="Data access not authorized"):
                await _run_tool(
                    "db_preview",
                    {
                        "query": "SELECT TOP 5 fldCoID FROM dbo.tblCo",
                        "max_rows": 5,
                        "database": "offline",
                    },
                )
