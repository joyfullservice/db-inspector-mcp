"""Tests for MCP tools."""

from unittest.mock import MagicMock, patch

import pytest

from db_inspector_mcp.config import check_data_access
from db_inspector_mcp.security import validate_readonly_sql
from db_inspector_mcp.tools import (
    db_check_readonly_status,
    db_count_query_results,
    db_explain,
    db_get_query_columns,
    db_list_tables,
    db_list_views,
    db_measure_query,
    db_preview,
    db_sum_query_column,
)


@pytest.fixture
def mock_backend():
    """Create a mock backend for testing."""
    backend = MagicMock()
    backend.count_query_results.return_value = 100
    backend.get_query_columns.return_value = [
        {"name": "id", "type": "int", "nullable": False}
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
    return backend


@pytest.fixture
def mock_registry(mock_backend):
    """Mock the registry to return mock_backend."""
    with patch("db_inspector_mcp.tools.get_registry") as mock_get_registry:
        registry = MagicMock()
        registry.get.return_value = mock_backend
        mock_get_registry.return_value = registry
        yield mock_backend


def test_db_count_query_results(mock_registry):
    """Test db_count_query_results tool."""
    result = db_count_query_results("SELECT * FROM users")
    assert result["count"] == 100
    mock_registry.count_query_results.assert_called_once_with("SELECT * FROM users")


def test_db_get_query_columns(mock_registry):
    """Test db_get_query_columns tool."""
    result = db_get_query_columns("SELECT * FROM users")
    assert "columns" in result
    assert len(result["columns"]) == 1


def test_db_sum_query_column(mock_registry):
    """Test db_sum_query_column tool."""
    result = db_sum_query_column("SELECT amount FROM transactions", "amount")
    assert result["sum"] == 1234.56


def test_db_measure_query(mock_registry):
    """Test db_measure_query tool."""
    result = db_measure_query("SELECT * FROM users", max_rows=1000)
    assert result["execution_time_ms"] == 50.0
    assert result["row_count"] == 10
    assert result["hit_limit"] is False


def test_db_preview_requires_permission(mock_registry):
    """Test that db_preview requires permission."""
    with patch("db_inspector_mcp.tools.check_data_access") as mock_check:
        mock_check.side_effect = PermissionError("Not authorized")
        with pytest.raises(PermissionError):
            db_preview("SELECT * FROM users", max_rows=10)


def test_db_explain(mock_registry):
    """Test db_explain tool."""
    result = db_explain("SELECT * FROM users")
    assert result["plan"] == "<plan>"


def test_db_list_tables(mock_registry):
    """Test db_list_tables tool."""
    result = db_list_tables()
    assert "tables" in result
    assert len(result["tables"]) == 1


def test_db_list_views(mock_registry):
    """Test db_list_views tool."""
    result = db_list_views()
    assert "views" in result
    assert len(result["views"]) == 1


def test_db_check_readonly_status(mock_registry):
    """Test db_check_readonly_status tool."""
    result = db_check_readonly_status()
    assert result["readonly"] is True
    assert "details" in result


def test_tools_reject_write_operations(mock_registry):
    """Test that tools reject write operations."""
    with pytest.raises(ValueError, match="INSERT"):
        db_count_query_results("INSERT INTO users VALUES (1)")

