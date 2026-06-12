"""Configuration management for db-inspector-mcp."""

import os
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from .backends.base import DatabaseBackend
from .backends.registry import BackendRegistry, get_registry
from .security import check_data_access_permission, get_permission_error_message

# Per-tool-call workspace context (set by db_tool decorator).
_workspace_ctx: ContextVar[tuple[BackendRegistry, dict[str, str]] | None] = ContextVar(
    "workspace_ctx", default=None,
)


def current_registry() -> BackendRegistry:
    """Return the registry for the active workspace tool call."""
    ctx = _workspace_ctx.get()
    if ctx is not None:
        return ctx[0]
    return get_registry()


def current_env() -> dict[str, str]:
    """Return the env map for the active workspace tool call."""
    ctx = _workspace_ctx.get()
    if ctx is not None:
        return ctx[1]
    root = _find_project_root()
    return parse_workspace_env(root)


def set_workspace_context(
    registry: BackendRegistry, env_map: dict[str, str],
) -> object:
    """Set workspace context; returns token for reset."""
    return _workspace_ctx.set((registry, env_map))


def reset_workspace_context(token: object) -> None:
    """Restore previous workspace context."""
    _workspace_ctx.reset(token)


def _find_project_root() -> Path:
    """
    Find the project root by searching for .env file or common project markers.

    Resolution order:
    1. ``DB_MCP_PROJECT_DIR`` env-var (explicit override)
    2. Upward search from the current working directory
    3. Upward search from the installed package location
    4. Falls back to the current working directory
    """
    import sys

    explicit_dir = os.getenv("DB_MCP_PROJECT_DIR")
    if explicit_dir:
        explicit_path = Path(explicit_dir).resolve()
        if explicit_path.is_dir():
            return explicit_path
        print(
            f"Warning: DB_MCP_PROJECT_DIR points to a non-existent directory: {explicit_dir}",
            file=sys.stderr,
        )

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

    return Path.cwd().resolve()


def parse_workspace_env(directory: Path) -> dict[str, str]:
    """Parse workspace .env files into a dict without mutating os.environ.

    Layering: ``.env`` <- ``.env.local`` <- explicit ``DB_MCP_*`` from process
    env (preserves mcp.json env-section precedence).
    """
    directory = directory.resolve()
    env_map: dict[str, str] = {}

    env_path = directory / ".env"
    if env_path.exists():
        for key, value in dotenv_values(env_path).items():
            if key and value is not None:
                env_map[key] = value

    env_local_path = directory / ".env.local"
    if env_local_path.exists():
        for key, value in dotenv_values(env_local_path).items():
            if key and value is not None:
                env_map[key] = value

    for key, value in os.environ.items():
        if key.startswith("DB_MCP_"):
            env_map[key] = value

    return env_map


def record_env_mtimes(directory: Path) -> dict[str, float]:
    """Return mtimes for .env and .env.local in *directory*."""
    mtimes: dict[str, float] = {}
    for name in (".env", ".env.local"):
        path = directory / name
        if path.exists():
            try:
                mtimes[str(path.resolve())] = path.stat().st_mtime
            except OSError:
                pass
    return mtimes


def env_files_changed(directory: Path, stored_mtimes: dict[str, float]) -> bool:
    """Return True if any tracked .env file mtime differs from *stored_mtimes*."""
    if not stored_mtimes:
        return False
    current = record_env_mtimes(directory)
    for path_str, old_mtime in stored_mtimes.items():
        try:
            if Path(path_str).stat().st_mtime != old_mtime:
                return True
        except OSError:
            return True
    for path_str in current:
        if path_str not in stored_mtimes:
            return True
    return False


def config_from_env(env_map: dict[str, str]) -> dict[str, Any]:
    """Build configuration dict from a parsed workspace env map."""
    return {
        "DB_MCP_DATABASE": env_map.get("DB_MCP_DATABASE", "").lower(),
        "DB_MCP_CONNECTION_STRING": env_map.get("DB_MCP_CONNECTION_STRING", ""),
        "DB_MCP_QUERY_TIMEOUT_SECONDS": int(
            env_map.get("DB_MCP_QUERY_TIMEOUT_SECONDS", "30"),
        ),
        "DB_MCP_ALLOW_DATA_ACCESS": env_map.get("DB_MCP_ALLOW_DATA_ACCESS", "false"),
        "DB_MCP_VERIFY_READONLY": env_map.get("DB_MCP_VERIFY_READONLY", "true"),
        "DB_MCP_READONLY_FAIL_ON_WRITE": env_map.get(
            "DB_MCP_READONLY_FAIL_ON_WRITE", "false",
        ),
        "DB_MCP_ENABLE_LOGGING": (
            env_map.get("DB_MCP_ENABLE_LOGGING", "false").lower() == "true"
        ),
        "DB_MCP_LOG_DIR": env_map.get("DB_MCP_LOG_DIR", ""),
        "DB_MCP_LOG_MAX_SIZE_MB": int(env_map.get("DB_MCP_LOG_MAX_SIZE_MB", "10")),
        "DB_MCP_LOG_BACKUP_COUNT": int(env_map.get("DB_MCP_LOG_BACKUP_COUNT", "5")),
    }


def load_config() -> dict[str, Any]:
    """Load configuration from the discovered project root (eager/fallback path)."""
    root = _find_project_root()
    return config_from_env(parse_workspace_env(root))


def _get_access_conn_ttl(env_map: dict[str, str]) -> float | None:
    """Read the Access ODBC connection TTL from a workspace env map."""
    raw = env_map.get("DB_MCP_ACCESS_CONN_TTL")
    if raw is not None:
        try:
            return float(raw)
        except ValueError:
            pass
    return None


def _get_access_connect_timeout(env_map: dict[str, str]) -> float | None:
    """Read the Access ODBC connect timeout from a workspace env map."""
    raw = env_map.get("DB_MCP_ACCESS_CONNECT_TIMEOUT")
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
    """Resolve relative database file paths in a connection string."""
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


def _create_backend(
    backend_type: str,
    connection_string: str,
    query_timeout: int,
    env_map: dict[str, str],
) -> DatabaseBackend:
    """Create a backend instance based on type."""
    backend_type = backend_type.lower()
    conn_ttl = _get_access_conn_ttl(env_map)
    connect_timeout = _get_access_connect_timeout(env_map)

    if backend_type == "sqlserver":
        from .backends.mssql import MSSQLBackend
        return MSSQLBackend(connection_string, query_timeout)
    if backend_type == "postgres":
        from .backends.postgres import PostgresBackend
        return PostgresBackend(connection_string, query_timeout)
    if backend_type == "access_odbc":
        from .backends.access_odbc import AccessODBCBackend
        return AccessODBCBackend(
            connection_string, query_timeout, conn_ttl, connect_timeout,
        )
    if backend_type == "access_com":
        from .backends.access_com import AccessCOMBackend
        return AccessCOMBackend(connection_string, query_timeout, conn_ttl)
    raise ValueError(
        f"Unsupported backend: {backend_type}. "
        "Supported backends: sqlserver, postgres, access_odbc, access_com"
    )


def _collect_db_configs(env_map: dict[str, str]) -> dict[str, dict[str, str]]:
    """Collect database configurations from a workspace env map."""
    db_configs: dict[str, dict[str, str]] = {}

    database = env_map.get("DB_MCP_DATABASE", "").lower()
    connection_string = env_map.get("DB_MCP_CONNECTION_STRING", "")
    if database and connection_string:
        db_configs["default"] = {
            "backend": database,
            "connection_string": connection_string,
        }

    for key, value in env_map.items():
        if key.startswith("DB_MCP_") and key.endswith("_DATABASE"):
            name_part = key[7:-8].rstrip("_")
            if name_part:
                db_name = name_part.lower()
                conn_key = f"DB_MCP_{name_part}_CONNECTION_STRING"
                if conn_key in env_map:
                    db_configs[db_name] = {
                        "backend": value.lower(),
                        "connection_string": env_map[conn_key],
                    }

    return db_configs


def build_registry_from_env(
    env_map: dict[str, str],
    base_dir: Path,
    registry: BackendRegistry | None = None,
) -> BackendRegistry:
    """Build a BackendRegistry from a parsed workspace env map."""
    import sys

    if registry is None:
        registry = BackendRegistry()

    config = config_from_env(env_map)
    query_timeout = config["DB_MCP_QUERY_TIMEOUT_SECONDS"]
    db_configs = _collect_db_configs(env_map)

    if not db_configs:
        raise ValueError(
            "No database configuration found. "
            "Set DB_MCP_DATABASE/DB_MCP_CONNECTION_STRING for single database, "
            "or DB_MCP_<name>_DATABASE/DB_MCP_<name>_CONNECTION_STRING for multiple databases."
        )

    failures: list[str] = []
    for db_name, db_config in db_configs.items():
        try:
            conn_str = _resolve_connection_string_paths(
                db_config["connection_string"], db_config["backend"], base_dir,
            )
            backend = _create_backend(
                db_config["backend"], conn_str, query_timeout, env_map,
            )
        except Exception as exc:
            failures.append(f"{db_name} ({db_config['backend']}): {exc}")
            print(
                f"Warning: failed to configure backend '{db_name}': {exc}",
                file=sys.stderr,
            )
            continue
        registry.register(db_name, backend, set_as_default=(db_name == "default"))

    if not registry.list_backends():
        detail = "; ".join(failures) if failures else "Check DB_MCP_* configuration."
        raise ValueError(f"No database backends could be initialized. {detail}")

    return registry


def get_backend() -> DatabaseBackend:
    """Create and return a database backend based on configuration."""
    root = _find_project_root()
    env_map = parse_workspace_env(root)
    config = config_from_env(env_map)

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
        connection_string, backend_name, root,
    )
    return _create_backend(backend_name, connection_string, query_timeout, env_map)


def initialize_backends() -> BackendRegistry:
    """Initialize backends into the global registry (eager startup path)."""
    root = _find_project_root()
    env_map = parse_workspace_env(root)
    registry = get_registry()
    registry.clear()
    return build_registry_from_env(env_map, root, registry)


def check_data_access(tool_name: str, database: str | None = None) -> None:
    """Check if a tool has data access permission for the active workspace."""
    env_map = current_env()
    config = config_from_env(env_map)
    if database is None:
        registry = current_registry()
        database = registry.get_default_name()
    if not check_data_access_permission(tool_name, config, env_map, database=database):
        error_msg = get_permission_error_message(tool_name, database=database)
        raise PermissionError(error_msg)


def get_config() -> dict[str, Any]:
    """Get the current configuration (eager/fallback path)."""
    return load_config()
