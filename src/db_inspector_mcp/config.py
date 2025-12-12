"""Configuration management for db-inspector-mcp."""

import os
from typing import Any

from .backends.base import DatabaseBackend
from .backends.mssql import MSSQLBackend
from .backends.postgres import PostgresBackend
from .security import check_data_access_permission, get_permission_error_message


def load_config() -> dict[str, Any]:
    """
    Load configuration from environment variables.
    
    Returns:
        Dictionary with configuration values
    """
    return {
        "DB_BACKEND": os.getenv("DB_BACKEND", "").lower(),
        "DB_CONNECTION_STRING": os.getenv("DB_CONNECTION_STRING", ""),
        "DB_QUERY_TIMEOUT_SECONDS": int(os.getenv("DB_QUERY_TIMEOUT_SECONDS", "30")),
        "DB_ALLOW_DATA_ACCESS": os.getenv("DB_ALLOW_DATA_ACCESS", "false"),
        "DB_ALLOW_PREVIEW": os.getenv("DB_ALLOW_PREVIEW", "false"),
        "DB_VERIFY_READONLY": os.getenv("DB_VERIFY_READONLY", "true"),
        "DB_READONLY_FAIL_ON_WRITE": os.getenv("DB_READONLY_FAIL_ON_WRITE", "false"),
    }


def get_backend() -> DatabaseBackend:
    """
    Create and return a database backend based on configuration.
    
    Returns:
        DatabaseBackend instance
        
    Raises:
        ValueError: If backend is not specified or invalid
        ValueError: If connection string is missing
    """
    config = load_config()
    
    backend_name = config["DB_BACKEND"]
    connection_string = config["DB_CONNECTION_STRING"]
    query_timeout = config["DB_QUERY_TIMEOUT_SECONDS"]
    
    if not backend_name:
        raise ValueError(
            "DB_BACKEND environment variable is required. "
            "Set DB_BACKEND=sqlserver or DB_BACKEND=postgres"
        )
    
    if not connection_string:
        raise ValueError(
            "DB_CONNECTION_STRING environment variable is required. "
            "Provide a valid database connection string."
        )
    
    if backend_name == "sqlserver":
        return MSSQLBackend(connection_string, query_timeout)
    elif backend_name == "postgres":
        return PostgresBackend(connection_string, query_timeout)
    else:
        raise ValueError(
            f"Unsupported backend: {backend_name}. "
            "Supported backends: sqlserver, postgres"
        )


def check_data_access(tool_name: str) -> None:
    """
    Check if a tool has data access permission.
    
    Args:
        tool_name: Name of the tool being called
        
    Raises:
        PermissionError: If data access is not authorized
    """
    config = load_config()
    if not check_data_access_permission(tool_name, config):
        error_msg = get_permission_error_message(tool_name)
        raise PermissionError(error_msg)


def get_config() -> dict[str, Any]:
    """
    Get the current configuration.
    
    Returns:
        Configuration dictionary
    """
    return load_config()

