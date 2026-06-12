"""Tests for MCP tools."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from db_inspector_mcp.security import validate_readonly_sql
from db_inspector_mcp.tools import (
    db_check_readonly_status,
    db_count_query_results,
    db_explain,
    db_get_query_columns,
    db_list_databases,
    db_list_tables,
    db_list_views,
    db_measure_query,
    db_preview,
    db_sql_help,
    db_sum_query_column,
    mcp,
)
from mcp.server.fastmcp.utilities.context_injection import find_context_parameter


def test_db_tool_registers_context_injection():
    """db_tool wrapper must expose ctx for FastMCP injection."""
    tool = mcp._tool_manager.get_tool("db_preview")
    assert tool is not None
    assert tool.context_kwarg == "ctx"
    assert find_context_parameter(tool.fn) == "ctx"
    assert "query" in tool.parameters["properties"]
    assert "args" not in tool.parameters["properties"]
    assert "kwargs" not in tool.parameters["properties"]


@pytest.fixture
def mock_backend():
    """Create a mock backend for testing."""
    backend = MagicMock()
    backend.count_query_results.return_value = 100
    backend.get_query_columns.return_value = [
        {"name": "id", "type": "int", "nullable": False},
        {"name": "amount", "type": "decimal", "nullable": True},
    ]
    backend.sum_query_column.return_value = 1234.56
    backend.measure_query.return_value = {
        "execution_time_ms": 50.0,
        "row_count": 10,
        "hit_limit": False,
    }
    backend.preview.return_value = [{"id": 1, "name": "test"}]
    backend.explain_query.return_value = "<plan>"
    backend.list_tables.return_value = [{"name": "users", "schema": "dbo"}]
    backend.list_views.return_value = [
        {"name": "active_users", "schema": "dbo", "definition": "SELECT ..."}
    ]
    backend.verify_readonly.return_value = {
        "readonly": True,
        "details": "✓ Read-only",
    }
    backend.sql_dialect = "mssql"
    return backend


@pytest.fixture
def workspace_ctx(mock_backend):
    """Mock workspace manager and return the mock backend."""
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


@pytest.mark.anyio
async def test_db_count_query_results(workspace_ctx):
    """Test db_count_query_results tool."""
    registry, mock_backend = workspace_ctx
    result = await db_count_query_results(ctx=MagicMock(), query="SELECT * FROM users")
    assert result["count"] == 100
    mock_backend.count_query_results.assert_called_once_with("SELECT * FROM users")


@pytest.mark.anyio
async def test_db_get_query_columns(workspace_ctx):
    """Test db_get_query_columns tool."""
    result = await db_get_query_columns(ctx=MagicMock(), query="SELECT * FROM users")
    assert "columns" in result
    assert len(result["columns"]) >= 1


@pytest.mark.anyio
async def test_db_sum_query_column(workspace_ctx):
    """Test db_sum_query_column tool."""
    result = await db_sum_query_column(
        ctx=MagicMock(), query="SELECT amount FROM transactions", column="amount",
    )
    assert result["sum"] == 1234.56


@pytest.mark.anyio
async def test_db_sum_query_column_rejects_unknown_column(workspace_ctx):
    """Test db_sum_query_column rejects columns not present in query output."""
    with pytest.raises(ValueError, match="was not found"):
        await db_sum_query_column(
            ctx=MagicMock(), query="SELECT amount FROM transactions", column="nonexistent",
        )


@pytest.mark.anyio
async def test_db_sum_query_column_rejects_malicious_column_name(workspace_ctx):
    """Test db_sum_query_column rejects injection-like column values."""
    with pytest.raises(ValueError, match="was not found"):
        await db_sum_query_column(
            ctx=MagicMock(),
            query="SELECT amount FROM transactions",
            column='amount"; DROP TABLE users; --',
        )


@pytest.mark.anyio
async def test_db_measure_query(workspace_ctx):
    """Test db_measure_query tool."""
    result = await db_measure_query(ctx=MagicMock(), query="SELECT * FROM users", max_rows=1000)
    assert result["execution_time_ms"] == 50.0
    assert result["row_count"] == 10
    assert result["hit_limit"] is False


@pytest.mark.anyio
async def test_db_preview_requires_permission(workspace_ctx):
    """Test that db_preview requires permission."""
    with patch("db_inspector_mcp.tools.check_data_access") as mock_check:
        mock_check.side_effect = PermissionError("Not authorized")
        with pytest.raises(PermissionError):
            await db_preview(ctx=MagicMock(), query="SELECT * FROM users", max_rows=10)


@pytest.mark.anyio
async def test_db_explain(workspace_ctx):
    """Test db_explain tool."""
    result = await db_explain(ctx=MagicMock(), query="SELECT * FROM users")
    assert result["plan"] == "<plan>"


@pytest.mark.anyio
async def test_db_list_tables(workspace_ctx):
    """Test db_list_tables tool."""
    result = await db_list_tables(ctx=MagicMock())
    assert "tables" in result
    assert len(result["tables"]) == 1


@pytest.mark.anyio
async def test_db_list_views(workspace_ctx):
    """Test db_list_views tool."""
    result = await db_list_views(ctx=MagicMock())
    assert "views" in result
    assert len(result["views"]) == 1


@pytest.mark.anyio
async def test_db_check_readonly_status(workspace_ctx):
    """Test db_check_readonly_status tool."""
    result = await db_check_readonly_status(ctx=MagicMock())
    assert result["readonly"] is True
    assert "details" in result


@pytest.mark.anyio
async def test_tools_reject_write_operations(workspace_ctx):
    """Test that tools reject write operations."""
    with pytest.raises(ValueError, match="INSERT"):
        await db_count_query_results(ctx=MagicMock(), query="INSERT INTO users VALUES (1)")

    with pytest.raises(ValueError, match="SELECT \\.\\.\\. INTO"):
        await db_count_query_results(ctx=MagicMock(), query="SELECT * INTO users_copy FROM users")


@pytest.mark.anyio
async def test_db_list_databases_includes_dialect():
    """Test that db_list_databases includes dialect information."""
    mock_access_backend = MagicMock()
    mock_access_backend.sql_dialect = "access"
    mock_access_backend.is_connected = True
    mock_access_backend.get_object_counts.return_value = {"tables": 10}

    mock_mssql_backend = MagicMock()
    mock_mssql_backend.sql_dialect = "mssql"
    mock_mssql_backend.is_connected = False

    registry = MagicMock()
    registry.list_backends.return_value = ["legacy", "new"]
    registry.get_default_name.return_value = "legacy"
    registry.get.side_effect = lambda name: (
        mock_access_backend if name == "legacy" else mock_mssql_backend
    )

    async def fake_get_registry_for(ctx):
        return registry, {}, Path("/fake/workspace")

    with patch("db_inspector_mcp.tools.get_workspace_manager") as mock_mgr, \
         patch("db_inspector_mcp.tools.refresh_logging_from_env"):
        manager = MagicMock()
        manager.get_registry_for = fake_get_registry_for
        mock_mgr.return_value = manager

        result = await db_list_databases(ctx=MagicMock())

    assert "databases" in result
    assert len(result["databases"]) == 2

    legacy_db = next(db for db in result["databases"] if db["name"] == "legacy")
    assert legacy_db["dialect"] == "access"
    assert legacy_db["is_default"] is True
    assert legacy_db["status"] == "connected"
    assert legacy_db["object_counts"] == {"tables": 10}
    mock_access_backend.get_object_counts.assert_called_once()

    new_db = next(db for db in result["databases"] if db["name"] == "new")
    assert new_db["dialect"] == "mssql"
    assert new_db["is_default"] is False
    assert new_db["status"] == "not_connected"
    assert new_db["object_counts"] == {}
    mock_mssql_backend.get_object_counts.assert_not_called()


@pytest.mark.anyio
async def test_db_sql_help_access_joins():
    """Test db_sql_help returns Access JOIN syntax help."""
    mock_backend = MagicMock()
    mock_backend.sql_dialect = "access"
    registry = MagicMock()
    registry.get.return_value = mock_backend

    async def fake_get_registry_for(ctx):
        return registry, {}, Path("/fake/workspace")

    with patch("db_inspector_mcp.tools.get_workspace_manager") as mock_mgr, \
         patch("db_inspector_mcp.tools.refresh_logging_from_env"):
        manager = MagicMock()
        manager.get_registry_for = fake_get_registry_for
        mock_mgr.return_value = manager

        result = await db_sql_help(ctx=MagicMock(), topic="joins")

    assert result["dialect"] == "access"
    assert result["topic"] == "joins"
    assert "examples" in result
    assert "parentheses" in result["description"].lower()


@pytest.mark.anyio
async def test_db_sql_help_access_distinct_mentions_distinctrow():
    """Regression: the Access 'distinct' help topic must mention DISTINCTROW."""
    mock_backend = MagicMock()
    mock_backend.sql_dialect = "access"
    registry = MagicMock()
    registry.get.return_value = mock_backend

    async def fake_get_registry_for(ctx):
        return registry, {}, Path("/fake/workspace")

    with patch("db_inspector_mcp.tools.get_workspace_manager") as mock_mgr, \
         patch("db_inspector_mcp.tools.refresh_logging_from_env"):
        manager = MagicMock()
        manager.get_registry_for = fake_get_registry_for
        mock_mgr.return_value = manager

        topic = await db_sql_help(ctx=MagicMock(), topic="distinct")

    assert topic["dialect"] == "access"
    assert topic["topic"] == "distinct"
    assert "DISTINCTROW" in topic["title"]
    assert "DISTINCTROW" in topic["description"]
    assert "DISTINCTROW" in topic["pattern"]
    example_sqls = " ".join(ex["sql"] for ex in topic["examples"])
    assert "DISTINCTROW" in example_sqls

    summary = await db_sql_help(ctx=MagicMock(), topic="all")
    assert "DISTINCTROW" in " ".join(summary["summary"].keys()) or any(
        "DISTINCTROW" in v for v in summary["summary"].values()
    )


@pytest.mark.anyio
async def test_db_sql_help_access_all():
    """Test db_sql_help returns Access quick reference."""
    mock_backend = MagicMock()
    mock_backend.sql_dialect = "access"
    registry = MagicMock()
    registry.get.return_value = mock_backend

    async def fake_get_registry_for(ctx):
        return registry, {}, Path("/fake/workspace")

    with patch("db_inspector_mcp.tools.get_workspace_manager") as mock_mgr, \
         patch("db_inspector_mcp.tools.refresh_logging_from_env"):
        manager = MagicMock()
        manager.get_registry_for = fake_get_registry_for
        mock_mgr.return_value = manager

        result = await db_sql_help(ctx=MagicMock(), topic="all")

    assert result["dialect"] == "access"
    assert "summary" in result
    assert "Multiple JOINs" in result["summary"]
    assert "Conditionals" in result["summary"]


@pytest.mark.anyio
async def test_db_sql_help_invalid_topic():
    """Test db_sql_help returns error for invalid topic."""
    mock_backend = MagicMock()
    mock_backend.sql_dialect = "access"
    registry = MagicMock()
    registry.get.return_value = mock_backend

    async def fake_get_registry_for(ctx):
        return registry, {}, Path("/fake/workspace")

    with patch("db_inspector_mcp.tools.get_workspace_manager") as mock_mgr, \
         patch("db_inspector_mcp.tools.refresh_logging_from_env"):
        manager = MagicMock()
        manager.get_registry_for = fake_get_registry_for
        mock_mgr.return_value = manager

        result = await db_sql_help(ctx=MagicMock(), topic="invalid_topic")

    assert "error" in result
    assert "available_topics" in result


@pytest.mark.anyio
async def test_db_sql_help_defaults_to_all():
    """Test db_sql_help defaults to 'all' when no topic specified."""
    mock_backend = MagicMock()
    mock_backend.sql_dialect = "access"
    registry = MagicMock()
    registry.get.return_value = mock_backend

    async def fake_get_registry_for(ctx):
        return registry, {}, Path("/fake/workspace")

    with patch("db_inspector_mcp.tools.get_workspace_manager") as mock_mgr, \
         patch("db_inspector_mcp.tools.refresh_logging_from_env"):
        manager = MagicMock()
        manager.get_registry_for = fake_get_registry_for
        mock_mgr.return_value = manager

        result = await db_sql_help(ctx=MagicMock())

    assert result["dialect"] == "access"
    assert result["topic"] == "all"
    assert "summary" in result


@pytest.mark.anyio
async def test_db_list_databases_empty_returns_error():
    """An empty registry yields an explicit error, not a silent empty success."""
    registry = MagicMock()
    registry.list_backends.return_value = []
    registry.get_default_name.return_value = None

    async def fake_get_registry_for(ctx):
        return registry, {}, Path("/fake/workspace")

    with patch("db_inspector_mcp.tools.get_workspace_manager") as mock_mgr, \
         patch("db_inspector_mcp.tools.refresh_logging_from_env"):
        manager = MagicMock()
        manager.get_registry_for = fake_get_registry_for
        mock_mgr.return_value = manager

        result = await db_list_databases(ctx=MagicMock())

    assert result["databases"] == []
    assert result["default"] is None
    assert "error" in result
    assert "No database backends" in result["error"]


@pytest.mark.anyio
async def test_db_list_databases_does_not_connect_disconnected_backend():
    """db_list_databases must not open a connection for disconnected backends."""
    backend = MagicMock()
    backend.sql_dialect = "access"
    backend.is_connected = False
    backend.get_object_counts.side_effect = AssertionError(
        "db_list_databases must not connect to a disconnected backend"
    )

    registry = MagicMock()
    registry.list_backends.return_value = ["sync"]
    registry.get_default_name.return_value = "sync"
    registry.get.return_value = backend

    async def fake_get_registry_for(ctx):
        return registry, {}, Path("/fake/workspace")

    with patch("db_inspector_mcp.tools.get_workspace_manager") as mock_mgr, \
         patch("db_inspector_mcp.tools.refresh_logging_from_env"):
        manager = MagicMock()
        manager.get_registry_for = fake_get_registry_for
        mock_mgr.return_value = manager

        result = await db_list_databases(ctx=MagicMock())

    assert len(result["databases"]) == 1
    assert result["databases"][0]["status"] == "not_connected"
    assert result["databases"][0]["object_counts"] == {}
    backend.get_object_counts.assert_not_called()
