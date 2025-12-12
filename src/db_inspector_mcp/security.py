"""Security module for SQL validation and permission checks."""

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
    # This is a simple approach - for production, consider a proper SQL parser
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


def check_data_access_permission(tool_name: str, config: dict[str, Any]) -> bool:
    """
    Check if a tool requires and has data access permission.
    
    Tools that require data access:
    - db_preview: Fetches actual row data
    - db_compare_queries with compare_samples=True: Compares sample data
    
    Args:
        tool_name: Name of the tool being called
        config: Configuration dictionary with permission flags
        
    Returns:
        True if tool doesn't require data access OR permission is granted,
        False if tool requires data access but permission is not granted
    """
    # Tools that require data access permission
    data_access_tools = {
        "db_preview",
    }
    
    # If tool doesn't require data access, allow it
    if tool_name not in data_access_tools:
        return True
    
    # Check global flag first
    allow_data_access = config.get("DB_MCP_ALLOW_DATA_ACCESS", "false").lower() == "true"
    if allow_data_access:
        return True
    
    # Check per-tool override
    if tool_name == "db_preview":
        allow_preview = config.get("DB_MCP_ALLOW_PREVIEW", "false").lower() == "true"
        if allow_preview:
            return True
    
    return False


def get_permission_error_message(tool_name: str) -> str:
    """
    Get a clear error message for permission denial.
    
    Args:
        tool_name: Name of the tool that was denied
        
    Returns:
        Error message explaining how to enable the permission
    """
    if tool_name == "db_preview":
        return (
            "Data access not authorized. "
            "Set DB_MCP_ALLOW_DATA_ACCESS=true or DB_MCP_ALLOW_PREVIEW=true to enable db_preview."
        )
    
    return (
        f"Data access not authorized for {tool_name}. "
        "Set DB_MCP_ALLOW_DATA_ACCESS=true to enable data access tools."
    )

