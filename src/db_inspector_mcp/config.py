"""Configuration management for db-inspector-mcp."""

import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .backends.base import DatabaseBackend
from .backends.registry import BackendRegistry, get_registry
from .security import check_data_access_permission, get_permission_error_message


def _find_project_root() -> Path:
    """
    Find the project root by searching for .env file or common project markers.
    
    Resolution order:
    1. ``DB_MCP_PROJECT_DIR`` env-var (explicit override)
    2. Upward search from the current working directory
    3. Upward search from the installed package location
    4. Falls back to the current working directory
    
    When Cursor (or another IDE) launches the MCP server, it normally sets
    the working directory to the open workspace root — so the automatic
    search finds the project's ``.env`` file even when the server is
    configured at the *user* level.  ``DB_MCP_PROJECT_DIR`` is available as
    a fallback for environments where the working directory is not set to
    the project root.
    
    For each starting point the function walks up the directory tree looking
    for:
    * ``.env`` — primary indicator
    * ``.cursor/mcp.json`` — MCP configuration
    * ``pyproject.toml`` — Python project marker
    
    Returns:
        Path to project root, or current working directory if not found
    """
    import sys
    
    # 1. Explicit override via DB_MCP_PROJECT_DIR
    explicit_dir = os.getenv("DB_MCP_PROJECT_DIR")
    if explicit_dir:
        explicit_path = Path(explicit_dir).resolve()
        if explicit_path.is_dir():
            return explicit_path
        else:
            print(
                f"Warning: DB_MCP_PROJECT_DIR points to a non-existent directory: {explicit_dir}",
                file=sys.stderr,
            )
    
    # 2–3. Automatic search from CWD and package location
    search_roots = [Path.cwd().resolve()]
    
    try:
        package_dir = Path(__file__).parent.parent.parent.resolve()
        if (package_dir.parent / "pyproject.toml").exists():
            search_roots.append(package_dir.parent)
        search_roots.append(package_dir.parent)
    except Exception:
        pass
    
    for root in search_roots:
        current = root
        while current != current.parent:
            if (current / ".env").exists():
                return current
            if (current / ".cursor" / "mcp.json").exists():
                return current
            if (current / "pyproject.toml").exists():
                return current
            current = current.parent
    
    # 4. Fall back to current working directory
    return Path.cwd().resolve()


_env_loaded = False
_project_root: Path | None = None


def _get_project_root() -> Path:
    """Return the stored project root, falling back to discovery."""
    if _project_root is not None:
        return _project_root
    return _find_project_root()


def _load_env_files() -> None:
    """
    Load .env files from the project root.
    
    Searches for project root by looking for .env file or project markers,
    then loads:
    1. .env (base configuration)
    2. .env.local (local overrides, if exists)
    
    Environment variables already set (e.g., from MCP server env section) take precedence.
    
    Prints diagnostic messages to stderr so users can verify which files were
    loaded (visible in Cursor's MCP server output pane).
    """
    global _env_loaded, _project_root
    if _env_loaded:
        return
    _env_loaded = True

    import sys
    
    project_root = _find_project_root()
    _project_root = project_root
    cwd = Path.cwd().resolve()

    print(f"Working directory: {cwd}", file=sys.stderr)
    if os.getenv("DB_MCP_PROJECT_DIR"):
        print(f"DB_MCP_PROJECT_DIR: {os.getenv('DB_MCP_PROJECT_DIR')}", file=sys.stderr)
    print(f"Resolved project root: {project_root}", file=sys.stderr)
    
    # Load .env file if it exists
    env_path = project_root / ".env"
    if env_path.exists():
        result = load_dotenv(str(env_path), override=False)
        if result:
            print(f"Loaded .env from {env_path}", file=sys.stderr)
        else:
            print(
                f"Warning: .env file exists at {env_path} but no new variables were loaded "
                f"(they may already be set via mcp.json env section)",
                file=sys.stderr,
            )
    else:
        print(
            f"No .env file found at {env_path} — "
            f"if this is unexpected, set DB_MCP_PROJECT_DIR to your project path",
            file=sys.stderr,
        )
    
    # Load .env.local if it exists (takes precedence over .env)
    env_local_path = project_root / ".env.local"
    if env_local_path.exists():
        load_dotenv(str(env_local_path), override=True)
        print(f"Loaded .env.local from {env_local_path}", file=sys.stderr)


def _load_env_from_directory(directory: Path) -> bool:
    """Load .env and .env.local from an explicit directory.

    Returns True if at least one file was loaded.
    """
    global _project_root
    import sys

    _project_root = directory.resolve()
    loaded = False
    env_path = directory / ".env"
    if env_path.exists():
        result = load_dotenv(str(env_path), override=False)
        if result:
            print(f"Loaded .env from {env_path}", file=sys.stderr)
            loaded = True
        else:
            print(
                f"Warning: .env file exists at {env_path} but no new variables were loaded "
                f"(they may already be set via mcp.json env section)",
                file=sys.stderr,
            )

    env_local_path = directory / ".env.local"
    if env_local_path.exists():
        load_dotenv(str(env_local_path), override=True)
        print(f"Loaded .env.local from {env_local_path}", file=sys.stderr)
        loaded = True

    return loaded


def initialize_from_workspace(workspace_path: Path) -> "BackendRegistry":
    """Initialize backends from a workspace directory discovered via MCP roots.

    This is the *lazy-init* path used when the server starts without a
    ``.env`` file (e.g. user-level MCP configuration where the working
    directory is not the project root).

    Args:
        workspace_path: Absolute path to the workspace root provided by the
            MCP client (Cursor / VS Code).

    Returns:
        The populated ``BackendRegistry``.

    Raises:
        ValueError: If no database configuration is found in the workspace.
    """
    global _project_root
    import sys

    workspace_path = workspace_path.resolve()
    _project_root = workspace_path
    print(f"Lazy init: loading .env from workspace root {workspace_path}", file=sys.stderr)

    _load_env_from_directory(workspace_path)
    return initialize_backends()


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


def _get_access_conn_ttl() -> float | None:
    """Read the Access ODBC connection TTL from the environment.

    Returns:
        TTL in seconds, or None to use the backend default (5 s).
    """
    raw = os.getenv("DB_MCP_ACCESS_CONN_TTL")
    if raw is not None:
        try:
            return float(raw)
        except ValueError:
            pass
    return None


_ACCESS_BACKENDS = {"access_odbc", "access_com"}


def _resolve_connection_string_paths(
    connection_string: str, backend_type: str, base_dir: Path,
) -> str:
    """Resolve relative database file paths in a connection string.

    For Access backends, the ``DBQ=`` value (or a bare file path) is resolved
    against *base_dir* when it is not already absolute.  Other backend types
    are returned unchanged.
    """
    import sys

    if backend_type.lower() not in _ACCESS_BACKENDS:
        return connection_string

    dbq_match = re.search(r"DBQ\s*=\s*([^;]+)", connection_string, re.IGNORECASE)

    if dbq_match:
        db_path_str = dbq_match.group(1).strip()
        db_path = Path(db_path_str)
        if not db_path.is_absolute():
            resolved = (base_dir / db_path).resolve()
            connection_string = (
                connection_string[: dbq_match.start(1)]
                + str(resolved)
                + connection_string[dbq_match.end(1) :]
            )
            print(
                f"Resolved relative DBQ path: {db_path_str} -> {resolved}",
                file=sys.stderr,
            )
            if not resolved.exists():
                print(
                    f"Warning: resolved database path does not exist: {resolved}",
                    file=sys.stderr,
                )
    elif not re.search(r"Driver\s*=", connection_string, re.IGNORECASE):
        # Bare file path (no DBQ=, no Driver=)
        db_path = Path(connection_string.strip())
        if not db_path.is_absolute():
            resolved = (base_dir / db_path).resolve()
            print(
                f"Resolved relative database path: {connection_string.strip()} -> {resolved}",
                file=sys.stderr,
            )
            if not resolved.exists():
                print(
                    f"Warning: resolved database path does not exist: {resolved}",
                    file=sys.stderr,
                )
            connection_string = str(resolved)

    return connection_string


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
        from .backends.mssql import MSSQLBackend
        return MSSQLBackend(connection_string, query_timeout)
    elif backend_type == "postgres":
        from .backends.postgres import PostgresBackend
        return PostgresBackend(connection_string, query_timeout)
    elif backend_type == "access_odbc":
        from .backends.access_odbc import AccessODBCBackend
        conn_ttl = _get_access_conn_ttl()
        return AccessODBCBackend(connection_string, query_timeout, conn_ttl)
    elif backend_type == "access_com":
        from .backends.access_com import AccessCOMBackend
        conn_ttl = _get_access_conn_ttl()
        return AccessCOMBackend(connection_string, query_timeout, conn_ttl)
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
    
    connection_string = _resolve_connection_string_paths(
        connection_string, backend_name, _get_project_root(),
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
    base_dir = _get_project_root()
    default_set = False
    for db_name, db_config in db_configs.items():
        conn_str = _resolve_connection_string_paths(
            db_config["connection_string"], db_config["backend"], base_dir,
        )
        backend = _create_backend(
            db_config["backend"],
            conn_str,
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

