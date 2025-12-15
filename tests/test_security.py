"""Tests for security module."""

import pytest

from db_inspector_mcp.security import (
    check_data_access_permission,
    get_permission_error_message,
    validate_readonly_sql,
)


def test_validate_readonly_sql_allows_select():
    """Test that SELECT queries are allowed."""
    validate_readonly_sql("SELECT * FROM users")
    validate_readonly_sql("SELECT id, name FROM users WHERE active = 1")
    validate_readonly_sql("SELECT COUNT(*) FROM orders")


def test_validate_readonly_sql_rejects_insert():
    """Test that INSERT is rejected."""
    with pytest.raises(ValueError, match="INSERT"):
        validate_readonly_sql("INSERT INTO users VALUES (1, 'test')")


def test_validate_readonly_sql_rejects_update():
    """Test that UPDATE is rejected."""
    with pytest.raises(ValueError, match="UPDATE"):
        validate_readonly_sql("UPDATE users SET name = 'test' WHERE id = 1")


def test_validate_readonly_sql_rejects_delete():
    """Test that DELETE is rejected."""
    with pytest.raises(ValueError, match="DELETE"):
        validate_readonly_sql("DELETE FROM users WHERE id = 1")


def test_validate_readonly_sql_rejects_create():
    """Test that CREATE is rejected."""
    with pytest.raises(ValueError, match="CREATE"):
        validate_readonly_sql("CREATE TABLE test (id INT)")


def test_validate_readonly_sql_handles_comments():
    """Test that comments don't cause false positives."""
    # Should not raise error even though "INSERT" appears in comment
    validate_readonly_sql("SELECT * FROM users -- INSERT is not allowed")
    validate_readonly_sql("SELECT * FROM users /* INSERT test */")


def test_check_data_access_permission_allows_metadata_tools():
    """Test that metadata tools don't require permission."""
    config = {"DB_MCP_ALLOW_DATA_ACCESS": "false"}
    assert check_data_access_permission("db_row_count", config) is True
    assert check_data_access_permission("db_columns", config) is True
    assert check_data_access_permission("db_explain", config) is True


def test_check_data_access_permission_requires_permission_for_preview():
    """Test that db_preview requires permission."""
    config = {"DB_MCP_ALLOW_DATA_ACCESS": "false", "DB_MCP_ALLOW_PREVIEW": "false"}
    assert check_data_access_permission("db_preview", config) is False


def test_check_data_access_permission_allows_with_global_flag():
    """Test that global flag enables data access."""
    config = {"DB_MCP_ALLOW_DATA_ACCESS": "true"}
    assert check_data_access_permission("db_preview", config) is True


def test_check_data_access_permission_allows_with_per_tool_flag():
    """Test that per-tool flag enables specific tool."""
    config = {"DB_MCP_ALLOW_DATA_ACCESS": "false", "DB_MCP_ALLOW_PREVIEW": "true"}
    assert check_data_access_permission("db_preview", config) is True


def test_get_permission_error_message():
    """Test that error messages are clear."""
    msg = get_permission_error_message("db_preview")
    assert "DB_MCP_ALLOW_DATA_ACCESS" in msg or "DB_MCP_ALLOW_PREVIEW" in msg

