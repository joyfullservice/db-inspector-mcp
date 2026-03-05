"""Security module for SQL validation and permission checks."""

import os
import re
from typing import Any


# SQL keywords that indicate write operations
_WRITE_KEYWORDS = [
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE",
    "ALTER",
    "DROP",
    "TRUNCATE",
    "EXEC",
    "EXECUTE",
    "CALL",
]


def validate_readonly_sql(sql: str) -> None:
    """
    Validate that SQL contains no write operations.
    
    Uses regex with word boundaries to match whole words only,
    avoiding false positives in comments or string literals.
    
    Args:
        sql: SQL query string to validate
        
    Raises:
        ValueError: If write operations are detected in the SQL
    """
    # Normalize SQL: remove comments and string literals to avoid false positives
    sql_upper = sql.upper()
    
    # Remove single-line comments (-- ...)
    sql_upper = re.sub(r"--.*", "", sql_upper)
    
    # Remove multi-line comments (/* ... */)
    sql_upper = re.sub(r"/\*.*?\*/", "", sql_upper, flags=re.DOTALL)
    
    # Remove string literals (both single and double quotes)
    # This is a simplified approach - may not handle all edge cases
    sql_upper = re.sub(r"'[^']*'", "", sql_upper)
    sql_upper = re.sub(r'"[^"]*"', "", sql_upper)
    
    # Check for write keywords with word boundaries
    for keyword in _WRITE_KEYWORDS:
        # Use word boundaries to match whole words only
        pattern = r"\b" + re.escape(keyword) + r"\b"
        if re.search(pattern, sql_upper):
            raise ValueError(
                f"Write operation detected: '{keyword}' is not allowed. "
                "This tool only supports read-only operations (SELECT queries)."
            )


def check_data_access_permission(
    tool_name: str, config: dict[str, Any], database: str | None = None,
) -> bool:
    """
    Check if a tool requires and has data access permission.
    
    Tools that require data access:
    - db_preview: Fetches actual row data
    - db_compare_queries with compare_samples=True: Compares sample data

    Per-connection overrides (``DB_MCP_<NAME>_ALLOW_DATA_ACCESS``,
    ``DB_MCP_<NAME>_ALLOW_PREVIEW``) take precedence over the global
    settings when *database* is provided.  If no per-connection variable
    is set, the global value is used as a fallback.
    
    Args:
        tool_name: Name of the tool being called
        config: Configuration dictionary with global permission flags
        database: Optional connection name for per-connection lookup
        
    Returns:
        True if tool doesn't require data access OR permission is granted,
        False if tool requires data access but permission is not granted
    """
    data_access_tools = {
        "db_preview",
    }
    
    if tool_name not in data_access_tools:
        return True
    
    # --- per-connection override (checked first) ---
    if database:
        name_upper = database.upper()

        per_conn = os.getenv(f"DB_MCP_{name_upper}_ALLOW_DATA_ACCESS")
        if per_conn is not None:
            return per_conn.lower() == "true"

        if tool_name == "db_preview":
            per_conn_preview = os.getenv(f"DB_MCP_{name_upper}_ALLOW_PREVIEW")
            if per_conn_preview is not None:
                return per_conn_preview.lower() == "true"
    
    # --- global fallback ---
    allow_data_access = config.get("DB_MCP_ALLOW_DATA_ACCESS", "false").lower() == "true"
    if allow_data_access:
        return True
    
    if tool_name == "db_preview":
        allow_preview = config.get("DB_MCP_ALLOW_PREVIEW", "false").lower() == "true"
        if allow_preview:
            return True
    
    return False


def get_permission_error_message(tool_name: str, database: str | None = None) -> str:
    """
    Get a clear error message for permission denial.
    
    Args:
        tool_name: Name of the tool that was denied
        database: Optional connection name for per-connection hint
        
    Returns:
        Error message explaining how to enable the permission
    """
    if database:
        name_upper = database.upper()
        per_conn_hint = (
            f"DB_MCP_{name_upper}_ALLOW_DATA_ACCESS=true"
        )
        if tool_name == "db_preview":
            return (
                f"Data access not authorized for connection '{database}'. "
                f"Set {per_conn_hint} or DB_MCP_ALLOW_DATA_ACCESS=true to enable db_preview."
            )
        return (
            f"Data access not authorized for {tool_name} on connection '{database}'. "
            f"Set {per_conn_hint} or DB_MCP_ALLOW_DATA_ACCESS=true to enable data access tools."
        )

    if tool_name == "db_preview":
        return (
            "Data access not authorized. "
            "Set DB_MCP_ALLOW_DATA_ACCESS=true or DB_MCP_ALLOW_PREVIEW=true to enable db_preview."
        )
    
    return (
        f"Data access not authorized for {tool_name}. "
        "Set DB_MCP_ALLOW_DATA_ACCESS=true to enable data access tools."
    )

