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


def test_validate_readonly_sql_rejects_merge():
    """Test that MERGE is rejected."""
    with pytest.raises(ValueError, match="MERGE"):
        validate_readonly_sql("MERGE INTO users AS t USING incoming AS s ON t.id = s.id")


def test_validate_readonly_sql_rejects_select_into():
    """Test that SELECT INTO is rejected because it creates tables."""
    with pytest.raises(ValueError, match="SELECT \\.\\.\\. INTO"):
        validate_readonly_sql("SELECT * INTO users_copy FROM users")


def test_validate_readonly_sql_rejects_non_select_statements():
    """Test that non-SELECT statements are rejected even if not in deny list."""
    with pytest.raises(ValueError, match="Only read-only SELECT queries are allowed"):
        validate_readonly_sql("SHOW TABLES")


def test_validate_readonly_sql_allows_cte_select():
    """Test that CTE queries are allowed."""
    validate_readonly_sql(
        """
        WITH active_users AS (
            SELECT id, name FROM users WHERE active = 1
        )
        SELECT * FROM active_users
        """
    )


def test_validate_readonly_sql_handles_comments():
    """Test that comments don't cause false positives."""
    # Should not raise error even though "INSERT" appears in comment
    validate_readonly_sql("SELECT * FROM users -- INSERT is not allowed")
    validate_readonly_sql("SELECT * FROM users /* INSERT test */")


def test_validate_readonly_sql_handles_literals():
    """Test that write-like keywords in string literals don't trigger false positives."""
    validate_readonly_sql("SELECT 'DROP TABLE users' AS text_val")


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


# ---------------------------------------------------------------------------
# Per-connection data access overrides
# ---------------------------------------------------------------------------

def test_per_connection_allow_overrides_global_deny(monkeypatch):
    """Per-connection ALLOW_DATA_ACCESS=true overrides global false."""
    monkeypatch.setenv("DB_MCP_LEGACY_ALLOW_DATA_ACCESS", "true")
    config = {"DB_MCP_ALLOW_DATA_ACCESS": "false", "DB_MCP_ALLOW_PREVIEW": "false"}
    assert check_data_access_permission("db_preview", config, database="legacy") is True


def test_per_connection_deny_overrides_global_allow(monkeypatch):
    """Per-connection ALLOW_DATA_ACCESS=false overrides global true."""
    monkeypatch.setenv("DB_MCP_PROD_ALLOW_DATA_ACCESS", "false")
    config = {"DB_MCP_ALLOW_DATA_ACCESS": "true"}
    assert check_data_access_permission("db_preview", config, database="prod") is False


def test_per_connection_allow_preview_overrides_global_deny(monkeypatch):
    """Per-connection ALLOW_PREVIEW=true overrides global false."""
    monkeypatch.setenv("DB_MCP_LEGACY_ALLOW_PREVIEW", "true")
    config = {"DB_MCP_ALLOW_DATA_ACCESS": "false", "DB_MCP_ALLOW_PREVIEW": "false"}
    assert check_data_access_permission("db_preview", config, database="legacy") is True


def test_per_connection_data_access_takes_priority_over_preview(monkeypatch):
    """Per-connection ALLOW_DATA_ACCESS is checked before ALLOW_PREVIEW."""
    monkeypatch.setenv("DB_MCP_DEV_ALLOW_DATA_ACCESS", "false")
    monkeypatch.setenv("DB_MCP_DEV_ALLOW_PREVIEW", "true")
    config = {"DB_MCP_ALLOW_DATA_ACCESS": "false"}
    assert check_data_access_permission("db_preview", config, database="dev") is False


def test_per_connection_falls_back_to_global(monkeypatch):
    """Without per-connection vars, global setting is used."""
    monkeypatch.delenv("DB_MCP_NEW_ALLOW_DATA_ACCESS", raising=False)
    monkeypatch.delenv("DB_MCP_NEW_ALLOW_PREVIEW", raising=False)
    config = {"DB_MCP_ALLOW_DATA_ACCESS": "true"}
    assert check_data_access_permission("db_preview", config, database="new") is True


def test_per_connection_falls_back_to_global_deny(monkeypatch):
    """Without per-connection vars, global deny is honoured."""
    monkeypatch.delenv("DB_MCP_NEW_ALLOW_DATA_ACCESS", raising=False)
    monkeypatch.delenv("DB_MCP_NEW_ALLOW_PREVIEW", raising=False)
    config = {"DB_MCP_ALLOW_DATA_ACCESS": "false", "DB_MCP_ALLOW_PREVIEW": "false"}
    assert check_data_access_permission("db_preview", config, database="new") is False


def test_per_connection_metadata_tools_always_allowed(monkeypatch):
    """Metadata tools are allowed regardless of per-connection settings."""
    monkeypatch.setenv("DB_MCP_PROD_ALLOW_DATA_ACCESS", "false")
    config = {"DB_MCP_ALLOW_DATA_ACCESS": "false"}
    assert check_data_access_permission("db_explain", config, database="prod") is True


def test_per_connection_error_message_includes_connection_name():
    """Error message mentions per-connection env var when database is given."""
    msg = get_permission_error_message("db_preview", database="legacy")
    assert "DB_MCP_LEGACY_ALLOW_DATA_ACCESS" in msg
    assert "legacy" in msg


def test_per_connection_error_message_without_database():
    """Error message is unchanged when no database is provided."""
    msg = get_permission_error_message("db_preview")
    assert "DB_MCP_ALLOW_DATA_ACCESS" in msg
    assert "DB_MCP_ALLOW_PREVIEW" in msg

