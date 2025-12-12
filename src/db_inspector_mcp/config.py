"""Configuration management for db-inspector-mcp."""

import os
from typing import Any

from .backends.access import AccessBackend
from .backends.base import DatabaseBackend
from .backends.mssql import MSSQLBackend
from .backends.postgres import PostgresBackend
from .backends.registry import BackendRegistry, get_registry
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


def _create_backend(backend_type: str, connection_string: str, query_timeout: int) -> DatabaseBackend:
    """
    Create a backend instance based on type.
    
    Args:
        backend_type: Type of backend (sqlserver, postgres, access)
        connection_string: Database connection string
        query_timeout: Query timeout in seconds
        
    Returns:
        DatabaseBackend instance
        
    Raises:
        ValueError: If backend type is unsupported
    """
    backend_type = backend_type.lower()
    
    if backend_type == "sqlserver":
        return MSSQLBackend(connection_string, query_timeout)
    elif backend_type == "postgres":
        return PostgresBackend(connection_string, query_timeout)
    elif backend_type == "access":
        return AccessBackend(connection_string, query_timeout)
    else:
        raise ValueError(
            f"Unsupported backend: {backend_type}. "
            "Supported backends: sqlserver, postgres, access"
        )


def get_backend() -> DatabaseBackend:
    """
    Create and return a database backend based on configuration.
    
    This function maintains backward compatibility with single-database configuration.
    For multi-database support, use initialize_backends() instead.
    
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
            "Set DB_BACKEND=sqlserver, postgres, or access"
        )
    
    if not connection_string:
        raise ValueError(
            "DB_CONNECTION_STRING environment variable is required. "
            "Provide a valid database connection string."
        )
    
    return _create_backend(backend_name, connection_string, query_timeout)


def initialize_backends() -> BackendRegistry:
    """
    Initialize multiple database backends from environment variables.
    
    Supports two configuration patterns:
    1. Legacy single-database: DB_BACKEND, DB_CONNECTION_STRING (registered as "default")
    2. Multi-database: DB_<name>_BACKEND, DB_<name>_CONNECTION_STRING for each database
    
    Examples:
        # Single database (backward compatible)
        DB_BACKEND=sqlserver
        DB_CONNECTION_STRING=...
        
        # Multiple databases
        DB_SOURCE_BACKEND=access
        DB_SOURCE_CONNECTION_STRING=...
        DB_DEST_BACKEND=sqlserver
        DB_DEST_CONNECTION_STRING=...
        
    Returns:
        BackendRegistry with all configured backends
        
    Raises:
        ValueError: If configuration is invalid
    """
    registry = get_registry()
    config = load_config()
    query_timeout = config["DB_QUERY_TIMEOUT_SECONDS"]
    
    # Collect all database configurations
    db_configs: dict[str, dict[str, str]] = {}
    
    # Check for legacy single-database configuration
    if config["DB_BACKEND"] and config["DB_CONNECTION_STRING"]:
        db_configs["default"] = {
            "backend": config["DB_BACKEND"],
            "connection_string": config["DB_CONNECTION_STRING"],
        }
    
    # Scan for multi-database configurations (DB_<name>_BACKEND pattern)
    env_vars = dict(os.environ)
    for key, value in env_vars.items():
        if key.startswith("DB_") and key.endswith("_BACKEND"):
            # Extract database name (e.g., "SOURCE" from "DB_SOURCE_BACKEND")
            name_part = key[3:-7]  # Remove "DB_" prefix and "_BACKEND" suffix
            if name_part:
                db_name = name_part.lower()
                conn_key = f"DB_{name_part}_CONNECTION_STRING"
                
                if conn_key in env_vars:
                    db_configs[db_name] = {
                        "backend": value.lower(),
                        "connection_string": env_vars[conn_key],
                    }
    
    if not db_configs:
        raise ValueError(
            "No database configuration found. "
            "Set DB_BACKEND/DB_CONNECTION_STRING for single database, "
            "or DB_<name>_BACKEND/DB_<name>_CONNECTION_STRING for multiple databases."
        )
    
    # Register all backends
    default_set = False
    for db_name, db_config in db_configs.items():
        backend = _create_backend(
            db_config["backend"],
            db_config["connection_string"],
            query_timeout
        )
        # Set first backend as default, or "default" if it exists
        set_as_default = (db_name == "default") or (not default_set and db_name == list(db_configs.keys())[0])
        registry.register(db_name, backend, set_as_default=set_as_default)
        if set_as_default:
            default_set = True
    
    return registry


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

