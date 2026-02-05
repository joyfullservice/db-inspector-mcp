"""Configuration management for db-inspector-mcp."""

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .backends.access_com import AccessCOMBackend
from .backends.access_odbc import AccessODBCBackend
from .backends.base import DatabaseBackend
from .backends.mssql import MSSQLBackend
from .backends.postgres import PostgresBackend
from .backends.registry import BackendRegistry, get_registry
from .security import check_data_access_permission, get_permission_error_message


def _find_project_root() -> Path:
    """
    Find the project root by searching for .env file or common project markers.
    
    Searches upward from multiple starting points:
    1. Current working directory
    2. Location of this script (if available)
    
    For each starting point, searches upward for:
    1. .env file (primary indicator)
    2. .cursor/mcp.json (MCP configuration)
    3. pyproject.toml (Python project marker)
    
    Returns:
        Path to project root, or current working directory if not found
    """
    import sys
    
    # Try multiple starting points
    search_roots = [Path.cwd().resolve()]
    
    # Also try to find project root relative to this package's location
    try:
        # Get the directory containing this config.py file
        package_dir = Path(__file__).parent.parent.parent.resolve()
        # Walk up to find project root (should be parent of src/)
        if (package_dir.parent / "pyproject.toml").exists():
            search_roots.append(package_dir.parent)
        search_roots.append(package_dir.parent)
    except Exception:
        pass
    
    # Search from each starting point
    for root in search_roots:
        current = root
        # Walk up the directory tree
        while current != current.parent:
            # Check for .env file (primary indicator)
            if (current / ".env").exists():
                return current
            # Check for MCP config (secondary indicator)
            if (current / ".cursor" / "mcp.json").exists():
                return current
            # Check for Python project marker (tertiary indicator)
            if (current / "pyproject.toml").exists():
                return current
            
            current = current.parent
    
    # Fall back to current working directory
    return Path.cwd().resolve()


def _load_env_files() -> None:
    """
    Load .env files from the project root.
    
    Searches for project root by looking for .env file or project markers,
    then loads:
    1. .env (base configuration)
    2. .env.local (local overrides, if exists)
    
    Environment variables already set (e.g., from MCP server env section) take precedence.
    """
    import sys
    
    # Find project root (searches upward from current working directory)
    project_root = _find_project_root()
    
    # Load .env file if it exists
    env_path = project_root / ".env"
    if env_path.exists():
        # Convert Path to string for load_dotenv compatibility
        result = load_dotenv(str(env_path), override=False)
        if not result:
            print(f"Warning: .env file exists at {env_path} but no variables were loaded", file=sys.stderr)
    
    # Load .env.local if it exists (takes precedence over .env)
    env_local_path = project_root / ".env.local"
    if env_local_path.exists():
        # Convert Path to string for load_dotenv compatibility
        load_dotenv(str(env_local_path), override=True)


def load_config() -> dict[str, Any]:
    """
    Load configuration from environment variables.
    
    Automatically loads .env and .env.local files from the project root.
    Environment variables passed via MCP server env section take precedence.
    
    Returns:
        Dictionary with configuration values
    """
    # Load .env files first (if not already loaded)
    _load_env_files()
    
    return {
        "DB_MCP_DATABASE": os.getenv("DB_MCP_DATABASE", "").lower(),
        "DB_MCP_CONNECTION_STRING": os.getenv("DB_MCP_CONNECTION_STRING", ""),
        "DB_MCP_QUERY_TIMEOUT_SECONDS": int(os.getenv("DB_MCP_QUERY_TIMEOUT_SECONDS", "30")),
        "DB_MCP_ALLOW_DATA_ACCESS": os.getenv("DB_MCP_ALLOW_DATA_ACCESS", "false"),
        "DB_MCP_ALLOW_PREVIEW": os.getenv("DB_MCP_ALLOW_PREVIEW", "false"),
        "DB_MCP_VERIFY_READONLY": os.getenv("DB_MCP_VERIFY_READONLY", "true"),
        "DB_MCP_READONLY_FAIL_ON_WRITE": os.getenv("DB_MCP_READONLY_FAIL_ON_WRITE", "false"),
        # Logging configuration
        "DB_MCP_ENABLE_LOGGING": os.getenv("DB_MCP_ENABLE_LOGGING", "false").lower() == "true",
        "DB_MCP_LOG_DIR": os.getenv("DB_MCP_LOG_DIR", ""),
        "DB_MCP_LOG_MAX_SIZE_MB": int(os.getenv("DB_MCP_LOG_MAX_SIZE_MB", "10")),
        "DB_MCP_LOG_BACKUP_COUNT": int(os.getenv("DB_MCP_LOG_BACKUP_COUNT", "5")),
    }


def _create_backend(backend_type: str, connection_string: str, query_timeout: int) -> DatabaseBackend:
    """
    Create a backend instance based on type.
    
    Args:
        backend_type: Type of backend (sqlserver, postgres, access_odbc, access_com)
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
    elif backend_type == "access_odbc":
        return AccessODBCBackend(connection_string, query_timeout)
    elif backend_type == "access_com":
        return AccessCOMBackend(connection_string, query_timeout)
    else:
        raise ValueError(
            f"Unsupported backend: {backend_type}. "
            "Supported backends: sqlserver, postgres, access_odbc, access_com"
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
    
    backend_name = config["DB_MCP_DATABASE"]
    connection_string = config["DB_MCP_CONNECTION_STRING"]
    query_timeout = config["DB_MCP_QUERY_TIMEOUT_SECONDS"]
    
    if not backend_name:
        raise ValueError(
            "DB_MCP_DATABASE environment variable is required. "
            "Set DB_MCP_DATABASE=sqlserver, postgres, access_odbc, or access_com"
        )
    
    if not connection_string:
        raise ValueError(
            "DB_MCP_CONNECTION_STRING environment variable is required. "
            "Provide a valid database connection string."
        )
    
    return _create_backend(backend_name, connection_string, query_timeout)


def initialize_backends() -> BackendRegistry:
    """
    Initialize multiple database backends from environment variables.
    
    Supports two configuration patterns:
    1. Single database: DB_MCP_DATABASE, DB_MCP_CONNECTION_STRING (registered as "default")
    2. Multi-database: DB_MCP_<name>_DATABASE, DB_MCP_<name>_CONNECTION_STRING for each database
    
    Examples:
        # Single database
        DB_MCP_DATABASE=sqlserver
        DB_MCP_CONNECTION_STRING=...
        
        # Multiple databases
        DB_MCP_LEGACY_DATABASE=access
        DB_MCP_LEGACY_CONNECTION_STRING=...
        DB_MCP_NEW_DATABASE=sqlserver
        DB_MCP_NEW_CONNECTION_STRING=...
        
    Returns:
        BackendRegistry with all configured backends
        
    Raises:
        ValueError: If configuration is invalid
    """
    import sys
    
    registry = get_registry()
    config = load_config()
    query_timeout = config["DB_MCP_QUERY_TIMEOUT_SECONDS"]
    
    # Collect all database configurations
    db_configs: dict[str, dict[str, str]] = {}
    
    # Check for single-database configuration
    if config["DB_MCP_DATABASE"] and config["DB_MCP_CONNECTION_STRING"]:
        db_configs["default"] = {
            "backend": config["DB_MCP_DATABASE"],
            "connection_string": config["DB_MCP_CONNECTION_STRING"],
        }
    
    # Scan for multi-database configurations (DB_MCP_<name>_DATABASE pattern)
    env_vars = dict(os.environ)
    for key, value in env_vars.items():
        if key.startswith("DB_MCP_") and key.endswith("_DATABASE"):
            # Extract database name (e.g., "LEGACY" from "DB_MCP_LEGACY_DATABASE")
            # Remove "DB_MCP_" prefix (7 chars) and "_DATABASE" suffix (8 chars)
            # The name_part includes the trailing underscore, so we need to remove it
            name_part = key[7:-8].rstrip("_")  # Remove prefix, suffix, and trailing underscore
            if name_part:
                db_name = name_part.lower()
                conn_key = f"DB_MCP_{name_part}_CONNECTION_STRING"
                
                if conn_key in env_vars:
                    db_configs[db_name] = {
                        "backend": value.lower(),
                        "connection_string": env_vars[conn_key],
                    }
                else:
                    print(f"Warning: Found {key} but missing {conn_key}", file=sys.stderr)
    
    if not db_configs:
        raise ValueError(
            "No database configuration found. "
            "Set DB_MCP_DATABASE/DB_MCP_CONNECTION_STRING for single database, "
            "or DB_MCP_<name>_DATABASE/DB_MCP_<name>_CONNECTION_STRING for multiple databases."
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

